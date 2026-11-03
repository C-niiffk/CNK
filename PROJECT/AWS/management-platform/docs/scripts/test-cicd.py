#!/usr/bin/env python3
"""Offline checks for CI adapters and the original scripts they invoke. No AWS writes."""
import ast
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[2]
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'docs/scripts' / file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module
ci = load('cicd_adapter', 'cicd.py')
setup = load('one_time_setup', 'setup-cicd.py')
cleanup = load('lab_cleanup', 'cleanup.py')

class Selection(unittest.TestCase):
    def test_shared_sdk_rebuilds_java(self):
        self.assertEqual(set(ci.select(['application-plane/agent-client/src/main/java/X.java'])['images']), ci.JAVA)
    def test_management_is_independent(self):
        self.assertEqual(ci.select(['management-plane/api-manager/src/main/java/X.java'])['images'], ['management-server'])
    def test_yaml_only_uses_existing_images(self):
        result = ci.select(['gateway/deploy/k8s/deployment-gateway-a.yaml'])
        self.assertTrue(result['deploy']); self.assertEqual(result['images'], [])
    def test_terraform_is_separate(self):
        result = ci.select(['terraform/network.tf'])
        self.assertTrue(result['infra']); self.assertFalse(result['deploy'])
    def test_documentation_does_not_deploy(self):
        self.assertFalse(ci.select(['docs/deployment-cicd.md'])['deploy'])
    def test_rhel_host_config_needs_a_new_bundle(self):
        self.assertEqual(ci.select(['application-plane/agent-client/deploy/ec2/filebeat.yml'])['images'], ['application-service'])
    def test_es_bootstrap_needs_a_terraform_plan(self):
        self.assertTrue(ci.select(['management-plane/observability/deploy/ec2/es-user-data.sh'])['infra'])

    def test_first_push_includes_the_infrastructure(self):
        with patch.object(ci, 'run', return_value='terraform/network.tf\ngateway/src/Main.java\n'):
            result = ci.changes(base='0'*40)
        self.assertTrue(result['infra']); self.assertEqual(result['images'], ['gateway'])

    def test_manual_infrastructure_run_does_not_build_applications(self):
        result = ci.changes(target='infrastructure', build_all=True)
        self.assertTrue(result['infra']); self.assertFalse(result['deploy'])
        self.assertEqual(result['images'], [])

    def test_rollback_does_not_build_new_candidates(self):
        with patch.object(ci, 'run', side_effect=AssertionError('Rollback needs no source selection')):
            result = ci.changes(target='application', build_all=True, rollback='previous')
        self.assertTrue(result['deploy']); self.assertFalse(result['has_images'])

class UnifiedWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load((ROOT/'.github/workflows/management-platform.yml').read_text(), Loader=yaml.BaseLoader)

    def allowed(self, job, selection, event='push', results=None, rollback=''):
        jobs = self.workflow['jobs']; spec = jobs[job]; results = results or {}
        dependencies = spec.get('needs', [])
        if isinstance(dependencies, str): dependencies = [dependencies]
        expression = spec.get('if', 'True')
        if 'always()' not in expression and 'cancelled()' not in expression:
            if any(results.get(name, 'success') != 'success' for name in dependencies):
                return False
        context = {
            'github': SimpleNamespace(event_name=event, ref='refs/heads/main'),
            'inputs': SimpleNamespace(apply=True, rollback_release=rollback),
            'vars': SimpleNamespace(INFRA_ENABLED='true', INFRA_APPLY_ENABLED='true',
                                    PUBLISH_ENABLED='true', DEPLOY_ENABLED='true'),
            'needs': SimpleNamespace(**{name: SimpleNamespace(result=results.get(name, 'success'),
                outputs=SimpleNamespace(**{k: str(v).lower() if isinstance(v, bool) else v
                                           for k, v in (selection if name == 'changes' else {}).items()}))
                for name in jobs}),
            'always': lambda: True, 'cancelled': lambda: False,
        }
        expression = re.sub(r'!(?!=)', 'not ', expression).replace('&&', ' and ').replace('||', ' or ')
        return bool(eval(' '.join(expression.split()), {'__builtins__': {}}, context))

    def test_prs_never_reach_aws_jobs(self):
        selected = ci.select(['gateway/src/Main.java', 'terraform/network.tf'])
        for name in ['plan', 'apply', 'publish', 'deploy']:
            self.assertFalse(self.allowed(name, selected, event='pull_request'), name)

    def test_application_only_run_can_pass_skipped_infrastructure_jobs(self):
        selected = ci.select(['gateway/src/Main.java'])
        statuses = {'plan': 'skipped', 'apply': 'skipped'}
        self.assertFalse(self.allowed('plan', selected))
        self.assertTrue(self.allowed('publish', selected, results=statuses))
        self.assertTrue(self.allowed('deploy', selected, results=statuses))

    def test_infrastructure_only_run_does_not_publish_or_deploy_applications(self):
        selected = ci.select(['terraform/network.tf'])
        self.assertTrue(self.allowed('plan', selected))
        self.assertTrue(self.allowed('apply', selected))
        self.assertFalse(self.allowed('publish', selected))
        self.assertFalse(self.allowed('deploy', selected))

    def test_failed_or_unapproved_infrastructure_blocks_a_mixed_release(self):
        selected = ci.select(['gateway/src/Main.java', 'terraform/network.tf'])
        for result in ['failure', 'cancelled', 'skipped']:
            for name in ['publish', 'deploy']:
                self.assertFalse(self.allowed(name, selected, results={'apply': result}), (name, result))
        self.assertTrue(self.allowed('publish', selected))
        self.assertTrue(self.allowed('deploy', selected))

    def test_failed_ci_or_publication_blocks_deployment(self):
        selected = ci.select(['gateway/src/Main.java'])
        for name in ['checks', 'validate', 'publish', 'changes']:
            self.assertFalse(self.allowed('deploy', selected, results={name:'failure'}), name)

    def test_configuration_only_and_rollback_skip_publication(self):
        choices = [ci.select(['gateway/deploy/k8s/configmap.yaml']),
                   ci.changes(target='application', rollback='previous')]
        for selected in choices:
            statuses = {'plan':'skipped', 'apply':'skipped', 'publish':'skipped'}
            self.assertFalse(self.allowed('publish', selected, results=statuses, rollback='previous'))
            self.assertTrue(self.allowed('deploy', selected, results=statuses, rollback='previous'))

    def test_documentation_changes_do_not_release(self):
        selected = ci.select(['docs/deployment-cicd.md'])
        for name in ['plan', 'publish', 'deploy']:
            self.assertFalse(self.allowed(name, selected, results={'publish':'skipped'}), name)

    def test_workflow_shell_steps_are_syntax_valid(self):
        for name, job in self.workflow['jobs'].items():
            for step in job.get('steps', []):
                if 'run' in step:
                    subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)

    def test_public_ci_jobs_have_no_private_secret_inputs(self):
        self.assertNotIn('secrets.', json.dumps(self.workflow.get('env', {})))
        for name in ['changes', 'checks', 'validate']:
            self.assertNotIn('secrets.', json.dumps(self.workflow['jobs'][name]), name)

    def test_private_jobs_use_secrets_and_restrict_the_account(self):
        for name, role in [('plan','PLAN'),('apply','APPLY'),('publish','BUILD'),('deploy','DEPLOY')]:
            job = self.workflow['jobs'][name]
            self.assertEqual(job['env']['TF_VARS_JSON'], '${{ secrets.TF_VARS_JSON }}')
            auth = next(step['with'] for step in job['steps'] if step.get('uses','').startswith('aws-actions/configure-aws-credentials@'))
            self.assertEqual(auth['role-to-assume'], '${{ secrets.AWS_' + role + '_ROLE_ARN }}')
            self.assertEqual(auth['allowed-account-ids'], '${{ secrets.AWS_ACCOUNT_ID }}')
        source=json.dumps(self.workflow)
        for key in ['TF_STATE_BUCKET','TF_STATE_KEY','ARTIFACT_BUCKET','TF_VARS_JSON',
                    'AWS_PLAN_ROLE_ARN','AWS_APPLY_ROLE_ARN','AWS_BUILD_ROLE_ARN','AWS_DEPLOY_ROLE_ARN']:
            self.assertNotIn('vars.'+key, source)

