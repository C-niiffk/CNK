import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pipeline', BASE / 'pipeline.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def resource(kind, name, actions):
    return {'address': kind + '.' + name, 'type': kind, 'mode': 'managed', 'change': {'actions': actions}}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.silent = contextlib.redirect_stdout(io.StringIO())
        self.silent.__enter__()
        self.addCleanup(lambda: self.silent.__exit__(None, None, None))

    def test_nested_project_backend_only_and_infra_only(self):
        prefix = 'PROJECT/AWS/batch-platform/'
        result = p.classify([prefix+'backend-service/src/Worker.java'], 'backend-service deploy', prefix[:-1])
        self.assertEqual(result['target'], 'backend-service')
        self.assertFalse(result['infrastructure'])
        result = p.classify([prefix+'terraform/ecs.tf'], 'Increase CPU', prefix[:-1])
        self.assertEqual(result['target'], 'none')
        self.assertTrue(result['infrastructure'])
        self.assertEqual(result['ci_target'], 'none')

    def test_documentation_does_not_build_or_deploy(self):
        result = p.classify(['docs/architecture.md'], 'Update diagram', '.')
        self.assertEqual(result['target'], 'none')
        self.assertEqual(result['ci_target'], 'none')
        self.assertFalse(result['config_check'])

    def test_shared_pom_and_two_modules_need_full_release(self):
        for paths in (['pom.xml'], ['backend-service/pom.xml','frontend-service/src/Test.java']):
            self.assertEqual(p.classify(paths, '', '.')['target'], 'all')
            with self.assertRaises(RuntimeError): p.classify(paths, 'backend-service deploy', '.')

    def test_workflow_edit_tests_all_but_does_not_force_application_release(self):
        result = p.classify(['.github/workflows/batch-platform.yml'], '', 'PROJECT/AWS/batch-platform')
        self.assertEqual(result['ci_target'], 'all')
        self.assertEqual(result['target'], 'none')
        self.assertTrue(result['config_check'])

    def test_explicit_release_without_source_changes(self):
        self.assertEqual(p.classify([], '[deploy]', '.')['target'], 'all')
        self.assertEqual(p.classify([], 'backend-service deploy', '.')['target'], 'backend-service')

    def test_infrastructure_preserves_independent_image_tags_and_services(self):
        tags = {m:'old-'+m for m in p.ci.SERVICES}
        with patch.object(p.ci, 'service_rows', return_value=[{}]*4), patch.object(p.ci, 'current_service_tags', return_value=tags):
            result = p.runtime_variables({'service_names':['a','b','c','d']})
        self.assertTrue(result['deploy_services'])
        self.assertEqual(result['service_image_tags'], tags)
        self.assertEqual(p.runtime_variables({'service_names':[], 'cicd_image_tags': tags}),
                         {'deploy_services':False, 'service_image_tags':tags})

    def test_infrastructure_rejects_partial_live_deployment(self):
        with patch.object(p.ci, 'service_rows', return_value=[{}]):
            with self.assertRaises(RuntimeError): p.runtime_variables({'service_names':['only-one']})

    def test_infrastructure_allows_size_changes_but_not_database_replacement_or_iam(self):
        p.guard_infrastructure({'resource_changes':[
            resource('aws_db_instance','oracle',['update']),
            resource('aws_ecs_task_definition','core["backend"]',['delete','create']),
            resource('aws_subnet','private[2]',['create'])]})
        for change in (resource('aws_db_instance','oracle',['delete','create']),
                       resource('aws_iam_role','execution',['update']),
                       resource('aws_ssm_parameter','cicd_init',['update']),
                       resource('aws_s3_bucket','cicd_plans',['delete']),
                       resource('aws_lb_listener','new',['create'])):
            with self.assertRaises(RuntimeError): p.guard_infrastructure({'resource_changes':[change]})

    def test_pr_build_never_saves_or_pushes_images(self):
        with patch.object(p.ci,'run') as run, patch.dict(os.environ, {'GITHUB_SHA':'a'*40}):
            p.build('backend-service', False)
        calls = [c.args for c in run.call_args_list]
        self.assertEqual(calls[0],('mvn','-B','-ntp','-pl','backend-service','-am','verify'))
        self.assertIn('--platform=linux/amd64', calls[1])
        self.assertEqual(len(calls), 2)

    def test_wrong_commit_image_cannot_be_pushed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'ROOT',Path(tmp)), patch.dict(os.environ,{'GITHUB_SHA':'a'*40}):
            folder=Path(tmp)/'ci-images'
            folder.mkdir()
            (folder/'manifest.json').write_text(json.dumps({'sha':'b'*40,'modules':['backend-service']}))
            with patch.object(p.ci, 'run') as run:
                with self.assertRaises(RuntimeError): p.push_images('backend-service','tag')
                run.assert_not_called()

    def test_tampered_image_archive_cannot_be_pushed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'ROOT',Path(tmp)), patch.dict(os.environ,{'GITHUB_SHA':'a'*40}):
            folder=Path(tmp)/'ci-images'
            folder.mkdir()
            (folder/'images.tar').write_bytes(b'changed')
            (folder/'manifest.json').write_text(json.dumps({'sha':'a'*40,'modules':['backend-service'],'sha256':'0'*64}))
            with patch.object(p.ci, 'run') as run:
                with self.assertRaises(RuntimeError): p.push_images('backend-service','tag')
                run.assert_not_called()

    def test_stale_saved_plan_does_not_replan(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'WORK',Path(tmp)), patch.dict(os.environ,{
                'BATCH_PLAN_KEY':'plans/123-1/infrastructure.tfplan','BATCH_PLAN_SHA':'a'*64}):
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(p.ci,'initialize_backend'))
                stack.enter_context(patch.object(p.ci,'outputs',return_value={'cicd_plan_bucket':'bucket'}))
                stack.enter_context(patch.object(p,'digest',return_value='a'*64))
                stack.enter_context(patch.object(p.ci,'capture',return_value='{}'))
                run=stack.enter_context(patch.object(p.ci,'run',side_effect=[None,subprocess.CalledProcessError(1,'terraform apply')]))
                with self.assertRaises(subprocess.CalledProcessError): p.apply()
                self.assertEqual(len(run.call_args_list),2)
                self.assertEqual(run.call_args_list[-1].args[2],'apply')
                self.assertNotIn('plan',run.call_args_list[-1].args)

    def test_plan_checksum_mismatch_stops_before_terraform_apply(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'WORK',Path(tmp)), patch.dict(os.environ,{
                'BATCH_PLAN_KEY':'plans/123-1/infrastructure.tfplan','BATCH_PLAN_SHA':'a'*64}):
            with patch.object(p.ci,'initialize_backend'), patch.object(p.ci,'outputs',return_value={'cicd_plan_bucket':'bucket'}), \
                 patch.object(p,'digest',return_value='b'*64), patch.object(p.ci,'run') as run:
                with self.assertRaises(RuntimeError): p.apply()
                self.assertEqual(len(run.call_args_list),1)
                self.assertEqual(run.call_args.args[:3],('aws','s3','cp'))


if __name__ == '__main__':
    unittest.main()
