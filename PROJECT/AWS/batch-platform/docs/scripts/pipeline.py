#!/usr/bin/env python3
"""PR classification, credential-free image builds, and approved Terraform plans."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

SPEC = importlib.util.spec_from_file_location('batch_ci', Path(__file__).with_name('cicd.py'))
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
ROOT, WORK = ci.ROOT, ci.WORK
REVISION = '/batch-platform-lab/cicd/revision'


def emit(**values):
    for key, value in values.items():
        value = str(value).lower() if isinstance(value, bool) else str(value)
        print(key + '=' + value)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                out.write(key + '=' + value + '\n')


def classify(files, message, project_dir):
    prefix = project_dir.removeprefix('./').rstrip('/') + '/' if project_dir not in ('', '.') else ''
    paths = [p[len(prefix):] for p in files if p.startswith(prefix)]
    modules = {m for m in ci.SERVICES if any(p.startswith(m + '/') for p in paths)}
    shared = any(p in ('pom.xml', '.dockerignore') or p.startswith('.mvn/') for p in paths)
    infra = any(p.startswith('terraform/') for p in paths)
    helpers = any(p.startswith('docs/scripts/') for p in paths)
    workflow = any(p == '.github/workflows/batch-platform.yml' for p in files)
    explicit = ci.select_target(message)
    if explicit not in ('none', 'all') and (shared or modules - {explicit}):
        raise RuntimeError('The requested single service does not cover all changed application files. Split the PR or use [deploy].')
    target = explicit if explicit != 'none' else ('all' if shared or len(modules) > 1 else next(iter(modules), 'none'))
    # Workflow/helper edits validate all application modules; they do not silently release images.
    ci_target = 'all' if helpers or workflow else target
    return {'target': target, 'ci_target': ci_target, 'infrastructure': infra,
            'config_check': bool(infra or helpers or workflow or target != 'none')}


def select():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    sha = os.environ['GITHUB_SHA']
    if os.environ['GITHUB_EVENT_NAME'] == 'pull_request':
        pr = event['pull_request']
        base = pr['base']['sha']
        message = pr.get('title', '') + '\n' + (pr.get('body') or '')
        trusted = pr['head']['repo']['full_name'] == os.environ['GITHUB_REPOSITORY']
        trusted &= pr['author_association'] in ('OWNER', 'MEMBER', 'COLLABORATOR')
    else:
        trusted = True
        base = ci.aws('ssm', 'get-parameter', '--name', REVISION)['Parameter']['Value']
        if not re.fullmatch(r'[a-f0-9]{40}', base):
            base = event.get('before', '')
        message = (event.get('head_commit') or {}).get('message', '')
        # Refuse an older rerun after a newer successful deployment.
        if base and base != '0' * 40:
            check = subprocess.run(['git', 'merge-base', '--is-ancestor', base, sha])
            if check.returncode:
                raise RuntimeError('This commit is older than, or unrelated to, the last completed deployment. Run the current main revision.')
    if base and base != '0' * 40:
        raw = ci.capture('git', 'diff', '--name-only', '--no-renames', base, sha)
    else:
        raw = ci.capture('git', 'ls-files', '--full-name')
    result = classify(raw.splitlines(), message, os.environ.get('PROJECT_DIR', '.'))
    emit(**result, trusted=trusted)
    ci.note('Selection: `' + json.dumps(result, sort_keys=True) + '`')


def image_ref(module):
    return 'batch-ci/' + module + ':' + os.environ['GITHUB_SHA']


def build(target, archive):
    selected = ci.SERVICES if target == 'all' else (target,)
    args = ['mvn', '-B', '-ntp']
    if target != 'all':
        args += ['-pl', target, '-am']
    ci.run(*args, 'verify')
    for module in selected:
        ci.run('docker', 'build', '--platform=linux/amd64', '-f', module + '/Dockerfile',
               '-t', image_ref(module), '.')
    if archive:
        path = ROOT / 'ci-images'
        path.mkdir(exist_ok=True)
        tar = path / 'images.tar'
        ci.run('docker', 'save', '-o', str(tar), *(image_ref(m) for m in selected))
        manifest = {'sha': os.environ['GITHUB_SHA'], 'modules': list(selected), 'sha256': digest(tar)}
        (path / 'manifest.json').write_text(json.dumps(manifest))
    print('APPLICATION CI PASSED: Maven verification and linux/amd64 Docker builds.')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as src:
        for block in iter(lambda: src.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def push_images(target, tag):
    path = ROOT / 'ci-images'
    manifest = json.loads((path / 'manifest.json').read_text())
    selected = ci.SERVICES if target == 'all' else (target,)
    if manifest['sha'] != os.environ['GITHUB_SHA'] or not set(selected) <= set(manifest['modules']):
        raise RuntimeError('Image artifact belongs to another commit or does not contain the requested service.')
    if manifest['sha256'] != digest(path / 'images.tar'):
        raise RuntimeError('Image artifact checksum mismatch.')
    ci.run('docker', 'load', '-i', str(path / 'images.tar'))
    repos = ci.outputs()['ecr_repositories']
    registry = repos[selected[0]].split('/')[0]
    password = ci.capture('aws', 'ecr', 'get-login-password', '--region', os.environ['AWS_REGION'])
    subprocess.run(['docker', 'login', '--username', 'AWS', '--password-stdin', registry],
                   input=password, text=True, check=True)
    for module in selected:
        image = repos[module] + ':' + ci.validate_tag(tag)
        ci.run('docker', 'tag', image_ref(module), image)
        ci.run('docker', 'push', image)
    print('PUSHED: the images built by Application CI in this main workflow run.')


def runtime_variables(values):
    live = ci.service_rows(values) if values.get('service_names') else []
    if live:
        if len(live) != 4:
            raise RuntimeError('Resolve the incomplete ECS deployment before changing infrastructure.')
        tags = ci.current_service_tags(values)
        if set(tags) != set(ci.SERVICES):
            raise RuntimeError('Could not preserve all current service image tags.')
    else:
        tags = values['cicd_image_tags']
    return {'deploy_services': bool(live), 'service_image_tags': tags}


def guard_infrastructure(plan):
    supported = {
        'aws_db_instance', 'aws_db_subnet_group', 'aws_db_option_group',
        'aws_ecs_task_definition', 'aws_ecs_service', 'aws_ecs_cluster',
        'aws_vpc', 'aws_subnet', 'aws_route_table', 'aws_route_table_association',
        'aws_nat_gateway', 'aws_eip', 'aws_security_group',
        'aws_vpc_security_group_ingress_rule', 'aws_vpc_security_group_egress_rule',
        'aws_lb', 'aws_lb_target_group', 'aws_cloudwatch_log_group',
        'aws_cloudwatch_metric_alarm', 'aws_cloudwatch_log_metric_filter',
    }
    update_only = {'aws_db_instance', 'aws_db_subnet_group', 'aws_db_option_group',
                   'aws_ecs_service', 'aws_ecs_cluster', 'aws_vpc', 'aws_lb',
                   'aws_lb_target_group', 'aws_cloudwatch_log_group'}
    rejected = []
    for row in plan.get('resource_changes', []):
        actions = row['change']['actions']
        if row.get('mode') == 'data' or actions == ['no-op']:
            continue
        address = row['address']
        kind = row.get('type', address.split('.')[0])
        bootstrap = kind.startswith('aws_iam_') or kind == 'aws_ssm_parameter' or '.cicd_' in address
        destructive_data = 'delete' in actions and kind in (
            'aws_db_instance', 'aws_ecr_repository', 'aws_secretsmanager_secret', 'aws_vpc')
        secret = kind == 'aws_secretsmanager_secret'
        unsupported = kind not in supported or (kind in update_only and actions != ['update'])
        if bootstrap or destructive_data or secret or unsupported:
            rejected.append(address + ' ' + '/'.join(actions))
    if rejected:
        raise RuntimeError('These changes need the operator/bootstrap or cleanup procedure, not normal Infrastructure CD:\n' + '\n'.join(rejected))


def plan(speculative):
    ci.initialize_backend()
    values = ci.outputs()
    variables = runtime_variables(values)
    var_file = WORK / 'runtime.tfvars.json'
    var_file.write_text(json.dumps(variables))
    target = WORK / 'infrastructure.tfplan'
    ci.run('terraform', '-chdir=terraform', 'plan', '-no-color', '-input=false', '-lock-timeout=5m',
           '-var-file=cicd.runtime.tfvars', '-var-file=' + str(var_file), '-out=' + str(target))
    parsed = json.loads(ci.capture('terraform', '-chdir=terraform', 'show', '-json', str(target)))
    guard_infrastructure(parsed)
    changes = [r for r in parsed.get('resource_changes', []) if r.get('mode') != 'data' and r['change']['actions'] != ['no-op']]
    output_change = any(c['actions'] != ['no-op'] for c in parsed.get('output_changes', {}).values())
    ci.note('Infrastructure plan: ' + str(len(changes)) + ' changed resources. Current application image tags are preserved.')
    for row in changes:
        ci.note('`' + row['address'] + '` — ' + '/'.join(row['change']['actions']))
    if speculative:
        print('TERRAFORM CI PASSED: speculative plan only; nothing applied.')
        return
    key = 'plans/' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '/infrastructure.tfplan'
    sha = digest(target)
    uri = 's3://' + values['cicd_plan_bucket'] + '/' + key
    ci.run('aws', 's3', 'cp', str(target), uri, '--sse', 'AES256', '--only-show-errors')
    ci.note('Review the Terraform plan above before approving **infra-lab**. Plan SHA-256: `' + sha + '`.')
    emit(plan_key=key, plan_sha=sha, has_changes=bool(changes or output_change))


def apply():
    ci.initialize_backend()
    values = ci.outputs()
    key, sha = os.environ['BATCH_PLAN_KEY'], os.environ['BATCH_PLAN_SHA']
    if not re.fullmatch(r'plans/[0-9]+-[0-9]+/infrastructure\.tfplan', key) or not re.fullmatch(r'[a-f0-9]{64}', sha):
        raise RuntimeError('Invalid approved plan reference.')
    target = WORK / 'approved.tfplan'
    ci.run('aws', 's3', 'cp', 's3://' + values['cicd_plan_bucket'] + '/' + key, str(target), '--only-show-errors')
    if digest(target) != sha:
        raise RuntimeError('Approved plan checksum mismatch. Do not apply.')
    parsed = json.loads(ci.capture('terraform', '-chdir=terraform', 'show', '-json', str(target)))
    guard_infrastructure(parsed)
    ci.run('terraform', '-chdir=terraform', 'apply', '-input=false', '-lock-timeout=5m', str(target))
    # Do not create or automatically approve a new plan if this saved plan is stale.
    after = ci.outputs()
    if after.get('service_names'):
        ci.check_deployment(after, after['cicd_image_tags'])
    db = ci.aws('rds', 'describe-db-instances', '--db-instance-identifier', after['db_identifier'])['DBInstances'][0]
    ci.note('RDS current instance class: `' + db['DBInstanceClass'] + '`; MultiAZ: `' + str(db['MultiAZ']) + '`.')
    if db.get('PendingModifiedValues'):
        ci.note('RDS has pending modifications: `' + json.dumps(db['PendingModifiedValues']) + '`. With apply_immediately=false, AWS may wait for the maintenance window.')
        raise RuntimeError('AWS accepted the database change but has not finished applying it. Wait for the maintenance window, then rerun all jobs. Application CD is held until infrastructure is ready.')
    ci.note('INFRASTRUCTURE APPLY PASSED: the approved saved plan was applied.')


def complete():
    sha = os.environ['GITHUB_SHA']
    if not re.fullmatch(r'[a-f0-9]{40}', sha):
        raise RuntimeError('Invalid commit SHA.')
    ci.aws('ssm', 'put-parameter', '--name', REVISION, '--type', 'String', '--overwrite', '--value', sha)
    ci.note('WORKFLOW COMPLETE: ' + sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['select', 'build', 'push', 'plan', 'apply', 'complete'])
    parser.add_argument('--target', choices=['all', *ci.SERVICES], default='all')
    parser.add_argument('--archive', action='store_true')
    parser.add_argument('--speculative', action='store_true')
    parser.add_argument('--tag', default='')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.umask(0o077)
    WORK.mkdir(exist_ok=True)
    if args.command == 'select': select()
    elif args.command == 'build': build(args.target, args.archive)
    elif args.command == 'push': push_images(args.target, args.tag)
    elif args.command == 'plan': plan(args.speculative)
    elif args.command == 'apply': apply()
    else: complete()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, KeyError, ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('PIPELINE FAILED: ' + str(error))