class Privacy(unittest.TestCase):
    def test_masking_includes_json_fields_account_and_address_without_command_injection(self):
        output=io.StringIO()
        values={'allowed_cidrs':['198.51.100.7/32'],
                'operator_arn':'arn:aws:iam::111111111111:role/operator',
                'custom':'private\n::notice::not-a-command'}
        with patch.dict('os.environ',{'GITHUB_ACTIONS':'true','TF_VARS_JSON':json.dumps(values)},clear=True),redirect_stdout(output):
            ci.mask_inputs()
        lines=output.getvalue().splitlines()
        self.assertIn('::add-mask::198.51.100.7/32',lines)
        self.assertIn('::add-mask::198.51.100.7',lines)
        self.assertIn('::add-mask::111111111111',lines)
        self.assertTrue(all(line.startswith('::add-mask::') for line in lines))
        self.assertTrue(any('%0A::notice::' in line for line in lines))

    def test_local_operation_does_not_print_values_for_github_masking(self):
        output=io.StringIO()
        with patch.dict('os.environ',{'GITHUB_ACTIONS':'false','TF_VARS_JSON':'invalid'},clear=True),redirect_stdout(output):
            ci.mask_inputs()
        self.assertEqual(output.getvalue(),'')

    def test_private_logs_capture_both_streams_and_preserve_the_exit_code(self):
        for status in [0,7]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);output=io.StringIO();uploaded={}
                def fake_aws(*args):
                    path=Path(args[args.index('--body')+1])
                    uploaded.update(key=args[args.index('--key')+1],text=path.read_text(),mode=path.stat().st_mode & 0o777)
                    return {}
                command=[sys.executable,'-c',f"import sys; print('private-host'); print('private-error',file=sys.stderr); sys.exit({status})"]
                with patch.object(ci,'ROOT',root),patch.object(ci,'WORK',root),patch.object(ci,'run_id',return_value='101-1'), \
                     patch.dict('os.environ',{'ARTIFACT_BUCKET':'private-artifacts'}),patch.object(ci,'aws',side_effect=fake_aws),redirect_stdout(output):
                    if status:
                        with self.assertRaises(SystemExit) as error:
                            ci.run_private('terraform-apply',command)
                        self.assertEqual(error.exception.code,status)
                    else:ci.run_private('terraform-apply',command)
                self.assertIn('private-host',uploaded['text']);self.assertIn('private-error',uploaded['text'])
                self.assertNotIn('private-host',output.getvalue());self.assertNotIn('private-error',output.getvalue())
                self.assertEqual(uploaded['mode'],0o600)
                self.assertEqual(uploaded['key'],'plans/101-1/terraform-apply.log')

    def test_heredoc_deployment_stops_on_failure_and_does_not_print_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);scripts=root/'docs/scripts';scripts.mkdir(parents=True)
            for name in ['cicd.py','remote.py']:shutil.copy2(ROOT/'docs/scripts'/name,scripts/name)
            binary=root/'bin';binary.mkdir();aws=binary/'aws'
            aws.write_text('#!'+sys.executable+'\nprint("{}")\n');aws.chmod(0o700)
            import os
            env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ['PATH'],
                     GITHUB_RUN_ID='101',GITHUB_RUN_ATTEMPT='1',ARTIFACT_BUCKET='private-artifacts')
            env.pop('MP_RELEASE_ID',None)
            result=subprocess.run([sys.executable,str(scripts/'cicd.py'),'run-private','--label','deployment','--','bash','-e','-o','pipefail'],
                input='echo private-deployment\nfalse\necho must-not-run\n',text=True,capture_output=True,env=env,cwd=root)
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertNotIn('private-deployment',result.stdout)
            log=(root/'.ha-work/deployment.log').read_text()
            self.assertIn('private-deployment',log);self.assertNotIn('must-not-run',log)

