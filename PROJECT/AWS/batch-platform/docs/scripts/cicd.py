#!/usr/bin/env python3
"""Application CI/CD over the existing Terraform state. No infrastructure creation or destroy."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.cicd-work'
SERVICES = ('frontend-service', 'backend-service', 'agent-service', 'application-service')
TASK_ADDRESSES = {'aws_ecs_task_definition.init',
                  'aws_ecs_task_definition.core["frontend"]', 'aws_ecs_task_definition.core["backend"]',
                  'aws_ecs_task_definition.app["app1"]', 'aws_ecs_task_definition.app["app2"]'}
SERVICE_ADDRESSES = {'aws_ecs_service.core["frontend"]', 'aws_ecs_service.core["backend"]',
                     'aws_ecs_service.app["app1"]', 'aws_ecs_service.app["app2"]'}
ALARM_ADDRESSES = {'aws_cloudwatch_metric_alarm.target_health["frontend"]',
                   'aws_cloudwatch_metric_alarm.target_health["backend"]'}
TARGET_TASKS = {
    'frontend-service': {'aws_ecs_task_definition.core["frontend"]'},
    'backend-service': {'aws_ecs_task_definition.core["backend"]', 'aws_ecs_task_definition.init'},
    'agent-service': {'aws_ecs_task_definition.app["app1"]', 'aws_ecs_task_definition.app["app2"]'},
    'application-service': {'aws_ecs_task_definition.app["app1"]', 'aws_ecs_task_definition.app["app2"]'},
}
TARGET_SERVICES = {
    'frontend-service': {'aws_ecs_service.core["frontend"]'},
    'backend-service': {'aws_ecs_service.core["backend"]'},
    'agent-service': {'aws_ecs_service.app["app1"]', 'aws_ecs_service.app["app2"]'},
    'application-service': {'aws_ecs_service.app["app1"]', 'aws_ecs_service.app["app2"]'},
}


def select_target(message):
    chosen = {line.strip().lower().removesuffix(' deploy') for line in message.splitlines()
              if re.fullmatch(r'(frontend-service|backend-service|agent-service|application-service) deploy', line.strip(), re.I)}
    full = '[deploy]' in message.lower()
    if len(chosen) > 1 or (chosen and full):
        raise RuntimeError('Use one service deploy line OR [deploy], not both.')
    return next(iter(chosen)) if chosen else ('all' if full else 'none')


def emit_selection():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    if os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch':
        target = event.get('inputs', {}).get('target', 'all')
        if target not in ('all', *SERVICES):
            raise RuntimeError('Unknown rollback target.')
    else:
        target = select_target((event.get('head_commit') or {}).get('message', ''))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('target=' + target + '\n')
    print('Deployment selection:', target)


def capture(*args):
    return subprocess.check_output(list(args), text=True).strip()


def run(*args):
    subprocess.run(list(args), check=True)


def aws(*args):
    data = json.loads(capture('aws', '--no-cli-pager', '--output', 'json', *args) or '{}')
    if data.get('failures'):
        raise RuntimeError('AWS returned item failures: ' + json.dumps(data['failures']))
    return data


def outputs():
    return {k: v['value'] for k, v in json.loads(capture('terraform', '-chdir=terraform', 'output', '-json')).items()}


def note(message):
    print(message, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(message + '\n\n')


def validate_tag(tag):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag) or tag == 'latest':
        raise RuntimeError('Use an explicit immutable image tag, not latest.')
    return tag


def check_sources():
    files = list((ROOT / 'backend-service/src/main/java').rglob('*.java'))
    if any('OWNER_PASSWORD' in p.read_text() and '[A-Za-z0-9]{32,64}' in p.read_text() for p in files):
        raise RuntimeError('Old Oracle password validation found. Change [A-Za-z0-9]{32,64} to [A-Za-z0-9]{16,30} before building.')
    for p in (ROOT / 'frontend-service/src').rglob('index.html'):
        text = p.read_text()
        if 'crypto.randomUUID()' in text and 'getRandomValues' not in text:
            print('REVIEW: confirm index.html has an HTTP-compatible UUID fallback; CI cannot prove browser compatibility from a text check.')
    # Deployment tfvars are materialized at runtime from the protected BATCH_TFVARS secret.
    if not (ROOT / 'terraform/cicd-images.tf').is_file() or 'local.service_image_tags' not in (ROOT / 'terraform/ecs.tf').read_text():
        raise RuntimeError('Run install.sh to enable independent ECS image tags before building.')
    print('CI source/configuration checks passed.')


def materialize_runtime_tfvars():
    value = os.environ.get('BATCH_TFVARS')
    if not value:
        raise RuntimeError('Missing protected GitHub Actions secret: BATCH_TFVARS')
    path = ROOT / 'terraform/cicd.runtime.tfvars'
    path.write_text(value.rstrip() + '\n')
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def initialize_backend():
    required = ('BATCH_AWS_ACCOUNT_ID', 'BATCH_STATE_BUCKET', 'BATCH_STATE_KEY', 'AWS_REGION')
    for key in required:
        if not os.environ.get(key):
            raise RuntimeError('Missing GitHub Actions secret/environment value: ' + key)
    materialize_runtime_tfvars()
    account = os.environ['BATCH_AWS_ACCOUNT_ID']
    if aws('sts', 'get-caller-identity')['Account'] != account:
        raise RuntimeError('AWS account does not match BATCH_AWS_ACCOUNT_ID.')
    config = {'bucket': os.environ['BATCH_STATE_BUCKET'], 'key': os.environ['BATCH_STATE_KEY'],
              'region': os.environ['AWS_REGION'], 'encrypt': True, 'use_lockfile': True}
    path = ROOT / 'terraform/backend.hcl'
    path.write_text('\n'.join(f'{k} = {json.dumps(v)}' for k, v in config.items()) + '\n')
    run('terraform', '-chdir=terraform', 'init', '-input=false', '-reconfigure',
        '-lockfile=readonly', '-backend-config=backend.hcl')
    if capture('terraform', '-chdir=terraform', 'workspace', 'show') != 'default':
        raise RuntimeError('This pipeline uses the existing default workspace.')


def guard_plan(plan, allow_services, target='all'):
    tasks = TASK_ADDRESSES if target == 'all' else TARGET_TASKS[target]
    services = SERVICE_ADDRESSES if target == 'all' else TARGET_SERVICES[target]
    rejected = []
    for row in plan.get('resource_changes', []):
        address, change = row['address'], row['change']
        actions = change['actions']
        if row.get('mode') == 'data' or actions == ['no-op']:
            continue
        if address in tasks and actions in (['create'], ['update'], ['delete', 'create'], ['create', 'delete']):
            continue
        if allow_services and address in services and (actions == ['update'] or (target == 'all' and actions == ['create'])):
            continue
        if target == 'all' and allow_services and address in ALARM_ADDRESSES and actions == ['update']:
            before, after = change['before'], change['after']
            changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)} - {'id', 'arn'}
            if changed <= {'treat_missing_data'}:
                continue
        rejected.append(address + ': ' + ','.join(actions))
    if rejected:
        raise RuntimeError('Plan includes changes outside the selected deployment (' + target + '). '
                           'Review and apply infrastructure separately with the operator identity:\n' + '\n'.join(rejected))


def apply_release(tags, enable_services, target='all'):
    name = 'services' if enable_services else 'init-image'
    plan_path = WORK / (name + '.tfplan')
    variables = WORK / 'release.tfvars.json'
    variables.write_text(json.dumps({'service_image_tags': tags, 'deploy_services': enable_services}))
    run('terraform', '-chdir=terraform', 'plan', '-input=false', '-lock-timeout=5m',
        '-var-file=cicd.runtime.tfvars', '-var-file=' + str(variables), '-out=' + str(plan_path))
    plan = json.loads(capture('terraform', '-chdir=terraform', 'show', '-json', str(plan_path)))
    guard_plan(plan, enable_services, target)
    print('PLAN CHECK PASSED: changes are limited to ' + target + '.', flush=True)
    run('terraform', '-chdir=terraform', 'apply', '-input=false', str(plan_path))


def repository_images(values, tags):
    tags = {module: tags for module in SERVICES} if isinstance(tags, str) else tags
    result = {}
    repos = values['ecr_repositories']
    if set(repos) != set(SERVICES):
        raise RuntimeError('Expected the four batch-platform ECR repositories.')
    for service, url in repos.items():
        tag = validate_tag(tags[service])
        registry, name = url.split('/', 1)
        if registry.split('.')[0] != os.environ['BATCH_AWS_ACCOUNT_ID']:
            raise RuntimeError('ECR account does not match the deployment account.')
        repo = aws('ecr', 'describe-repositories', '--repository-names', name)['repositories'][0]
        if repo['imageTagMutability'] != 'IMMUTABLE':
            raise RuntimeError('This pipeline requires immutable ECR repositories: ' + name)
        image = aws('ecr', 'describe-images', '--repository-name', name, '--image-ids', 'imageTag=' + tag)['imageDetails'][0]
        result[url + ':' + tag] = image['imageDigest']
    return result


def service_rows(values):
    names = values['service_names']
    if not names:
        return []
    data = aws('ecs', 'describe-services', '--cluster', values['cluster_name'], '--services', *names)
    if len(data.get('services', [])) != len(names):
        raise RuntimeError('Some Terraform-managed ECS services are missing. Inspect partial cleanup before deployment.')
    return data['services']


def current_tags(values):
    tags = set()
    for service in service_rows(values):
        task = aws('ecs', 'describe-task-definition', '--task-definition', service['taskDefinition'])['taskDefinition']
        tags.update(c['image'].rsplit(':', 1)[-1] for c in task['containerDefinitions'])
    return sorted(tags)


def current_service_tags(values):
    tags = {}
    repos = values['ecr_repositories']
    for service in service_rows(values):
        task = aws('ecs', 'describe-task-definition', '--task-definition', service['taskDefinition'])['taskDefinition']
        for container in task['containerDefinitions']:
            image = container['image']
            matches = [name for name, url in repos.items() if image.startswith(url + ':')]
            if len(matches) != 1:
                raise RuntimeError('Expected a tagged project image: ' + image)
            module = matches[0]
            tag = validate_tag(image[len(repos[module]) + 1:])
            if module in tags and tags[module] != tag:
                raise RuntimeError('App1/App2 are using different versions of ' + module + '; wait for their deployment to finish or use [deploy].')
            tags[module] = tag
    return tags


def release_tags(previous, target, tag):
    if target == 'all':
        return {module: tag for module in SERVICES}
    if target not in SERVICES or set(previous) != set(SERVICES):
        raise RuntimeError('A single-service deployment needs an existing complete deployment. Use [deploy] first.')
    return {**previous, target: tag}


def ensure_secrets(values):
    arn = values['runtime_secret_arn']
    versions = aws('secretsmanager', 'list-secret-version-ids', '--secret-id', arn)
    if not versions.get('Versions'):
        run('python3', 'docs/scripts/init-secrets.py')
    else:
        print('Existing runtime secret reused; passwords will not be regenerated.')
    secret = json.loads(aws('secretsmanager', 'get-secret-value', '--secret-id', arn)['SecretString'])
    for key in ('owner_password', 'reader_password'):
        if not re.fullmatch(r'[A-Za-z0-9]{16,30}', secret.get(key, '')):
            raise RuntimeError('Existing ' + key + ' is incompatible with the Oracle initializer. Coordinate the secret and database password fix; no automatic rotation.')
    if not all(secret.get(k) for k in ('internal_token', 'ui_password')):
        raise RuntimeError('Runtime secret is missing internal_token or ui_password.')
    print('Runtime secret keys and Oracle password lengths validated; no values printed.')


def choose_mode(db_resource_id, live, checkpoint):
    if checkpoint.get('db_resource_id') != db_resource_id:
        raise RuntimeError('RDS identity changed. Rerun setup-cicd.py before deploying.')
    status = checkpoint.get('status')
    if status == 'failed':
        raise RuntimeError('Previous init-db failed. Inspect the schema before retrying; automatic initialization is blocked.')
    if status in ('succeeded', 'adopted'):
        return 'release'
    if len(live) == 4:
        return 'release'
    if live:
        raise RuntimeError('Partial services exist without a confirmed initialized database. Inspect the previous deployment.')
    if status in ('new', 'launching', 'running'):
        return 'first-deploy'
    raise RuntimeError('Database initialization is unconfirmed. Finish setup-cicd.py; an existing schema must not be initialized blindly.')


def check_deployment(values, tag, target='all'):
    expected = values['cicd_task_definitions']
    if len(expected) != 4 or set(values['service_names']) != set(expected):
        raise RuntimeError('Expected four ECS services and four desired task definition ARNs.')
    wanted_images = repository_images(values, tag)
    for arn in expected.values():
        definition = aws('ecs', 'describe-task-definition', '--task-definition', arn)['taskDefinition']
        for container in definition['containerDefinitions']:
            if container['image'] not in wanted_images:
                raise RuntimeError('Task definition references an unexpected image: ' + container['image'])
    for attempt in range(120):
        ready = True
        rows = service_rows(values)
        for service in rows:
            name = service['serviceName']
            if service.get('status') != 'ACTIVE':
                raise RuntimeError(name + ' is not ACTIVE.')
            deployments = service.get('deployments', [])
            if any(d.get('taskDefinition') == expected[name] and d.get('rolloutState') == 'FAILED' for d in deployments):
                raise RuntimeError(name + ' failed its requested deployment.')
            stable = len(deployments) == 1 and service['runningCount'] == service['desiredCount'] and service['pendingCount'] == 0
            if stable and service['taskDefinition'] != expected[name]:
                raise RuntimeError(name + ' is stable on another revision (possible automatic rollback). Requested release failed.')
            ready &= stable and service['desiredCount'] > 0 and service['taskDefinition'] == expected[name]
        if ready:
            break
        print(f'Waiting for requested ECS revisions ({attempt + 1}/120)', flush=True)
        time.sleep(15)
    else:
        raise RuntimeError('ECS did not stabilize on the requested release within 30 minutes.')
    for name, expected_arn in expected.items():
        arns = aws('ecs', 'list-tasks', '--cluster', values['cluster_name'], '--service-name', name,
                   '--desired-status', 'RUNNING')['taskArns']
        if not arns:
            raise RuntimeError('No running tasks for ' + name)
        for i in range(0, len(arns), 100):
            tasks = aws('ecs', 'describe-tasks', '--cluster', values['cluster_name'], '--tasks', *arns[i:i+100])['tasks']
            if len(tasks) != len(arns[i:i+100]):
                raise RuntimeError('Some running tasks could not be inspected.')
            if any(t['lastStatus'] != 'RUNNING' or t['taskDefinitionArn'] != expected_arn or t.get('healthStatus') == 'UNHEALTHY' for t in tasks):
                raise RuntimeError('Unexpected or unhealthy running task for ' + name)
    groups = values['target_group_arns']
    if len(groups) != 2:
        raise RuntimeError('Expected frontend and backend target groups.')
    for attempt in range(40):
        ready = True
        for arn in groups.values():
            targets = aws('elbv2', 'describe-target-health', '--target-group-arn', arn)['TargetHealthDescriptions']
            ready &= bool(targets) and all(t['TargetHealth']['State'] == 'healthy' for t in targets)
        if ready:
            note('DEPLOYMENT PASSED: ' + target + ' release is running; all four services and both target groups are healthy.')
            return
        print('Waiting for healthy ALB targets.', flush=True)
        time.sleep(15)
    raise RuntimeError('ALB targets were not all healthy within 10 minutes.')


def deploy(mode, rollback_tag, target='all'):
    check_sources()
    initialize_backend()
    values = outputs()
    for key in ('cluster_name', 'db_identifier', 'cicd_role_arn', 'cicd_init_parameter', 'cicd_db_resource_id'):
        if not values.get(key):
            raise RuntimeError('Missing Terraform output ' + key + '; finish the one-time infrastructure/CI setup first.')
    if values['cluster_name'] != 'batch-platform-lab' or values['db_identifier'] != 'batch-platform-lab':
        raise RuntimeError('This runbook targets batch-platform-lab.')
    db = aws('rds', 'describe-db-instances', '--db-instance-identifier', values['db_identifier'])['DBInstances'][0]
    if db['DBInstanceStatus'] != 'available' or db['DbiResourceId'] != values['cicd_db_resource_id']:
        raise RuntimeError('The expected RDS instance is not available.')
    live = service_rows(values)
    if mode == 'auto':
        checkpoint = json.loads(aws('ssm', 'get-parameter', '--name', values['cicd_init_parameter'])['Parameter']['Value'])
        mode = choose_mode(db['DbiResourceId'], live, checkpoint)
        note('Automatic deployment mode: ' + mode)
    if mode == 'first-deploy' and live:
        raise RuntimeError('Services already exist. Use release; first-deploy is only for a new empty schema.')
    if target != 'all' and (mode == 'first-deploy' or len(live) != 4):
        raise RuntimeError('Use [deploy] for the first complete deployment; a single service cannot initialize the platform.')
    if mode != 'first-deploy' and len(live) != 4:
        checkpoint = json.loads(aws('ssm', 'get-parameter', '--name', values['cicd_init_parameter'])['Parameter']['Value'])
        if checkpoint.get('db_resource_id') != db['DbiResourceId'] or checkpoint.get('status') not in ('succeeded', 'adopted'):
            raise RuntimeError('Services are incomplete and schema initialization is unconfirmed. Use first-deploy for a new database, or inspect partial deployment.')
    note('Previous image tags: ' + (', '.join(current_tags(values)) or '(first deployment)'))
    previous = current_service_tags(values) if target != 'all' else {}
    if target != 'all':
        note('Previous per-service tags: `' + json.dumps(previous, sort_keys=True) + '`')
    if mode == 'rollback':
        tag = validate_tag(rollback_tag)
    elif os.environ.get('BATCH_USE_CI_IMAGES') == 'true':
        tag = validate_tag('sha-' + os.environ['GITHUB_SHA'] + '-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'])
        run('python3', 'docs/scripts/pipeline.py', 'push', '--target', target, '--tag', tag)
    else:
        tag = validate_tag('sha-' + os.environ['GITHUB_SHA'] + '-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'])
        os.environ['IMAGE_TAG'] = tag
        selected = SERVICES if target == 'all' else (target,)
        run('bash', 'docs/scripts/build-push.sh', *selected)
    tags = release_tags(previous, target, tag)
    repository_images(values, tags)
    note('Deployment target: `' + target + '`; requested image tag: `' + tag + '`')
    if mode == 'first-deploy':
        ensure_secrets(values)
        checkpoint = json.loads(aws('ssm', 'get-parameter', '--name', values['cicd_init_parameter'])['Parameter']['Value'])
        if checkpoint.get('db_resource_id') == db['DbiResourceId'] and checkpoint.get('status') in ('launching', 'running'):
            # Resume before replacing/deregistering the task definition saved in the launch request.
            run('python3', 'docs/scripts/run-init.py')
        apply_release(tags, False)
        run('python3', 'docs/scripts/run-init.py')
    apply_release(tags, True, target)
    values = outputs()
    check_deployment(values, tags, target)
    note('Deployed per-service tags: `' + json.dumps(tags, sort_keys=True) + '`')
    note('Frontend URL: ' + values['url'])
    note('Run the four SH/API checks from your allowlisted browser. CI checks AWS service health; it does not run a browser/business smoke test.')
    note('For infrastructure changes, open a Terraform PR; Infrastructure CD preserves these per-service image tags.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['select', 'check', 'deploy'])
    parser.add_argument('--target', choices=['all', *SERVICES], default='all')
    parser.add_argument('--mode', choices=['auto', 'first-deploy', 'release', 'rollback'], default='auto')
    parser.add_argument('--rollback-tag', default='')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.umask(0o077)
    WORK.mkdir(exist_ok=True)
    if args.command == 'select':
        emit_selection()
    elif args.command == 'check':
        check_sources()
    else:
        deploy(args.mode, args.rollback_tag, args.target)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, KeyError, ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('FAILED: ' + str(error))
