#!/usr/bin/env python3
"""Offline checks for CI adapters and the original scripts they invoke. No AWS writes."""
import ast
import importlib.util
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

class OneTimeSetup(unittest.TestCase):
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