class GitHubSecretSetup(unittest.TestCase):
    def fixture(self):
        state={'repo':'demo/platform','account':'111111111111','region':'us-east-1',
               'state_bucket':'state-bucket','state_key':'platform.tfstate','artifact_bucket':'private-artifacts',
               'roles':{name:'arn:aws:iam::111111111111:role/'+name for name in ['plan','apply','build','deploy']}}
        config={'allowed_cidrs':['198.51.100.7/32'],'operator_arn':'arn:operator'}
        private={'AWS_ACCOUNT_ID','TF_STATE_BUCKET','TF_STATE_KEY','ARTIFACT_BUCKET','TF_VARS_JSON',
                 'AWS_PLAN_ROLE_ARN','AWS_APPLY_ROLE_ARN','AWS_BUILD_ROLE_ARN','AWS_DEPLOY_ROLE_ARN'}
        previous={name:'legacy-value' for name in private}
        previous.update(PUBLISH_ENABLED='true',DEPLOY_ENABLED='false',UNRELATED='keep')
        def fake_gh(endpoint,**kwargs):
            if endpoint.startswith('users/'):return {'id':1}
            if endpoint.endswith('/deployment-branch-policies'):return {'branch_policies':[{'name':'main','type':'branch'}]}
            if endpoint.endswith('/protection'):
                return {'required_status_checks':{'contexts':['checks','validate']},'required_pull_request_reviews':{}}
            if endpoint.endswith('/actions/variables?per_page=100'):
                return {'variables':[{'name':k,'value':v} for k,v in previous.items()]}
            return {'deployment_branch_policy':{'custom_branch_policies':True},
                    'protection_rules':[{'type':'required_reviewers','reviewers':[{'id':1}]}]}
        return state,config,private,previous,fake_gh

    def test_private_values_are_secrets_and_legacy_variables_are_removed_after_success(self):
        state,config,private,previous,fake_gh=self.fixture();calls=[]
        def record(args,**kwargs):calls.append((args,kwargs));return ''
        with patch.object(setup,'gh',side_effect=fake_gh),patch.object(setup,'call',side_effect=record):
            setup.configure_github(state,config,'reviewer',True)
        secrets=[(i,args,kwargs) for i,(args,kwargs) in enumerate(calls) if args[:3]==['gh','secret','set']]
        self.assertEqual({args[3] for _,args,_ in secrets},private)
        for _,args,kwargs in secrets:
            self.assertIsInstance(kwargs['body'],str);self.assertNotIn(kwargs['body'],args)
        public=[(args,kwargs) for args,kwargs in calls if args[:3]==['gh','variable','set']]
        self.assertEqual({args[3] for args,_ in public},{'AWS_REGION','CODEBUILD_PROJECT','INFRA_ENABLED',
                                                      'INFRA_APPLY_ENABLED','PUBLISH_ENABLED','DEPLOY_ENABLED'})
        self.assertEqual(next(kwargs['body'] for args,kwargs in public if args[3]=='DEPLOY_ENABLED'),'false')
        deletes=[(i,args[3]) for i,(args,_) in enumerate(calls) if args[:3]==['gh','variable','delete']]
        self.assertEqual({name for _,name in deletes},private)
        self.assertGreater(min(i for i,_ in deletes),max(i for i,_,_ in secrets))

    def test_failed_secret_write_keeps_all_legacy_variables(self):
        state,config,_,_,fake_gh=self.fixture();calls=[]
        def fail(args,**kwargs):
            calls.append(args)
            if args[:4]==['gh','secret','set','TF_VARS_JSON']:raise setup.SetupError('secret write failed')
            return ''
        with patch.object(setup,'gh',side_effect=fake_gh),patch.object(setup,'call',side_effect=fail):
            with self.assertRaisesRegex(setup.SetupError,'secret write failed'):
                setup.configure_github(state,config,'reviewer',True)
        self.assertFalse(any(args[:3]==['gh','variable','delete'] for args in calls))

    def test_write_only_secrets_do_not_trigger_new_environment_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(setup,'TF',root),patch.object(setup,'CONFIG',root/'variables.json'), \
                 patch.object(setup,'gh',return_value={'variables':[{'name':'AWS_REGION','value':'us-east-1'}]}), \
                 patch.object(setup,'new_config',side_effect=AssertionError('Existing environment must not get new defaults')):
                with self.assertRaisesRegex(setup.SetupError,'Secrets cannot be downloaded'):
                    setup.select_inputs(SimpleNamespace(vars=None,new_environment=False),
                                        {'repo':'demo/platform','region':'us-east-1'}, {})

