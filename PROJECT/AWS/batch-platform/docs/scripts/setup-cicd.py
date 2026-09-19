#!/usr/bin/env python3
"""One-time lab setup: state bucket, infrastructure, OIDC, and GitHub variables.

Run from your Mac with the existing AWS operator profile. This executes a saved
Terraform plan automatically; it never commits code or starts a GitHub workflow.
"""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / 'terraform'
WORK = ROOT / '.cicd-work'


def capture(*args):
    return subprocess.check_output(list(args), text=True).strip()


def run(*args):
    subprocess.run(list(args), check=True)


def aws(*args):
    return json.loads(capture('aws', '--no-cli-pager', '--output', 'json', *args) or '{}')


def outputs():
    return {k: v['value'] for k, v in json.loads(capture('terraform', '-chdir=terraform', 'output', '-json')).items()}


def managed_addresses():
    result = subprocess.run(['terraform', '-chdir=terraform', 'state', 'list'], text=True, capture_output=True)
    if result.returncode:
        if 'No state file was found' in result.stderr:
            return set()
        raise RuntimeError(result.stderr.strip())
    return set(result.stdout.splitlines())


def load_ci():
    spec = importlib.util.spec_from_file_location('batch_ci', ROOT / 'docs/scripts/cicd.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_backend(path):
    result = {}
    if path.exists():
        for key in ('bucket', 'key', 'region'):
            match = re.search(r'(?m)^\s*' + key + r'\s*=\s*("(?:[^"\\]|\\.)*")\s*(?:#.*)?$', path.read_text())
            if not match:
                raise RuntimeError('Use literal bucket, key, and region strings in terraform/backend.hcl.')
            result[key] = json.loads(match.group(1))
            if '$' in result[key]:
                raise RuntimeError('backend.hcl contains an unexpanded variable: ' + key)
    return result


def merge_settings(text, settings):
    # These managed settings are scalar strings; all other user settings are preserved.
    for key, value in settings.items():
        text = re.sub(r'(?m)^\s*' + re.escape(key) + r'\s*=.*$', '', text)
        text += '\n' + key + ' = ' + json.dumps(value) + '\n'
    return text


def guard_setup_plan(plan, expected_region):
    variables = plan['variables']
    for key, expected in (('region', expected_region), ('project', 'batch-platform'), ('environment', 'lab')):
        if variables[key]['value'] != expected:
            raise RuntimeError('Setup requires ' + key + '=' + expected + '. Check the existing variables.')
    deleted = [r['address'] for r in plan.get('resource_changes', [])
               if r.get('mode') != 'data' and 'delete' in r['change']['actions']]
    if deleted:
        raise RuntimeError('Setup will not delete or replace existing resources: ' + ', '.join(deleted))


def ensure_bucket(bucket, account, region):
    result = subprocess.run(['aws', '--output', 'json', 's3api', 'get-bucket-location',
                             '--bucket', bucket, '--expected-bucket-owner', account],
                            text=True, capture_output=True)
    if result.returncode:
        if 'NoSuchBucket' not in result.stderr:
            raise RuntimeError(result.stderr.strip())
        args = ['s3api', 'create-bucket', '--bucket', bucket, '--region', region]
        if region != 'us-east-1':
            args += ['--create-bucket-configuration', 'LocationConstraint=' + region]
        aws(*args)
    else:
        location = json.loads(result.stdout).get('LocationConstraint') or 'us-east-1'
        if location != region:
            raise RuntimeError('Existing state bucket is in a different region: ' + location)
    aws('s3api', 'put-bucket-versioning', '--bucket', bucket,
        '--versioning-configuration', 'Status=Enabled')
    aws('s3api', 'put-public-access-block', '--bucket', bucket,
        '--public-access-block-configuration',
        'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true')
    if result.returncode:
        aws('s3api', 'put-bucket-encryption', '--bucket', bucket,
            '--server-side-encryption-configuration',
            json.dumps({'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}))
    encryption = aws('s3api', 'get-bucket-encryption', '--bucket', bucket)
    if any(r['ApplyServerSideEncryptionByDefault']['SSEAlgorithm'] != 'AES256'
           for r in encryption['ServerSideEncryptionConfiguration']['Rules']):
        raise RuntimeError('This lab CI role supports SSE-S3 state encryption. An existing KMS bucket needs matching KMS permissions.')
    print('STATE BUCKET READY:', bucket, flush=True)


def new_variables(region):
    options = aws('rds', 'describe-orderable-db-instance-options', '--engine', 'oracle-se2',
                  '--db-instance-class', 'db.t3.small', '--license-model', 'license-included', '--vpc')
    versions = sorted({r['EngineVersion'] for r in options['OrderableDBInstanceOptions']
                       if r['EngineVersion'].startswith('19.') and r['StorageType'] == 'gp3'})
    if not versions:
        raise RuntimeError('No Oracle 19c / db.t3.small / gp3 option was returned in this region.')
    with urllib.request.urlopen('https://checkip.amazonaws.com', timeout=15) as response:
        address = str(ipaddress.IPv4Address(response.read().decode().strip()))
    values = dict(region=region, project='batch-platform', environment='lab',
                  use_custom_domain=False, domain_name='', hosted_zone_id='',
                  allowed_cidrs=[address + '/32'], oracle_engine_version=versions[-1],
                  db_instance_class='db.t3.small', image_tag='jdk21-v1', deploy_services=False,
                  protect_data=True, final_snapshot_identifier='batch-platform-lab-final', alarm_email='')
    print('NEW LAB CONFIG: Oracle', versions[-1], '/ db.t3.small; browser CIDR', address + '/32')
    return '\n'.join(key + ' = ' + json.dumps(value) for key, value in values.items()) + '\n'


def configure_checkpoint(values, live, database_created):
    name = values['cicd_init_parameter']
    checkpoint = json.loads(aws('ssm', 'get-parameter', '--name', name)['Parameter']['Value'])
    same = checkpoint.get('db_resource_id') == values['cicd_db_resource_id']
    if same and checkpoint.get('status') in ('new', 'launching', 'running', 'succeeded', 'adopted'):
        return
    if same and checkpoint.get('status') == 'failed':
        raise RuntimeError('Previous database initialization failed. Setup will not erase that checkpoint.')
    if database_created:
        status = 'new'
    elif len(live) == 4 and all(s['status'] == 'ACTIVE' and s['desiredCount'] > 0
                                and s['runningCount'] == s['desiredCount'] and s['pendingCount'] == 0 for s in live):
        status = 'adopted'
    else:
        raise RuntimeError('Existing DB has no confirmed initialization and no complete running deployment. Inspect its schema before allowing init-db; the checkpoint was not reset.')
    aws('ssm', 'put-parameter', '--name', name, '--type', 'String', '--overwrite',
        '--value', json.dumps({'db_resource_id': values['cicd_db_resource_id'], 'status': status}))
    print('DATABASE CHECKPOINT:', status, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='The intended 12-digit AWS account ID')
    parser.add_argument('--repo', help='OWNER/REPO; defaults to the current Git remote repository')
    parser.add_argument('--reviewer', help='GitHub login of an infrastructure reviewer; defaults to your gh login')
    parser.add_argument('--solo', action='store_true', help='One-person lab: PR still required, but no second-person PR approval')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.umask(0o077)
    WORK.mkdir(exist_ok=True)
    if sys.version_info < (3, 9):
        raise RuntimeError('Python 3.9 or later is required.')
    for command in ('aws', 'terraform', 'gh', 'git'):
        if not shutil.which(command):
            raise RuntimeError('Install the missing tool: ' + command)
    version = json.loads(capture('terraform', 'version', '-json'))['terraform_version']
    parts = tuple(int(p) for p in version.split('-')[0].split('.')[:2])
    if not ((1, 11) <= parts < (2, 0)):
        raise RuntimeError('Terraform >=1.11.0 and <2.0.0 is required.')
    if 'aws-cli/2.' not in capture('aws', '--version'):
        raise RuntimeError('AWS CLI v2 is required.')
    if not re.fullmatch(r'\d{12}', args.account) or aws('sts', 'get-caller-identity')['Account'] != args.account:
        raise RuntimeError('AWS credentials do not match --account.')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    os.environ.update(AWS_REGION=region, AWS_DEFAULT_REGION=region, AWS_PAGER='', AWS_DEFAULT_OUTPUT='json')
    repo_root = Path(capture('git', 'rev-parse', '--show-toplevel'))
    project_dir = str(ROOT.relative_to(repo_root))
    repo_name = args.repo or json.loads(capture('gh', 'repo', 'view', '--json', 'nameWithOwner'))['nameWithOwner']
    repo = json.loads(capture('gh', 'api', 'repos/' + repo_name))
    repo_name = repo['full_name']
    reviewer = args.reviewer or json.loads(capture('gh', 'api', 'user'))['login']
    if not args.solo and reviewer == json.loads(capture('gh', 'api', 'user'))['login']:
        raise RuntimeError('Use --reviewer OTHER_LOGIN for team review, or --solo for a one-person lab.')
    print('SETTING UP:', args.account, region, repo_name, project_dir, flush=True)

    def variable(name, value):
        run('gh', 'variable', 'set', name, '--repo', repo_name, '--body', value)

    def secret(name, value):
        # gh encrypts repository secrets client-side before upload; they are never committed to Git.
        run('gh', 'secret', 'set', name, '--repo', repo_name, '--body', value)

    # Also pause the preceding package's workflow during migration.
    variable('BATCH_CD_ENABLED', 'false')
    variable('BATCH_CD_PAUSED', 'true')
    github_args = ['python3', 'docs/scripts/configure-github.py', '--repo', repo_name, '--reviewer', reviewer]
    if args.solo:
        github_args.append('--solo')
    run(*github_args)
    backend = read_backend(TF / 'backend.hcl')
    bucket = backend.get('bucket', 'batch-tfstate-' + args.account + '-' + region)
    key = backend.get('key', 'batch-platform/lab/terraform.tfstate')
    if backend.get('region', region) != region:
        raise RuntimeError('AWS_REGION differs from the original backend region.')
    ensure_bucket(bucket, args.account, region)
    backend.update(bucket=bucket, key=key, region=region, encrypt=True, use_lockfile=True)
    (TF / 'backend.hcl').write_text('\n'.join(k + ' = ' + json.dumps(v) for k, v in backend.items()) + '\n')
    run('terraform', '-chdir=terraform', 'init', '-input=false', '-backend-config=backend.hcl')
    if capture('terraform', '-chdir=terraform', 'workspace', 'show') != 'default':
        raise RuntimeError('This lab setup uses the original default workspace.')
    managed = managed_addresses()
    before = outputs()
    ci = load_ci()
    live = ci.service_rows(before) if before.get('service_names') else []
    overrides = []
    if live:
        tags = ci.current_service_tags(before)
        if set(tags) != set(ci.SERVICES):
            raise RuntimeError('Existing deployment is incomplete. Resolve it before changing infrastructure.')
        overrides = ['-var=deploy_services=true', '-var=service_image_tags=' + json.dumps(tags)]
    else:
        overrides = ['-var=deploy_services=false']
    provider = 'arn:aws:iam::' + args.account + ':oidc-provider/token.actions.githubusercontent.com'
    result = subprocess.run(['aws', '--output', 'json', 'iam', 'get-open-id-connect-provider',
                             '--open-id-connect-provider-arn', provider], text=True, capture_output=True)
    if result.returncode and 'NoSuchEntity' not in result.stderr:
        raise RuntimeError(result.stderr.strip())
    if not result.returncode and 'sts.amazonaws.com' not in json.loads(result.stdout)['ClientIDList']:
        raise RuntimeError('Existing OIDC provider needs the sts.amazonaws.com audience.')
    owns_provider = 'aws_iam_openid_connect_provider.cicd[0]' in managed
    settings = dict(github_repository=repo_name, github_owner_id=str(repo['owner']['id']),
                    github_repository_id=str(repo['id']),
                    github_existing_oidc_provider_arn='' if owns_provider or result.returncode else provider,
                    cicd_state_bucket=bucket, cicd_state_key=key)
    config = TF / 'cicd.tfvars'
    source = config if config.exists() else TF / 'terraform.tfvars'
    text = source.read_text() if source.exists() else new_variables(region)
    if source.exists() and 'aws_db_instance.oracle' not in managed:
        print('Using retained configuration. Confirm its Oracle engine version is still orderable if planning fails.')
    config.write_text(merge_settings(text, settings))
    ci.check_sources()
    run('terraform', '-chdir=terraform', 'fmt')
    run('terraform', '-chdir=terraform', 'validate')
    plan_path = WORK / 'setup.tfplan'
    run('terraform', '-chdir=terraform', 'plan', '-input=false', '-lock-timeout=5m',
        '-var-file=cicd.tfvars', *overrides, '-out=' + str(plan_path))
    plan = json.loads(capture('terraform', '-chdir=terraform', 'show', '-json', str(plan_path)))
    guard_setup_plan(plan, region)
    database_created = any(r['address'] == 'aws_db_instance.oracle' and r['change']['actions'] == ['create']
                           for r in plan.get('resource_changes', []))
    run('terraform', '-chdir=terraform', 'apply', '-input=false', str(plan_path))
    after = outputs()
    configure_checkpoint(after, live, database_created)
    # Keep account/repository-derived deployment identifiers out of the public repository and
    # out of visible Actions variables. They are stored as protected Actions secrets instead.
    for name, value in dict(BATCH_AWS_ACCOUNT_ID=args.account,
                           BATCH_AWS_ROLE_ARN=after['cicd_role_arn'], BATCH_STATE_BUCKET=bucket,
                           BATCH_PLAN_ROLE_ARN=after['cicd_plan_role_arn'], BATCH_INFRA_ROLE_ARN=after['cicd_infra_role_arn'],
                           BATCH_PR_PLAN_ROLE_ARN=after['cicd_pr_plan_role_arn'], BATCH_STATE_KEY=key,
                           BATCH_TFVARS=config.read_text()).items():
        secret(name, value)
    for name, value in dict(BATCH_AWS_REGION=region, BATCH_PROJECT_DIR=project_dir).items():
        variable(name, value)
    variable('BATCH_CD_PAUSED', 'false')
    print('SETUP COMPLETE. Open a PR; use [deploy] in its title for the first complete application deployment.', flush=True)
    print('Keep terraform/cicd.tfvars LOCAL and ignored. Commit only terraform/.terraform.lock.hcl.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, KeyError, ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('SETUP FAILED: ' + str(error))
