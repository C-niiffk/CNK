import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, BASE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ci = load('ci', 'cicd.py')
init = load('init', 'run-init.py')
secret = load('secret', 'init-secrets.py')
setup = load('setup', 'setup-cicd.py')
image_patch = load('image_patch', 'patch-image-tags.py')

def change(address, actions, before=None, after=None):
    return {'address':address, 'mode':'managed', 'change':{'actions':actions,'before':before,'after':after}}

class CICDTests(unittest.TestCase):
    def setUp(self):
        ctx = contextlib.redirect_stdout(io.StringIO())
        ctx.__enter__()
        self.addCleanup(lambda:ctx.__exit__(None,None,None))

    def test_plan_accepts_task_revision_and_service_update(self):
        ci.guard_plan({'resource_changes':[
            change('aws_ecs_task_definition.app["app1"]',['delete','create']),
            change('aws_ecs_service.core["backend"]',['update'])]},True)

    def test_commit_selection_accepts_service_line_and_full_deployment(self):
        self.assertEqual(ci.select_target('backend-service deploy\n\nFix the worker'), 'backend-service')
        self.assertEqual(ci.select_target('Frontend-Service Deploy'), 'frontend-service')
        self.assertEqual(ci.select_target('Update all services [deploy]'), 'all')
        self.assertEqual(ci.select_target('Document backend-service deploy syntax'), 'none')
        self.assertEqual(ci.select_target('backend-service deploy; echo injected'), 'none')

    def test_conflicting_commit_keywords_are_rejected(self):
        for message in ('backend-service deploy\n[deploy]', 'backend-service deploy\nagent-service deploy'):
            with self.assertRaises(RuntimeError): ci.select_target(message)

    def test_backend_tag_change_preserves_other_images(self):
        previous={module:'old-'+module for module in ci.SERVICES}
        updated=ci.release_tags(previous,'backend-service','new-backend')
        self.assertEqual(updated['backend-service'],'new-backend')
        for module in ci.SERVICES:
            if module!='backend-service':self.assertEqual(updated[module],previous[module])
        self.assertEqual(previous['backend-service'],'old-backend-service')
        with self.assertRaises(RuntimeError):ci.release_tags({},'backend-service','new-backend')

    def test_backend_plan_allows_only_backend_and_its_init_definition(self):
        ci.guard_plan({'resource_changes':[
            change('aws_ecs_task_definition.core["backend"]',['delete','create']),
            change('aws_ecs_task_definition.init',['delete','create']),
            change('aws_ecs_service.core["backend"]',['update'])]},True,'backend-service')
        for address in ('aws_ecs_service.core["frontend"]','aws_ecs_service.app["app1"]',
                        'aws_ecs_task_definition.app["app2"]','aws_db_instance.oracle'):
            with self.assertRaises(RuntimeError):
                ci.guard_plan({'resource_changes':[change(address,['update'])]},True,'backend-service')

    def test_backend_deployment_builds_one_image_and_never_runs_database_init(self):
        previous={module:'old-'+module for module in ci.SERVICES}
        expected={**previous,'backend-service':'sha-abc-1-1'}
        values={'cluster_name':'batch-platform-lab','db_identifier':'batch-platform-lab',
                'cicd_role_arn':'role','cicd_init_parameter':'/init','cicd_db_resource_id':'db-1','url':'http://example.invalid'}
        with contextlib.ExitStack() as stack:
            for name in ('check_sources','initialize_backend','check_deployment','repository_images'):
                stack.enter_context(patch.object(ci,name))
            stack.enter_context(patch.object(ci,'outputs',return_value=values))
            stack.enter_context(patch.object(ci,'aws',return_value={'DBInstances':[{'DBInstanceStatus':'available','DbiResourceId':'db-1'}]}))
            stack.enter_context(patch.object(ci,'service_rows',return_value=[{}, {}, {}, {}]))
            stack.enter_context(patch.object(ci,'current_tags',return_value=['old']))
            stack.enter_context(patch.object(ci,'current_service_tags',return_value=previous))
            stack.enter_context(patch.dict(os.environ,{'GITHUB_SHA':'abc','GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'}))
            command=stack.enter_context(patch.object(ci,'run'))
            apply=stack.enter_context(patch.object(ci,'apply_release'))
            ci.deploy('release','','backend-service')
            command.assert_called_once_with('bash','docs/scripts/build-push.sh','backend-service')
            apply.assert_called_once_with(expected,True,'backend-service')

    def test_image_patch_preserves_resource_sizes_and_rejects_unknown_layout(self):
        original='cpu = 256\nmemory = 512\ndesired_count = 1\n'
        for key in ('${each.key}-service','application-service','agent-service','backend-service'):
            original+='image = "${aws_ecr_repository.service["'+key+'"].repository_url}:${var.image_tag}"\n'
        patched=image_patch.patched(original)
        self.assertIn('cpu = 256\nmemory = 512\ndesired_count = 1',patched)
        self.assertEqual(patched.count('local.service_image_tags'),4)
        self.assertEqual(image_patch.patched(patched),patched)
        with self.assertRaises(RuntimeError):image_patch.patched('cpu = 256\n')

    def test_plan_rejects_service_removal_rds_and_iam_mutations(self):
        for row in [change('aws_ecs_service.core["backend"]',['delete']),
                    change('aws_db_instance.oracle',['delete','create']),
                    change('aws_iam_role_policy.cicd[0]',['update']),
                    change('aws_vpc.main',['create'])]:
            with self.assertRaises(RuntimeError): ci.guard_plan({'resource_changes':[row]},True)

    def test_init_image_plan_cannot_enable_services(self):
        with self.assertRaises(RuntimeError):
            ci.guard_plan({'resource_changes':[change('aws_ecs_service.app["app1"]',['create'])]},False)

    def test_only_missing_data_flag_may_change_on_target_alarm(self):
        address='aws_cloudwatch_metric_alarm.target_health["frontend"]'
        before={'treat_missing_data':'notBreaching','threshold':1}
        ci.guard_plan({'resource_changes':[change(address,['update'],before,dict(before,treat_missing_data='breaching'))]},True)
        with self.assertRaises(RuntimeError):
            ci.guard_plan({'resource_changes':[change(address,['update'],before,dict(before,threshold=0))]},True)

    def test_rollback_rejects_latest_empty_and_shell_fragments(self):
        for tag in ('latest','','x; echo unsafe','a'*129):
            with self.assertRaises(RuntimeError):ci.validate_tag(tag)
        self.assertEqual(ci.validate_tag('jdk21-v1'),'jdk21-v1')

    def test_database_password_format_and_length(self):
        for _ in range(30):
            password=secret.oracle_password()
            self.assertEqual(len(password),24)
            self.assertTrue(password[0].isalpha())
            self.assertRegex(password,r'^[A-Za-z0-9]{24}$')
            for pattern in ('[A-Z]','[a-z]','[0-9]'): self.assertRegex(password,pattern)

    def test_auto_mode_uses_first_deploy_only_for_known_new_or_inflight_init(self):
        for status in ('new', 'launching', 'running'):
            self.assertEqual(ci.choose_mode('db-1', [], {'db_resource_id':'db-1', 'status':status}), 'first-deploy')

    def test_auto_mode_resumes_services_without_rerunning_successful_init(self):
        for status in ('succeeded', 'adopted'):
            for live in ([], [{}], [{}, {}, {}, {}]):
                self.assertEqual(ci.choose_mode('db-1', live, {'db_resource_id':'db-1', 'status':status}), 'release')

    def test_auto_mode_blocks_unknown_database_and_unconfirmed_partial_services(self):
        for live, checkpoint in (
            ([], {'db_resource_id':'db-1', 'status':'unconfirmed'}),
            ([], {'db_resource_id':'old-db', 'status':'succeeded'}),
            ([{}], {'db_resource_id':'db-1', 'status':'new'}),
            ([], {'db_resource_id':'db-1', 'status':'failed'}),
        ):
            with self.assertRaises(RuntimeError): ci.choose_mode('db-1', live, checkpoint)

    def test_setup_distinguishes_empty_state_from_access_failure(self):
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,'','No state file was found!')):
            self.assertEqual(setup.managed_addresses(), set())
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,'','AccessDenied')):
            with self.assertRaisesRegex(RuntimeError, 'AccessDenied'): setup.managed_addresses()

    def test_setup_rejects_database_replacement(self):
        plan={'variables':{k:{'value':v} for k,v in {'region':'us-east-1','project':'batch-platform','environment':'lab'}.items()},
              'resource_changes':[change('aws_db_instance.oracle',['delete','create'])]}
        with self.assertRaisesRegex(RuntimeError, 'will not delete or replace'):
            setup.guard_setup_plan(plan, 'us-east-1')

    def test_setup_marks_new_database_and_preserves_completed_checkpoint(self):
        values={'cicd_init_parameter':'/init','cicd_db_resource_id':'db-1'}
        def answer(*args):
            if args[1]=='get-parameter':return {'Parameter':{'Value':json.dumps({'db_resource_id':'db-1','status':'unconfirmed'})}}
            self.assertEqual(json.loads(args[args.index('--value')+1])['status'], 'new')
            return {}
        with patch.object(setup, 'aws', side_effect=answer) as api:
            setup.configure_checkpoint(values, [], True)
            self.assertEqual(api.call_count,2)
        with patch.object(setup, 'aws', return_value={'Parameter':{'Value':json.dumps({'db_resource_id':'db-1','status':'succeeded'})}}) as api:
            setup.configure_checkpoint(values, [], False)
            self.assertEqual(api.call_count,1)

    def test_setup_does_not_label_an_unknown_existing_database_as_empty(self):
        values={'cicd_init_parameter':'/init','cicd_db_resource_id':'db-1'}
        with patch.object(setup, 'aws', return_value={'Parameter':{'Value':json.dumps({'db_resource_id':'db-1','status':'unconfirmed'})}}) as api:
            with self.assertRaisesRegex(RuntimeError, 'Existing DB has no confirmed initialization'):
                setup.configure_checkpoint(values, [], False)
            self.assertEqual(api.call_count,1)

    def test_setup_preserves_existing_user_configuration_and_literal_backend(self):
        text='allowed_cidrs = ["203.0.113.10/32"]\ngithub_repository = "old/repo"\n'
        merged=setup.merge_settings(text, {'github_repository':'new/repo'})
        self.assertIn('allowed_cidrs = ["203.0.113.10/32"]', merged)
        self.assertEqual(merged.count('github_repository ='),1)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'backend.hcl'
            p.write_text('bucket = "original-bucket"\nkey = "original/state.tfstate"\nregion = "us-east-1"\n')
            self.assertEqual(setup.read_backend(p)['key'],'original/state.tfstate')

    def test_successful_init_is_not_repeated(self):
        checkpoint={'db_resource_id':'db-1','status':'succeeded'}
        with patch.object(init,'aws',return_value={'Parameter':{'Value':json.dumps(checkpoint)}}) as api:
            init.initialize({'cicd_init_parameter':'/init','cicd_db_resource_id':'db-1'})
            self.assertEqual(api.call_count,1)

    def test_init_launch_reuses_token_and_marks_success(self):
        checkpoint={'db_resource_id':'db-1','status':'launching','client_token':'token',
                    'created_at':100,'cluster':'cluster','task_definition':'td:1','network':{}}
        saved=[]
        def aws(*args):
            if args[0]=='ssm':return {'Parameter':{'Value':json.dumps(checkpoint)}}
            if args[1]=='run-task':
                self.assertEqual(args[args.index('--client-token')+1],'token')
                self.assertEqual(args[args.index('--task-definition')+1],'td:1')
                return {'tasks':[{'taskArn':'task'}]}
            if args[1]=='describe-tasks':return {'tasks':[{'lastStatus':'STOPPED','containers':[{'name':'init','exitCode':0}]}]}
            self.fail(args)
        with patch.object(init,'aws',side_effect=aws), patch.object(init,'save',side_effect=lambda n,v:saved.append(v.copy())),patch.object(init.time,'time',return_value=110):
            init.initialize({'cicd_init_parameter':'/init','cicd_db_resource_id':'db-1'})
        self.assertEqual([s['status'] for s in saved],['running','succeeded'])

    def test_failed_init_blocks_a_second_ddl_attempt(self):
        checkpoint={'db_resource_id':'db-1','status':'failed'}
        with patch.object(init,'aws',return_value={'Parameter':{'Value':json.dumps(checkpoint)}}) as api:
            with self.assertRaisesRegex(RuntimeError,'Previous init-db failed'):
                init.initialize({'cicd_init_parameter':'/init','cicd_db_resource_id':'db-1'})
            self.assertEqual(api.call_count,1)

    def test_old_unconfirmed_launch_is_not_repeated(self):
        checkpoint={'db_resource_id':'db-1','status':'launching','created_at':0}
        with patch.object(init,'aws',return_value={'Parameter':{'Value':json.dumps(checkpoint)}}),patch.object(init.time,'time',return_value=3600):
            with self.assertRaisesRegex(RuntimeError,'older than 30 minutes'):
                init.initialize({'cicd_init_parameter':'/init','cicd_db_resource_id':'db-1'})

    def test_recreated_rds_does_not_reuse_previous_success(self):
        checkpoint={'db_resource_id':'old-db','status':'succeeded'}
        saved=[]
        with patch.object(init,'aws',side_effect=[{'Parameter':{'Value':json.dumps(checkpoint)}},RuntimeError('stop before launch')]), \
             patch.object(init,'save',side_effect=lambda n,v:saved.append(v.copy())):
            with self.assertRaisesRegex(RuntimeError,'stop before launch'):
                init.initialize({'cicd_init_parameter':'/init','cicd_db_resource_id':'new-db','cluster_name':'cluster',
                                 'init_task_definition':'td:1','init_network_configuration':{}})
        self.assertEqual(saved[0]['db_resource_id'],'new-db')
        self.assertEqual(saved[0]['status'],'launching')

    def test_automatic_rollback_is_not_reported_as_deploy_success(self):
        expected={name:'td:new' for name in ['frontend','backend','app1','app2']}
        values={'cicd_task_definitions':expected,'service_names':list(expected)}
        rows=[{'serviceName':name,'status':'ACTIVE','deployments':[{}],
               'taskDefinition':'td:old','runningCount':1,'desiredCount':1,'pendingCount':0} for name in expected]
        with patch.object(ci,'repository_images',return_value={'repo:new':'digest'}), \
             patch.object(ci,'aws',return_value={'taskDefinition':{'containerDefinitions':[{'image':'repo:new'}]}}), \
             patch.object(ci,'service_rows',return_value=rows):
            with self.assertRaisesRegex(RuntimeError,'possible automatic rollback'):
                ci.check_deployment(values,'new')

if __name__=='__main__':unittest.main()