class ExistingScripts(unittest.TestCase):
    def test_ci_image_check_does_not_authenticate_or_push(self):
        with patch.object(ci, 'run') as run, \
             patch.object(ci, 'aws', side_effect=AssertionError('No AWS calls allowed')):
            ci.check_images(['gateway'])
        command = run.call_args.args[0]
        self.assertIn('--load', command); self.assertNotIn('--push', command)
        self.assertEqual(command[0], 'docker')

    def test_ci_image_paths_match_the_unchanged_manual_builder(self):
        source = ast.parse((ROOT / 'docs/scripts/build-images.py').read_text())
        files = next(node.value for node in source.body if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'files' for t in node.targets))
        self.assertEqual(ci.DOCKERFILES, ast.literal_eval(files))

    def test_existing_only_secrets_never_generate_or_write_aws_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / '.ha-work'; work.mkdir()
            (root / 'docs/scripts').mkdir(parents=True)
            shutil.copy2(ROOT / 'docs/scripts/deploy.py', root / 'docs/scripts/deploy.py')
            meta = {'region': 'us-east-1', 'vpc_cidr': '10.70.0.0/16', 'app2': {'a': {}, 'b': {}},
                    'runtime_secret_arn': 'runtime', 'redis_secret_arn': 'redis'}
            (work / 'deployment.json').write_text(json.dumps(meta))
            keys = ['MGMT_DB_PASSWORD','APP2_DB_PASSWORD','GRDC_A_DB_PASSWORD','GRDC_B_DB_PASSWORD',
                    'NACOS_PASSWORD','ADMIN_PASSWORD','NACOS_IDENTITY_VALUE','JWT_SECRET','NACOS_AUTH_TOKEN']
            values = {k: 'Aa1' + 'a'*24 for k in keys}
            calls = []
            def fake_run(args, **kwargs):
                calls.append(args)
                if args[0] == 'aws':
                    self.assertNotIn('put-secret-value', args)
                    secret = 'redis-password' if args[-1] == 'redis' else json.dumps(values)
                    return SimpleNamespace(stdout=json.dumps({'SecretString': secret}))
                return SimpleNamespace(stdout='')
            with patch.object(sys, 'argv', ['deploy.py', 'secrets', '--existing-only']), \
                 patch('subprocess.run', side_effect=fake_run), \
                 patch('secrets.token_hex', side_effect=AssertionError('No generation allowed')), \
                 patch('secrets.token_bytes', side_effect=AssertionError('No generation allowed')):
                with self.assertRaises(SystemExit) as exited:
                    runpy.run_path(str(root / 'docs/scripts/deploy.py'), run_name='__main__')
            self.assertIsNone(exited.exception.code)
            self.assertEqual(json.loads((work / 'runtime.json').read_text())['ADMIN_PASSWORD'], values['ADMIN_PASSWORD'])
            self.assertTrue(any(x[0] == 'kubectl' for x in calls))

    def test_remote_script_is_shell_valid_and_reuses_installer(self):
        script = ci.ssm_script('artifact-bucket', 'bundles/test', 'a'*64,
                                 {'region':'us-east-1','runtime_secret_arn':'arn:runtime'}, 'a', ['--reuse-release'])
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn('docs/scripts/install-app2.sh', script)
        self.assertIn('get-secret-value', script)
        self.assertIn('sha256sum -c', script)
        self.assertNotIn('systemctl stop', script)  # Application lifecycle remains in the original installer.

    def test_ssm_failure_stops_deployment(self):
        with patch.object(ci, 'aws', side_effect=[{'Command':{'CommandId':'test'}},
             {'Status':'Failed','StandardErrorContent':'installer failed'}]):
            with self.assertRaisesRegex(RuntimeError, 'installer failed'):
                ci.run_command('i-test', ['false'])

    def test_ssm_success_returns_output(self):
        with patch.object(ci, 'aws', side_effect=[{'Command':{'CommandId':'test'}},
             {'Status':'Success','StandardOutputContent':'ready'}]):
            self.assertEqual(ci.run_command('i-test', ['true']), 'ready')

    def test_failed_first_vm_does_not_advance_the_second_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'deployment.json').write_text(json.dumps({'region':'us-east-1',
                'app2':{'a':{'instance_id':'i-a'},'b':{'instance_id':'i-b'}}}))
            bundle = root/'bundle.tgz'; bundle.write_bytes(b'package')
            with patch.object(ci,'WORK',root),patch.dict('os.environ',{'ARTIFACT_BUCKET':'artifacts'}), \
                 patch.object(ci,'aws'),patch.object(ci,'wait_target'), \
                 patch.object(ci,'ssm_script',return_value='installer'), \
                 patch.object(ci,'run_command',side_effect=RuntimeError('install failed')) as deploy:
                with self.assertRaisesRegex(RuntimeError,'install failed'):
                    ci.deploy_vm(bundle)
            self.assertEqual([call.args[0] for call in deploy.call_args_list],['i-a'])

class Releases(unittest.TestCase):
    def test_manual_release_ids_are_distinct_from_github_runs(self):
        with patch.dict('os.environ',{'MP_RELEASE_ID':'manual-'+'a'*32,'MP_SOURCE_COMMIT':'manual-commit'}):
            self.assertEqual(ci.run_id(),'manual-'+'a'*32)
            self.assertEqual(ci.source_commit(),'manual-commit')
        with patch.dict('os.environ',{'MP_RELEASE_ID':'../../another-prefix'}):
            with self.assertRaisesRegex(ValueError,'Invalid manual release ID'):
                ci.run_id()
    def test_partial_release_preserves_other_image_digests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ci, 'WORK', Path(directory)), \
             patch.dict('os.environ', {'ARTIFACT_BUCKET':'artifacts','GITHUB_RUN_ID':'100',
                                      'GITHUB_RUN_ATTEMPT':'1','GITHUB_SHA':'commit'}):
            ecr = {k: 'registry/test/' + k for k in ci.IMAGES}
            current = {'id':'99-1','images':{k:v+'@sha256:'+'a'*64 for k,v in ecr.items()},
                       'app2_bundle':{'key':'candidates/old/app2.tgz','sha256':'a'*64}}
            candidate = {'component':'management-server','commit':'commit',
                         'image':ecr['management-server']+'@sha256:'+'b'*64}
            with patch.object(ci, 'run', return_value=json.dumps({'ecr':ecr})), \
                 patch.object(ci, 'aws', return_value={'Contents':[{'Key':'releases/current.json'}]}), \
                 patch.object(ci, 's3_json', side_effect=[current,candidate]) as storage:
                ci.resolve(['management-server'])
            staged = json.loads((Path(directory)/'release.json').read_text())
            self.assertFalse(staged['deploy_app2'])
            self.assertEqual(staged['release']['images']['gateway'], current['images']['gateway'])
            self.assertEqual(staged['release']['images']['management-server'], candidate['image'])
            self.assertTrue(all(len(call.args) == 1 for call in storage.call_args_list))

class ManifestAndSyntax(unittest.TestCase):
    def test_yaml_python_and_shell_sources(self):
        for path in (ROOT/'docs/scripts').glob('*.py'): ast.parse(path.read_text(), filename=str(path))
        for path in ROOT.rglob('*.yaml'):
            if '/deploy/' in str(path): list(yaml.safe_load_all(path.read_text()))
        for path in (ROOT/'docs/scripts').glob('*.sh'): subprocess.run(['bash','-n',str(path)],check=True)

    def test_original_renderer_preserves_digests_and_sequential_rollout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'docs/scripts').mkdir(parents=True)
            shutil.copy2(ROOT/'docs/scripts/deploy.py',root/'docs/scripts/deploy.py')
            placeholders = set()
            for path in ROOT.rglob('*.yaml'):
                if '/deploy/k8s/' not in str(path): continue
                dest=root/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
                placeholders.update(re.findall(r'@@([A-Z0-9_]+)@@',path.read_text()))
            meta={'region':'us-east-1','vpc_id':'vpc-test','vpc_cidr':'10.70.0.0/16',
                  'db':{'management':{'jdbc_url':'jdbc:oracle:thin:@//db:1521/MPDB'}},
                  'redis_host':'redis.test','internal_alb':'internal.test',
                  'pod_roles':{'routing-controller':'arn:aws:iam::123456789012:role/routing'},
                  'routing_lock_table':'routing','es_hosts':['10.70.10.20','10.70.11.20'],
                  'app2':{s:{'private_ip':'10.70.10.30'} for s in ['a','b']},
                  'rules':{s:'arn:rule:'+s for s in ['management','app1','app2']},
                  'ecr':{k:'registry/test/'+k for k in ci.IMAGES}}
            for prefix,key in [('TG_','http_targets'),('TCP_','tcp_targets'),('DISC_','discovery_targets')]:
                meta[key]={x[len(prefix):].lower().replace('_','-'):'arn:target:'+x for x in placeholders if x.startswith(prefix)}
            work=root/'.ha-work';work.mkdir();(work/'deployment.json').write_text(json.dumps(meta))
            refs={k:v+'@sha256:'+'a'*64 for k,v in meta['ecr'].items()};(work/'image-refs.json').write_text(json.dumps(refs))
            commands=[]
            def fake_run(args, **kwargs):
                commands.append((args,kwargs.get('input')));return SimpleNamespace(stdout='')
            with patch.object(sys,'argv',['deploy.py','apply']),patch('subprocess.run',side_effect=fake_run):
                runpy.run_path(str(root/'docs/scripts/deploy.py'),run_name='__main__')
            for path in (work/'rendered').rglob('*.yaml'): self.assertNotIn('@@',path.read_text())
            count=0
            for index,(args,body) in enumerate(commands):
                if args[:2]==['kubectl','apply'] and body:
                    obj=json.loads(body)
                    if obj['kind'] in ['Deployment','DaemonSet']:
                        self.assertIn('rollout',commands[index+1][0]);count+=1
                        for container in obj['spec']['template']['spec']['containers']:
                            if container['image'].startswith('registry/test/'):
                                self.assertIn(container['image'],refs.values())
            self.assertGreater(count,20)

class CleanupAdapters(unittest.TestCase):
    def empty(self, confirmed='artifacts', backend='state', actual_account='111111111111', errors=False):
        self.deletions = []
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);work=root/'.ha-work';work.mkdir()
            local=root/'terraform/.terraform';local.mkdir(parents=True)
            (local/'terraform.tfstate').write_text(json.dumps({'backend':{'config':{'bucket':backend}}}))
            (work/'deployment.json').write_text('{"region":"us-east-1"}')
            rows={'Versions':[{'Key':'candidates/image','VersionId':str(i)} for i in range(1001)],
                  'DeleteMarkers':[{'Key':'releases/current.json','VersionId':'marker'}]}
            lists=0
            def fake_aws(*args):
                nonlocal lists
                if args[0]=='sts':return {'Account':actual_account}
                self.assertIn('--expected-bucket-owner',args)
                if args[1]=='list-object-versions':
                    lists+=1;return rows if lists==1 else {}
                self.assertEqual(args[1],'delete-objects')
                payload=Path(args[args.index('--delete')+1].removeprefix('file://'))
                self.deletions.append(json.loads(payload.read_text()))
                return {'Errors':[{'Code':'AccessDenied'}]} if errors else {}
            with patch.object(ci,'ROOT',root),patch.object(ci,'WORK',work), \
                 patch.dict('os.environ',{'MP_EXPECTED_ACCOUNT_ID':'111111111111','AWS_REGION':'us-east-1'}), \
                 patch.object(ci,'aws',side_effect=fake_aws), \
                 patch.object(ci,'run',return_value='{"artifact_bucket":"artifacts"}'):
                ci.empty_artifacts(confirmed)
            self.assertFalse((work/'delete-artifact-versions.json').exists())

    def test_versioned_artifacts_include_delete_markers_and_use_bounded_batches(self):
        self.empty()
        self.assertEqual([len(item['Objects']) for item in self.deletions],[1000,2])
        self.assertIn({'Key':'releases/current.json','VersionId':'marker'},self.deletions[-1]['Objects'])

    def test_artifact_cleanup_cannot_target_the_state_or_an_unconfirmed_bucket(self):
        for options in [{'confirmed':'another-bucket'},{'backend':'artifacts'}]:
            with self.subTest(options=options),self.assertRaisesRegex(ValueError,'never the Terraform state'):
                self.empty(**options)
            self.assertEqual(self.deletions,[])

    def test_artifact_cleanup_rejects_a_different_account(self):
        with self.assertRaisesRegex(ValueError,'cleanup account'):
            self.empty(actual_account='222222222222')
        self.assertEqual(self.deletions,[])

    def test_per_object_s3_failure_stops_artifact_cleanup(self):
        with self.assertRaisesRegex(RuntimeError,'AccessDenied'):
            self.empty(errors=True)
        self.assertEqual(len(self.deletions),1)

    def test_prepare_records_the_actual_application_key(self):
        meta={'name':'management-platform-lab','region':'us-east-1',
              'es_instances':{'a':'i-es'},'app2':{'a':{'instance_id':'i-app'}}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);tf=root/'terraform';(tf/'.terraform').mkdir(parents=True)
            (tf/'.terraform/terraform.tfstate').write_text(json.dumps({'backend':{'type':'s3','config':{
                'bucket':'state','key':'management-platform/lab/terraform.tfstate','region':'us-east-1'}}}))
            def fake_tf(*args,**kwargs):return 'default' if args[0]=='workspace' else '{}'
            instances={'Reservations':[{'Instances':[
                {'InstanceId':'i-app','KeyName':'ci-setup','BlockDeviceMappings':[]},
                {'InstanceId':'i-es','KeyName':'unrelated-es-key','BlockDeviceMappings':[]}]}]}
            with patch.object(cleanup,'TF',tf),patch.object(cleanup,'WORK',root), \
                 patch.object(cleanup,'CONTEXT',root/'context.json'),patch.object(cleanup,'INVENTORY',root/'inventory.json'), \
                 patch.dict('os.environ',{'MP_EXPECTED_ACCOUNT_ID':'111111111111'}), \
                 patch.object(cleanup,'check_identity',return_value={'Account':'111111111111'}), \
                 patch.object(cleanup,'tf',side_effect=fake_tf),patch.object(cleanup,'aws',return_value=instances), \
                 patch.object(cleanup,'run',return_value='{"items":[]}'):
                cleanup.prepare(meta,SimpleNamespace())
            self.assertEqual(json.loads((root/'inventory.json').read_text())['key_pairs'],['ci-setup'])

    def test_nonlegacy_key_requires_confirmation_and_deletes_only_the_inventory_key(self):
        inventory={'disks':[],'pvs':[],'key_pairs':['ci-setup']}
        with patch.object(cleanup,'load',return_value=inventory),patch.object(cleanup,'cleanup_rds_residuals') as rds, \
             patch.object(cleanup,'project_log_groups',return_value=[]),patch.object(cleanup,'aws_optional') as aws:
            with self.assertRaisesRegex(RuntimeError,'confirm-key-pair'):
                cleanup.extras({})
            rds.assert_not_called();aws.assert_not_called()
            cleanup.extras({},confirmed_key_pair='ci-setup')
            aws.assert_called_once_with('ec2','delete-key-pair','--key-name','ci-setup')

    def test_cleanup_verification_checks_the_inventory_key(self):
        meta={'name':'management-platform-lab','project':'management-platform','environment':'lab',
              'cluster_name':'cluster','app2':{},'es_instances':{},'ecr':{}}
        with patch.object(cleanup,'tf',return_value='{}'), \
             patch.object(cleanup,'load',return_value={'disks':[],'pvs':[],'key_pairs':['ci-setup']}), \
             patch.object(cleanup,'aws',return_value={}),patch.object(cleanup,'aws_optional',return_value=None) as aws:
            cleanup.verify(meta)
        keys=[call.args[-1] for call in aws.call_args_list if call.args[:2]==('ec2','describe-key-pairs')]
        self.assertEqual(keys,['ci-setup'])

class OneTimeSetup(unittest.TestCase):
    def test_new_environment_initialization_uses_the_ci_ssm_helper(self):
        deployment={'cluster_name':'cluster','app2':{'a':{'instance_id':'i-a'},'b':{'instance_id':'i-b'}},
                    'es_instances':{'a':'i-es-a','b':'i-es-b'},'db':{'management':{'secret_arn':'database'}}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);key=root/'setup.pem';key.write_text('test')
            state={'repo':'demo/platform','artifact_bucket':'artifacts','region':'us-east-1','new_environment':True}
            def fake_call(args,**kwargs):
                if args[:4]==['terraform','-chdir=terraform','output','-json']:
                    return json.dumps(deployment if args[4]=='deployment' else {'artifact_bucket':'artifacts'})
                return ''
            with patch.object(setup,'context',return_value=(state,{})),patch.object(setup,'prepare_local'), \
                 patch.object(setup,'require_tools'),patch.object(setup,'wait_ssm'), \
                 patch.object(setup,'HA',root),patch.object(setup,'SETTINGS',root/'setup.json'), \
                 patch.object(setup,'call',side_effect=fake_call) as calls, \
                 patch.dict(sys.modules,{'cicd':ci}),patch.object(ci,'run_command') as ssm:
                setup.initialize(SimpleNamespace(key=str(key)))
            self.assertEqual(ssm.call_count,6)
            self.assertTrue(state['initialized'])
            self.assertTrue(any('docs/scripts/init-db.py' in call.args[0] for call in calls.call_args_list))

    def test_release_dispatches_the_application_path_in_the_single_workflow(self):
        state = {'repo':'demo/platform','artifact_bucket':'artifacts','initialized':True}
        with patch.object(setup,'context',return_value=(state,{})), \
             patch.object(setup,'aws',return_value={}),patch.object(setup,'call') as call:
            setup.release(SimpleNamespace(first=False))
        args = call.call_args.args[0]
        self.assertEqual(args[:4],['gh','workflow','run','management-platform.yml'])
        self.assertIn('target=application',args)

    def test_plan_review_finds_the_active_infrastructure_run_in_a_combined_queue(self):
        state={'repo':'demo/platform','artifact_bucket':'artifacts'}
        queued={'id':3,'run_attempt':1,'status':'queued','head_branch':'main','workflow_id':42}
        active={'id':2,'run_attempt':1,'status':'in_progress','head_branch':'main','workflow_id':42,
                'head_sha':'commit','html_url':'https://github.com/demo/platform/actions/runs/2'}
        def fake_gh(endpoint,**kwargs):
            if endpoint.endswith('/runs?branch=main&per_page=20'):return {'workflow_runs':[queued,active]}
            if endpoint.endswith('/3/attempts/1/jobs?per_page=100'):return {'jobs':[]}
            if endpoint.endswith('/2/attempts/1/jobs?per_page=100'):
                return {'jobs':[{'name':'plan','conclusion':'success'}]}
            return {'id':42}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def fake_call(args,**kwargs):
                if args[:3]==['aws','s3','cp']:
                    Path(args[4]).write_text('{}')
                return ''
            with patch.object(setup,'context',return_value=(state,{})),patch.object(setup,'prepare_local'), \
                 patch.object(setup,'WORK',root),patch.object(setup,'TF',root), \
                 patch.object(setup,'gh',side_effect=fake_gh),patch.object(setup,'call',side_effect=fake_call) as calls, \
                 patch.object(setup,'check_plan_metadata'):
                setup.review_plan(SimpleNamespace(run=None))
            copies=[call.args[0][3] for call in calls.call_args_list if call.args[0][:3]==['aws','s3','cp']]
            self.assertTrue(all('/plans/2-1/' in value for value in copies))

    def test_manual_deployment_promotes_only_after_the_health_gate(self):
        for fail in [False,True]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory, patch.dict('os.environ',{}):
                root=Path(directory);commands=[]
                state={'repo':'demo/platform','artifact_bucket':'artifacts','initialized':True,'region':'us-east-1'}
                def fake_call(args,**kwargs):
                    commands.append(args)
                    if args[:4]==['terraform','-chdir=terraform','output','-json']:
                        return json.dumps({'cluster_name':'cluster'})
                    if args[:2]==['git','rev-parse']:return 'a'*40
                    if 'resolve' in args:(root/'release.json').write_text(json.dumps({'deploy_app2':True}))
                    if args[:3]==['python3','docs/scripts/cicd.py','verify'] and fail:
                        raise setup.SetupError('health gate failed')
                    return ''
                with patch.object(setup,'HA',root),patch.object(setup,'context',return_value=(state,{})), \
                     patch.object(setup,'require_tools'),patch.object(setup,'prepare_local'), \
                     patch.object(setup,'gh',return_value={'workflow_runs':[]}),patch.object(setup,'aws',return_value={}), \
                     patch.object(setup,'call',side_effect=fake_call):
                    if fail:
                        with self.assertRaisesRegex(setup.SetupError,'health gate failed'):
                            setup.manual_release(SimpleNamespace(build=False,release='previous',initialize_app2=False))
                    else:
                        setup.manual_release(SimpleNamespace(build=False,release='previous',initialize_app2=False))
                promote=[i for i,c in enumerate(commands) if c[:3]==['python3','docs/scripts/cicd.py','promote']]
                self.assertEqual(bool(promote),not fail)
                if promote:
                    verify=next(i for i,c in enumerate(commands) if c[:3]==['python3','docs/scripts/cicd.py','verify'])
                    self.assertGreater(promote[0],verify)
                self.assertTrue(any(c[:4]==['gh','variable','set','DEPLOY_ENABLED'] for c in commands))

    def test_pending_application_runs_block_a_manual_deployment(self):
        state={'repo':'demo/platform','initialized':True}
        with patch.object(setup,'context',return_value=(state,{})),patch.object(setup,'require_tools'), \
             patch.object(setup,'gh',return_value={'workflow_runs':[{'status':'waiting'}]}), \
             patch.object(setup,'call') as calls,patch.object(setup,'prepare_local') as prepare:
            with self.assertRaisesRegex(setup.SetupError,'pending main deployment runs'):
                setup.manual_release(SimpleNamespace(build=False,release='previous',initialize_app2=False))
            prepare.assert_not_called()
            self.assertEqual(calls.call_count,1)
    def test_existing_github_settings_can_restore_the_authoritative_backend(self):
        config={'region':'us-east-1','allowed_cidrs':['198.51.100.7/32'],
                'operator_arn':'arn:operator','oracle_engine_version':'19.existing','es_ami_id':'ami-existing'}
        variables={'TF_VARS_JSON':json.dumps(config),'TF_STATE_BUCKET':'original-state',
                   'TF_STATE_KEY':'original/platform.tfstate'}
        with tempfile.TemporaryDirectory() as directory,patch.object(setup,'TF',Path(directory)), \
             patch.object(setup,'CONFIG',Path(directory)/'variables.json'), \
             patch.object(setup,'gh',return_value={'variables':[{'name':k,'value':v} for k,v in variables.items()]}):
            state={'repo':'demo/platform','region':'us-east-1'}
            self.assertEqual(setup.select_inputs(SimpleNamespace(vars=None,new_environment=False),state,{}),config)
            self.assertEqual(state['state_bucket'],'original-state')
            self.assertEqual(state['state_key'],'original/platform.tfstate')

    def test_existing_setup_cannot_be_reclassified_as_a_new_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'variables.json';config.write_text('{}')
            with patch.object(setup,'TF',root),patch.object(setup,'CONFIG',config):
                with self.assertRaisesRegex(setup.SetupError,'existing backend'):
                    setup.select_inputs(SimpleNamespace(vars=None,new_environment=True),
                                        {'configured':True,'region':'us-east-1'}, {})

    def test_existing_inputs_are_preserved_without_new_lab_discovery(self):
        original = {'region':'us-east-1','allowed_cidrs':['198.51.100.7/32'],
                    'operator_arn':'arn:operator','oracle_engine_version':'19.existing',
                    'es_ami_id':'ami-existing','db_instance_class':'db.m6i.large',
                    'protect_data':True,'custom_setting':'retain-me'}
        args = SimpleNamespace(vars=None, new_environment=False)
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'variables.json';config.write_text(json.dumps(original))
            with patch.object(setup,'CONFIG',config), patch.object(setup,'TF',Path(directory)), \
                 patch.object(setup,'new_config',side_effect=AssertionError('No new deployment defaults')):
                actual=setup.select_inputs(args,{'region':'us-east-1'}, {})
            self.assertEqual(actual,original)

    def test_setup_rejects_a_different_aws_account(self):
        state={'account':'111111111111','region':'us-east-1','repo':'demo/platform','configured':True}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'setup.json';path.write_text(json.dumps(state))
            with patch.object(setup,'SETTINGS',path), patch.object(setup,'require_tools'), \
                 patch.object(setup,'call',return_value='{"terraform_version":"1.13.5"}'), \
                 patch.object(setup,'aws',return_value={'Account':'222222222222','Arn':'arn:operator'}):
                with self.assertRaisesRegex(setup.SetupError,'another account'):
                    setup.context(SimpleNamespace(region=None,repo=None),configured=True)

    def test_a_missing_existing_state_key_is_not_initialized_as_new(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup,'TF',Path(directory)), \
             patch.object(setup,'aws',side_effect=[{},None]) as calls:
            state={'state_bucket':'existing-state','state_key':'platform.tfstate','region':'us-east-1',
                   'account':'111111111111'}
            with self.assertRaisesRegex(setup.SetupError,'existing platform state key is absent'):
                setup.state_backend(SimpleNamespace(state_bucket=None,state_key=None),state,{})
            self.assertFalse((Path(directory)/'backend.hcl').exists())
            self.assertFalse(any('create-bucket' in x.args for x in calls.call_args_list))

    def test_oidc_claim_uses_the_exact_repository_and_environment_context(self):
        metadata={'created_at':'2026-10-01T00:00:00Z','owner':{'login':'demo','id':123},
                  'name':'platform','id':456}
        self.assertEqual(setup.oidc_repository_claim('demo/platform',metadata,{'use_default':True}),
                         'demo@123/platform@456')
        self.assertEqual(setup.oidc_repository_claim('demo/platform',metadata,{},'legacy'),'demo/platform')
        with self.assertRaisesRegex(setup.SetupError,'Custom OIDC'):
            setup.oidc_repository_claim('demo/platform',metadata,
                {'use_default':False,'include_claim_keys':['repository_id']})

    def test_bootstrap_does_not_switch_a_managed_provider_to_external(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state={'region':'us-east-1','repo':'demo/platform',
                'state_bucket':'state','state_key':'platform.tfstate','existing_github_oidc_arn':''}
            def fake_call(args,**kwargs):
                if 'show' in args:return json.dumps({'resource_changes':[]})
                if 'output' in args:return json.dumps({'artifact_bucket':'artifacts','roles':{'deploy':'arn:role'}})
                return ''
            with patch.object(setup,'TF',root),patch.object(setup,'WORK',root), \
                 patch.object(setup,'CONFIG',root/'variables.json'),patch.object(setup,'SETTINGS',root/'setup.json'), \
                 patch.object(setup,'call',side_effect=fake_call),patch.object(setup,'read_state_object',return_value={}), \
                 patch.object(setup,'aws',side_effect=AssertionError('Do not rediscover the provider')):
                config={};setup.bootstrap(state,config,'demo/platform')
            self.assertEqual(config['cicd_existing_github_oidc_arn'],'')
            self.assertEqual(config['cicd_state_key'],'platform.tfstate')
            self.assertFalse((root/'bootstrap-cicd').exists())

    def test_bootstrap_refuses_a_destructive_plan(self):
        with self.assertRaisesRegex(setup.SetupError,'deletes resources'):
            setup.check_bootstrap_plan({'resource_changes':[{'address':'aws_iam_role.cicd_github["build"]',
                'change':{'actions':['delete']}}]})

    def test_bootstrap_cannot_change_platform_resources(self):
        with self.assertRaisesRegex(setup.SetupError,'platform resources'):
            setup.check_bootstrap_plan({'resource_changes':[{'address':'aws_db_instance.oracle["management"]',
                'change':{'actions':['update']}}]})

    def test_restored_bootstrap_state_preserves_provider_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state={'region':'us-east-1','repo':'demo/platform',
                'state_bucket':'state','state_key':'platform.tfstate'}
            owned={'resources':[{'mode':'managed','type':'aws_iam_openid_connect_provider',
                                'name':'cicd_github','instances':[{}]}]}
            def fake_call(args,**kwargs):
                if 'show' in args:return json.dumps({'resource_changes':[]})
                if 'output' in args:return json.dumps({'artifact_bucket':'artifacts','roles':{'deploy':'arn:role'}})
                return ''
            with patch.object(setup,'TF',root),patch.object(setup,'WORK',root), \
                 patch.object(setup,'CONFIG',root/'variables.json'),patch.object(setup,'SETTINGS',root/'setup.json'), \
                 patch.object(setup,'read_state_object',side_effect=[{},owned]), \
                 patch.object(setup,'aws',side_effect=AssertionError('Provider is already in root state')), \
                 patch.object(setup,'call',side_effect=fake_call):
                setup.bootstrap(state,{},'demo/platform')
            self.assertEqual(state['existing_github_oidc_arn'],'')

    def test_legacy_separate_state_is_not_silently_recreated(self):
        old={'resources':[{'mode':'managed','type':'aws_iam_role','name':'github','instances':[{}]}]}
        state={'region':'us-east-1','repo':'demo/platform','state_bucket':'state','state_key':'platform.tfstate'}
        with patch.object(setup,'read_state_object',return_value=old),patch.object(setup,'call') as calls:
            with self.assertRaisesRegex(setup.SetupError,'separate CI bootstrap state still owns'):
                setup.bootstrap(state,{},'demo/platform')
            calls.assert_not_called()

    def test_saved_plan_must_match_the_reviewed_configuration_and_bytes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=root/'plan';plan.write_bytes(b'exact-plan')
            config=root/'vars.json';config.write_text('{}\n')
            state={'state_bucket':'state','state_key':'platform.tfstate'}
            metadata=dict(commit='commit',state_bucket='state',state_key='platform.tfstate',
                          plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(),
                          config_sha256=hashlib.sha256(config.read_bytes()).hexdigest())
            setup.check_plan_metadata(metadata,plan,state,'commit',config)
            plan.write_bytes(b'another-plan')
            with self.assertRaisesRegex(setup.SetupError,'checksum mismatch'):
                setup.check_plan_metadata(metadata,plan,state,'commit',config)

    def test_partial_database_initialization_is_not_automatically_replayed(self):
        state={'database_initialization':{'database':'db-secret','status':'started'}}
        with self.assertRaisesRegex(setup.SetupError,'previous SQL initialization'):
            setup.initialization_needed(state,'db-secret')
        state['database_initialization']['status']='complete'
        self.assertFalse(setup.initialization_needed(state,'db-secret'))

    def test_existing_environment_review_protection_is_not_silently_removed(self):
        calls=[]
        def fake_gh(endpoint,**kwargs):
            calls.append((endpoint,kwargs))
            if endpoint.startswith('users/'):return {'id':1}
            if endpoint.endswith('/deployment-branch-policies'):
                return {'branch_policies':[{'name':'main','type':'branch'}]}
            return {'deployment_branch_policy':{'custom_branch_policies':True},'protection_rules':[]}
        with patch.object(setup,'gh',side_effect=fake_gh):
            with self.assertRaisesRegex(setup.SetupError,'Add a required reviewer'):
                setup.configure_github({'repo':'demo/platform'},{},'reviewer',False)
        self.assertFalse(any(x[1].get('method')=='PUT' for x in calls))

    def test_first_release_flag_does_not_reinitialize_an_existing_release(self):
        with patch.object(setup,'context',return_value=({'repo':'demo/platform',
                 'artifact_bucket':'artifacts','initialized':True},{})), \
             patch.object(setup,'aws',return_value={}),patch.object(setup,'call') as calls:
            with self.assertRaisesRegex(setup.SetupError,'release already exists'):
                setup.release(SimpleNamespace(first=True))
            calls.assert_not_called()

    def test_new_lab_ami_selection_respects_the_existing_root_disk_limit(self):
        def image(name,size,date):return {'ImageId':name,'Architecture':'x86_64',
            'RootDeviceName':'/dev/sda1','CreationDate':date,
            'BlockDeviceMappings':[{'DeviceName':'/dev/sda1','Ebs':{'VolumeSize':size}}]}
        self.assertEqual(setup.rhel_image([image('ami-fits',20,'2026-09-01'),
                                          image('ami-too-large',30,'2026-10-01')]),'ami-fits')

if __name__ == '__main__':
    unittest.main()
