#!/usr/bin/env python3
"""One-time CI/CD setup; reuse the existing Terraform, workflows and installers.

Setup does not commit source or approve GitHub deployments. The explicit
manual-release command can deploy recorded artifacts or build locally with --build.
Run from a trusted checkout with the authorized AWS/GitHub identities.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / 'terraform'
WORK = ROOT / '.cicd-work'
HA = ROOT / '.ha-work'
SETTINGS = WORK / 'setup.json'
CONFIG = WORK / 'variables.json'
TF_VERSION = '1.13.5'
BOOTSTRAP_TARGETS = (
    'aws_iam_openid_connect_provider.cicd_github', 'aws_iam_role.cicd_github',
    'aws_iam_role_policy_attachment.cicd_plan_read', 'aws_iam_role_policy_attachment.cicd_apply_admin',
    'aws_iam_role_policy.cicd_state', 'aws_iam_role_policy.cicd_build',
    'aws_iam_role_policy.cicd_plans', 'aws_iam_role_policy.cicd_deploy',
    'aws_s3_bucket.cicd_artifacts', 'aws_s3_bucket_versioning.cicd_artifacts',
    'aws_s3_bucket_server_side_encryption_configuration.cicd_artifacts',
    'aws_s3_bucket_public_access_block.cicd_artifacts', 'aws_s3_bucket_policy.cicd_artifacts',
)


class SetupError(Exception):
    pass


def call(args, *, body=None, show=False, missing=()):
    result = subprocess.run(args, cwd=ROOT, text=True, input=body,
                            stdout=None if show else subprocess.PIPE,
                            stderr=None if show else subprocess.PIPE)
    if result.returncode:
        error = result.stderr or ''
        if missing and any(value in error for value in missing):
            return None
        raise SetupError(error.strip() or f'{args[0]} failed ({result.returncode}).')
    return result.stdout or ''


def aws(*args, missing=()):
    value = call(['aws', '--no-cli-pager', '--output', 'json', *args], missing=missing)
    return None if value is None else json.loads(value or '{}')


def gh(endpoint, *, method='GET', value=None, missing=()):
    args = ['gh', 'api', '--method', method, endpoint]
    if value is not None:
        args += ['--input', '-']
    output = call(args, body=json.dumps(value) if value is not None else None,
                  missing=missing)
    return None if output is None else json.loads(output or '{}')


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    path.chmod(0o600)


def private_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
    path.chmod(0o600)


def load_vars(path):
    if path.suffix == '.json':
        return json.loads(path.read_text())
    try:
        import hcl2
    except ImportError as error:
        raise SetupError('Install python-hcl2 in the setup virtual environment.') from error
    with path.open() as stream:
        return hcl2.load(stream)


def backend_text(bucket, key, region):
    return ''.join(name + ' = ' + json.dumps(value) + '\n'
                   for name, value in [('bucket', bucket), ('key', key), ('region', region)]) + \
        'encrypt = true\nuse_lockfile = true\n'


def require_tools(names):
    absent = [name for name in names if not shutil.which(name)]
    if absent:
        raise SetupError('Install the required tools: ' + ', '.join(absent))


def context(args, configured=False):
    require_tools(['aws', 'gh', 'git', 'terraform'])
    version = json.loads(call(['terraform', 'version', '-json']))['terraform_version']
    if version != TF_VERSION:
        raise SetupError(f'Use Terraform {TF_VERSION}, matching the workflows and saved plans.')
    state = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    identity = aws('sts', 'get-caller-identity')
    region = args.region or state.get('region') or os.environ.get('AWS_REGION') or 'us-east-1'
    repo = args.repo or state.get('repo') or json.loads(call(
        ['gh', 'repo', 'view', '--json', 'nameWithOwner']))['nameWithOwner']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise SetupError('Use a GitHub repository in OWNER/REPOSITORY format.')
    for key, value in [('account', identity['Account']), ('region', region), ('repo', repo)]:
        if state.get(key) and state[key] != value:
            raise SetupError(f'This setup directory belongs to another {key}; use its original context.')
        state[key] = value
    if configured and not state.get('configured'):
        raise SetupError('Run configure successfully first.')
    os.environ.update(AWS_REGION=region, AWS_DEFAULT_REGION=region, AWS_PAGER='',
                      TF_IN_AUTOMATION='true', TF_INPUT='false')
    return state, identity


def operator_role(identity):
    arn = identity['Arn']
    if ':assumed-role/' in arn:
        name = arn.split(':assumed-role/', 1)[1].split('/', 1)[0]
        return aws('iam', 'get-role', '--role-name', name)['Role']['Arn']
    if re.fullmatch(r'arn:aws:iam::[0-9]{12}:role/.+', arn):
        return arn
    raise SetupError('Use the authorized IAM Identity Center / IAM role, not a root session.')


def oidc_repository_claim(repo, metadata, template, requested=None):
    if template.get('use_default') is False and template.get('include_claim_keys') != ['repo', 'context']:
        raise SetupError('Custom OIDC subject template: align bootstrap explicitly; no template was changed.')
    immutable = template.get('use_immutable_subject')
    if requested:
        immutable = requested == 'immutable'
    elif immutable is None:
        if metadata['created_at'][:10] >= '2026-07-15':
            immutable = True
        else:
            raise SetupError('For an older repository, confirm its OIDC format and pass --oidc-format legacy or immutable.')
    if immutable:
        return f"{metadata['owner']['login']}@{metadata['owner']['id']}/{metadata['name']}@{metadata['id']}"
    return repo


def rhel_image(images, root_limit=20):
    candidates = []
    for image in images:
        root = next((x.get('Ebs', {}).get('VolumeSize') for x in image.get('BlockDeviceMappings', [])
                     if x.get('DeviceName') == image.get('RootDeviceName')), None)
        if isinstance(root, int) and root <= root_limit and image.get('Architecture') == 'x86_64':
            candidates.append(image)
    if not candidates:
        raise SetupError('No matching RHEL 9 AMI fits the current 20 GiB root disk; supply reviewed existing inputs.')
    return max(candidates, key=lambda item: item['CreationDate'])['ImageId']


def new_config(state, identity):
    role = operator_role(identity)
    version = aws('rds', 'describe-db-engine-versions', '--engine', 'oracle-se2', '--default-only')
    versions = [x['EngineVersion'] for x in version['DBEngineVersions'] if x['EngineVersion'].startswith('19.')]
    if len(versions) != 1:
        raise SetupError('A single default Oracle 19c version was not found; provide a reviewed variables file.')
    options = aws('rds', 'describe-orderable-db-instance-options', '--engine', 'oracle-se2',
                  '--engine-version', versions[0], '--db-instance-class', 'db.t3.small',
                  '--license-model', 'license-included', '--vpc')['OrderableDBInstanceOptions']
    if not any(x.get('MultiAZCapable') and x.get('StorageType') == 'gp3' for x in options):
        raise SetupError('The default Oracle version/class does not support the required deployment; provide reviewed inputs.')
    images = aws('ec2', 'describe-images', '--owners', '309956199498', '--filters',
                 'Name=name,Values=RHEL-9*_HVM-*-x86_64-*', 'Name=state,Values=available',
                 'Name=architecture,Values=x86_64')['Images']
    image = rhel_image(images)
    with urllib.request.urlopen('https://checkip.amazonaws.com', timeout=15) as response:
        address = str(ipaddress.IPv4Address(response.read().decode().strip()))
    key_name = 'management-platform-lab-cicd-setup'
    key_path = HA / 'cicd-setup.pem'
    existing = aws('ec2', 'describe-key-pairs', '--key-names', key_name,
                   missing=('InvalidKeyPair.NotFound',))
    if existing is None:
        if key_path.exists():
            raise SetupError('A local setup key already exists but the AWS pair is absent; inspect before changing it.')
        result = aws('ec2', 'create-key-pair', '--key-name', key_name, '--key-type', 'rsa')
        private_text(key_path, result['KeyMaterial'])
    elif not key_path.exists():
        raise SetupError('The setup EC2 key already exists; recover its original private key or provide reviewed existing inputs.')
    state['key_path'] = str(key_path)
    return dict(region=state['region'], project='management-platform', environment='lab',
                allowed_cidrs=[address + '/32'], operator_arn=role,
                oracle_engine_version=versions[0], db_instance_class='db.t3.small',
                eks_version='1.35', single_nat_gateway=False, protect_data=True,
                deploy_ec2_apps=True, image_tag='v1', app2_ami_id=image,
                app2_ami_owner='309956199498', es_ami_id=image, es_ami_owner='309956199498',
                app2_key_name=key_name, app2_data_gib=20)


def select_inputs(args, state, identity):
    if args.new_environment and not state.get('new_environment') and (
            (TF / 'backend.hcl').exists() or CONFIG.exists()):
        raise SetupError('An existing backend was found. Preserve its variables and configure without --new-environment.')
    if args.new_environment:
        state['new_environment'] = True
    if args.vars:
        config = load_vars(Path(args.vars).resolve())
    elif CONFIG.exists():
        config = load_vars(CONFIG)
    elif (TF / 'terraform.tfvars').exists():
        config = load_vars(TF / 'terraform.tfvars')
    elif args.new_environment:
        if (TF / 'backend.hcl').exists():
            raise SetupError('An existing backend was found. Preserve its variables and configure without --new-environment.')
        config = new_config(state, identity)
        state['new_environment'] = True
    else:
        variables = {x['name']: x['value'] for x in gh(
            f"repos/{state['repo']}/actions/variables?per_page=100")['variables']}
        if not all(variables.get(name) for name in ['TF_VARS_JSON', 'TF_STATE_BUCKET', 'TF_STATE_KEY']):
            raise SetupError('Keep existing terraform.tfvars/backend.hcl, or use --new-environment for a new lab.')
        config = json.loads(variables['TF_VARS_JSON'])
        state.setdefault('state_bucket', variables['TF_STATE_BUCKET'])
        state.setdefault('state_key', variables['TF_STATE_KEY'])
    if config.get('region', state['region']) != state['region']:
        raise SetupError('The existing variables use a different region.')
    if re.search(r'REPLACE|PASTE_', json.dumps(config)):
        raise SetupError('Replace example placeholders before using this variables file.')
    required = ['allowed_cidrs', 'operator_arn', 'oracle_engine_version', 'es_ami_id']
    if any(not config.get(key) for key in required):
        raise SetupError('Missing deployment inputs: ' + ', '.join(key for key in required if not config.get(key)))
    config['region'] = state['region']
    return config


def connection_arn(args, state, config):
    arn = args.connection or state.get('connection') or config.get('cicd_connection_arn')
    if not arn:
        name = (config.get('project', 'management-platform') + '-' + config.get('environment', 'lab') + '-github')[:32]
        matches = [x for x in aws('codeconnections', 'list-connections')['Connections']
                   if x['ConnectionName'] == name and x['ProviderType'] == 'GitHub']
        if len(matches) > 1:
            raise SetupError('Multiple matching GitHub connections; pass --connection with the intended ARN.')
        arn = matches[0]['ConnectionArn'] if matches else aws('codeconnections', 'create-connection',
                '--provider-type', 'GitHub', '--connection-name', name)['ConnectionArn']
    connection = aws('codeconnections', 'get-connection', '--connection-arn', arn)['Connection']
    if connection.get('OwnerAccountId') != state['account'] or connection.get('ProviderType') != 'GitHub':
        raise SetupError('The connection must belong to this AWS account and use GitHub.')
    state['connection'] = arn
    private_json(SETTINGS, state)
    if connection['ConnectionStatus'] != 'AVAILABLE':
        print('Complete the GitHub connection authorization in the AWS console:')
        print(f"https://{state['region']}.console.aws.amazon.com/codesuite/settings/connections")
        print('Then rerun the same configure command. No Terraform deployment was started.')
        raise SystemExit(2)
    return arn


def state_backend(args, state, config):
    current = load_vars(TF / 'backend.hcl') if (TF / 'backend.hcl').exists() else {}
    if current.get('region', state['region']) != state['region']:
        raise SetupError('The existing backend belongs to another region.')
    bucket = args.state_bucket or state.get('state_bucket') or current.get('bucket')
    key = args.state_key or state.get('state_key') or current.get('key')
    prefix = config.get('project', 'management-platform') + '/' + config.get('environment', 'lab')
    if not bucket:
        if not state.get('new_environment'):
            raise SetupError('Existing deployment: retain backend.hcl or pass --state-bucket and --state-key.')
        bucket = prefix.replace('/', '-') + '-tfstate-' + state['account'] + '-' + state['region']
    key = key or prefix + '/terraform.tfstate'
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket) or not key or '\n' in key:
        raise SetupError('Invalid Terraform backend bucket/key.')
    for name, value in [('state_bucket', bucket), ('state_key', key)]:
        if state.get(name) and state[name] != value:
            raise SetupError('Refusing to move an existing setup to a different Terraform backend.')
        state[name] = value
    result = aws('s3api', 'head-bucket', '--bucket', bucket, '--expected-bucket-owner', state['account'],
                 missing=('404', 'NoSuchBucket', 'NotFound'))
    if result is None:
        if not state.get('new_environment'):
            raise SetupError('The existing state bucket is unavailable; no empty replacement was created.')
        command = ['s3api', 'create-bucket', '--bucket', bucket]
        if state['region'] != 'us-east-1':
            command += ['--create-bucket-configuration', 'LocationConstraint=' + state['region']]
        aws(*command)
        aws('s3api', 'put-bucket-encryption', '--bucket', bucket,
            '--server-side-encryption-configuration', json.dumps({'Rules': [
                {'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}))
    else:
        live = aws('s3api', 'head-object', '--bucket', bucket, '--key', key,
                   missing=('404', 'NoSuchKey', 'NotFound'))
        if state.get('new_environment') and not state.get('configured') and not state.get('bootstrap_started') and live is not None:
            raise SetupError('A platform state already exists. Reuse its original variables instead of new-lab defaults.')
        if not state.get('new_environment') and live is None:
            raise SetupError('The existing platform state key is absent; no empty replacement state was initialized.')
    aws('s3api', 'put-bucket-versioning', '--bucket', bucket,
        '--versioning-configuration', 'Status=Enabled')
    aws('s3api', 'put-public-access-block', '--bucket', bucket,
        '--public-access-block-configuration',
        'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true')
    private_text(TF / 'backend.hcl', backend_text(bucket, key, state['region']))


def read_state_object(state, key, name):
    path = WORK / name
    try:
        result = aws('s3api', 'get-object', '--bucket', state['state_bucket'],
                     '--key', key, str(path), missing=('NoSuchKey', '404', 'NotFound'))
        return json.loads(path.read_text()) if result is not None else {}
    finally:
        path.unlink(missing_ok=True)


def check_bootstrap_plan(plan):
    for item in plan.get('resource_changes', []):
        if item.get('mode', 'managed') != 'managed' or item['change']['actions'] == ['no-op']:
            continue
        if 'delete' in item['change']['actions']:
            raise SetupError('Bootstrap plan deletes resources; inspect before applying. No bootstrap apply was run.')
        address = item['address']
        if not any(address == target or address.startswith(target + '[') for target in BOOTSTRAP_TARGETS):
            raise SetupError('Bootstrap plan includes platform resources; no bootstrap apply was run.')


def bootstrap(state, config, claim):
    prefix = config.get('project', 'management-platform') + '/' + config.get('environment', 'lab')
    legacy = read_state_object(state, prefix + '/cicd-bootstrap.tfstate', 'legacy-bootstrap-state.json')
    if any(item.get('mode') == 'managed' and item.get('instances') for item in legacy.get('resources', [])):
        raise SetupError('The earlier separate CI bootstrap state still owns resources. Transfer state ownership before using this consolidated layout; no resources were recreated or removed.')
    root_state = read_state_object(state, state['state_key'], 'root-state-check.json')
    owned = any(item.get('mode') == 'managed' and item.get('type') ==
                'aws_iam_openid_connect_provider' and item.get('name') == 'cicd_github'
                and item.get('instances') for item in root_state.get('resources', []))
    if owned:
        existing = ''
    elif 'existing_github_oidc_arn' in state:
        existing = state['existing_github_oidc_arn']
    elif config.get('cicd_existing_github_oidc_arn'):
        existing = config['cicd_existing_github_oidc_arn']
    else:
        existing = next((x['Arn'] for x in aws('iam', 'list-open-id-connect-providers')
            ['OpenIDConnectProviderList'] if x['Arn'].endswith(
                ':oidc-provider/token.actions.githubusercontent.com')), '')
    config.update(enable_cicd=True, cicd_repository=state['repo'],
                  cicd_oidc_repository_claim=claim, cicd_existing_github_oidc_arn=existing,
                  cicd_state_bucket=state['state_bucket'], cicd_state_key=state['state_key'])
    # Old transport inputs are now derived from resources in the same module.
    config.pop('cicd_artifact_bucket', None)
    config.pop('cicd_deploy_role_arn', None)
    state.update(existing_github_oidc_arn=existing, bootstrap_started=True)
    private_json(CONFIG, config)
    private_json(SETTINGS, state)
    os.environ.update(TF_VARS_JSON=json.dumps(config), TF_STATE_BUCKET=state['state_bucket'],
                      TF_STATE_KEY=state['state_key'])
    call(['python3', 'docs/scripts/cicd.py', 'prepare'], show=True)
    base = ['terraform', '-chdir=terraform']
    call(base + ['init', '-input=false', '-backend-config=backend.hcl'], show=True)
    # Exceptional first-time bootstrap only. Daily/manual infrastructure changes use a full plan.
    plan_path = WORK / 'bootstrap.tfplan'
    call(base + ['plan', '-input=false', '-lock-timeout=5m', '-out=' + str(plan_path)] +
         ['-target=' + address for address in BOOTSTRAP_TARGETS], show=True)
    check_bootstrap_plan(json.loads(call(base + ['show', '-json', str(plan_path)])))
    call(base + ['apply', '-input=false', '-lock-timeout=5m', str(plan_path)], show=True)
    outputs = json.loads(call(base + ['output', '-json', 'cicd_bootstrap']))
    if not outputs:
        raise SetupError('CI bootstrap outputs are unavailable; inspect the setup apply.')
    state['artifact_bucket'] = outputs['artifact_bucket']
    state['roles'] = outputs['roles']


def configure_github(state, config, reviewer, solo):
    repo = state['repo']
    reviewer_id = gh('users/' + reviewer)['id']
    for name in ['aws-build', 'aws-terraform-plan', 'aws-terraform-apply', 'aws-application']:
        endpoint = f'repos/{repo}/environments/{name}'
        existing = gh(endpoint, missing=('HTTP 404',))
        protected = name in ['aws-terraform-apply', 'aws-application']
        if existing is None:
            value = {'deployment_branch_policy': {'protected_branches': False, 'custom_branch_policies': True}}
            if protected:
                value.update(reviewers=[{'type': 'User', 'id': reviewer_id}], prevent_self_review=not solo)
            gh(endpoint, method='PUT', value=value)
            gh(endpoint + '/deployment-branch-policies', method='POST', value={'name': 'main', 'type': 'branch'})
        else:
            policy = existing.get('deployment_branch_policy') or {}
            rules = existing.get('protection_rules', [])
            reviewed = any(x.get('type') == 'required_reviewers' and x.get('reviewers') for x in rules)
            branches = gh(endpoint + '/deployment-branch-policies').get('branch_policies', [])
            if not policy.get('custom_branch_policies') or not branches or any(
                    x['name'] != 'main' or x.get('type', 'branch') != 'branch' for x in branches):
                raise SetupError(f'Keep {name} restricted to main in GitHub Environments, then rerun configure.')
            if protected and not reviewed:
                raise SetupError(f'Add a required reviewer to {name}; existing environment protections were not overwritten.')
    protection = gh(f'repos/{repo}/branches/main/protection', missing=('HTTP 404',))
    if protection is None:
        gh(f'repos/{repo}/branches/main/protection', method='PUT', value={
            'required_status_checks': {'strict': True, 'contexts': ['checks', 'validate']},
            'enforce_admins': True, 'required_pull_request_reviews': {
                'required_approving_review_count': 0 if solo else 1,
                'dismiss_stale_reviews': True}, 'restrictions': None,
            'allow_force_pushes': False, 'allow_deletions': False})
    else:
        checks = protection.get('required_status_checks') or {}
        contexts = set(checks.get('contexts') or []) | {x['context'] for x in checks.get('checks', [])}
        if not {'checks', 'validate'}.issubset(contexts) or protection.get('required_pull_request_reviews') is None:
            raise SetupError('Keep PRs and the checks/validate status checks required on main; existing branch rules were not overwritten.')
    variables = dict(AWS_REGION=state['region'], TF_STATE_BUCKET=state['state_bucket'],
                     TF_STATE_KEY=state['state_key'], ARTIFACT_BUCKET=state['artifact_bucket'],
                     TF_VARS_JSON=json.dumps(config, separators=(',', ':')),
                     CODEBUILD_PROJECT=config.get('project', 'management-platform') + '-' +
                     config.get('environment', 'lab') + '-deploy', INFRA_ENABLED='true',
                     INFRA_APPLY_ENABLED='true')
    variables.update({'AWS_' + name.upper() + '_ROLE_ARN': arn for name, arn in state['roles'].items()})
    previous = {x['name']: x['value'] for x in gh(f'repos/{repo}/actions/variables?per_page=100')['variables']}
    for name in ['PUBLISH_ENABLED', 'DEPLOY_ENABLED']:
        variables[name] = previous.get(name, 'false')
    for name, value in variables.items():
        call(['gh', 'variable', 'set', name, '--repo', repo], body=value)


def configure(args):
    state, identity = context(args)
    if call(['git', 'rev-parse', '--show-toplevel']).strip() != str(ROOT):
        raise SetupError('Use a standalone management-platform checkout with workflows at repository root.')
    changes = call(['git', 'status', '--porcelain']).splitlines()
    generated_locks = {'terraform/.terraform.lock.hcl'}
    if any(line[3:] not in generated_locks for line in changes):
        raise SetupError('Commit/push source changes before setup; private setup files must stay ignored.')
    metadata = gh('repos/' + state['repo'])
    if gh('repos/' + state['repo'] + '/commits/main')['sha'] != call(['git', 'rev-parse', 'HEAD']).strip():
        raise SetupError('Use the current main checkout so local bootstrap and workflows refer to the same source.')
    for relative in ['.cicd-work/variables.json', '.ha-work/runtime.json', 'terraform/backend.hcl',
                     'terraform/terraform.tfvars']:
        if call(['git', 'check-ignore', relative], missing=()) == '':
            raise SetupError('Keep private setup inputs ignored before continuing.')
    config = select_inputs(args, state, identity)
    private_json(CONFIG, config)
    private_json(SETTINGS, state)
    arn = connection_arn(args, state, config)
    quotas = aws('service-quotas', 'list-service-quotas', '--service-code', 'codebuild')['Quotas']
    small = [item for item in quotas if 'linux/small' in item['QuotaName'].lower()
             and 'concurrent' in item['QuotaName'].lower()]
    if not small or any(item['Value'] < 1 for item in small):
        raise SetupError('Verify/increase the CodeBuild Linux/Small concurrent-build quota in this region before enabling CD.')
    claim = oidc_repository_claim(state['repo'], metadata,
            gh(f"repos/{state['repo']}/actions/oidc/customization/sub"),
            args.oidc_format or state.get('oidc_format'))
    state['oidc_format'] = 'immutable' if '@' in claim else 'legacy'
    state_backend(args, state, config)
    config.update(enable_cicd=True, cicd_enable_webhook=True, cicd_repository=state['repo'],
                  cicd_connection_arn=arn)
    bootstrap(state, config, claim)
    private_json(CONFIG, config)
    reviewer = args.reviewer or state.get('reviewer')
    solo = args.solo or state.get('solo', False)
    if solo and not reviewer:
        reviewer = gh('user')['login']
    if not reviewer:
        raise SetupError('Select --solo for the personal lab, or --reviewer LOGIN for a team.')
    configure_github(state, config, reviewer, solo)
    state.update(configured=True, reviewer=reviewer, solo=solo)
    private_json(SETTINGS, state)
    print('SETUP COMPLETE. GitHub configuration and Terraform backends are ready.')
    print('Review .cicd-work/variables.json locally. Keep .cicd-work and .ha-work outside Git.')
    print('Next: gh workflow run management-platform.yml --ref main -f target=infrastructure -f apply=true')


def prepare_local(state):
    config = json.loads(CONFIG.read_text())
    os.environ.update(TF_VARS_JSON=json.dumps(config), TF_STATE_BUCKET=state['state_bucket'],
                      TF_STATE_KEY=state['state_key'], ARTIFACT_BUCKET=state['artifact_bucket'])
    call(['python3', 'docs/scripts/cicd.py', 'prepare'], show=True)
    call(['terraform', '-chdir=terraform', 'init', '-input=false', '-backend-config=backend.hcl'], show=True)
    return config


def check_plan_metadata(metadata, plan, state, head_sha, config_file):
    expected = dict(commit=head_sha, state_bucket=state['state_bucket'], state_key=state['state_key'],
                    config_sha256=hashlib.sha256(config_file.read_bytes()).hexdigest(),
                    plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest())
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise SetupError('Saved plan commit/backend/configuration/checksum mismatch; create and review a fresh run.')


def review_plan(args):
    state, _ = context(args, configured=True)
    prepare_local(state)
    if args.run:
        run = gh(f"repos/{state['repo']}/actions/runs/{args.run}")
    else:
        runs = gh(f"repos/{state['repo']}/actions/workflows/management-platform.yml/runs?branch=main&per_page=20")['workflow_runs']
        run = None
        for candidate in runs:
            if candidate['status'] == 'completed':
                continue
            jobs = gh(f"repos/{state['repo']}/actions/runs/{candidate['id']}/attempts/{candidate['run_attempt']}/jobs?per_page=100")['jobs']
            if any(job['name'] == 'plan' and job['conclusion'] == 'success' for job in jobs):
                run = candidate
                break
        if run is None:
            raise SetupError('Wait for an active main run to finish its plan job, or select an explicit --run ID.')
    workflow = gh(f"repos/{state['repo']}/actions/workflows/management-platform.yml")
    if run['head_branch'] != 'main' or run['workflow_id'] != workflow['id']:
        raise SetupError('Select a Terraform workflow run on main.')
    jobs = gh(f"repos/{state['repo']}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")['jobs']
    if not any(x['name'] == 'plan' and x['conclusion'] == 'success' for x in jobs):
        raise SetupError('Wait for the plan job in this run to succeed before reviewing its saved plan.')
    prefix = f"s3://{state['artifact_bucket']}/plans/{run['id']}-{run['run_attempt']}/"
    plan, metadata = WORK / 'review.tfplan', WORK / 'review-plan.json'
    call(['aws', 's3', 'cp', prefix + 'infra.tfplan', str(plan), '--only-show-errors'])
    call(['aws', 's3', 'cp', prefix + 'metadata.json', str(metadata), '--only-show-errors'])
    plan.chmod(0o600)
    metadata.chmod(0o600)
    check_plan_metadata(json.loads(metadata.read_text()), plan, state, run['head_sha'], TF / 'ci.auto.tfvars.json')
    call(['terraform', '-chdir=terraform', 'show', str(plan)], show=True)
    print('Approve only this reviewed run after the plan job succeeds: ' + run['html_url'])


def wait_ssm(instance_ids):
    until = time.monotonic() + 900
    while time.monotonic() < until:
        items = aws('ssm', 'describe-instance-information', '--filters',
                    'Key=InstanceIds,Values=' + ','.join(instance_ids))['InstanceInformationList']
        if {x['InstanceId'] for x in items if x['PingStatus'] == 'Online'} == set(instance_ids):
            return
        time.sleep(10)
    raise SetupError('SSM agents are not online; inspect instance/cloud-init status before initialization.')


def initialization_needed(state, database_identity):
    marker = state.get('database_initialization', {})
    if marker.get('database') == database_identity:
        if marker['status'] == 'complete':
            return False
        raise SetupError('A previous SQL initialization did not finish. Inspect/repair it before rerunning SQL manually.')
    return True


def initialize(args):
    state, _ = context(args, configured=True)
    require_tools(['kubectl', 'helm', 'session-manager-plugin', 'ssh', 'scp'])
    prepare_local(state)
    deployment = json.loads(call(['terraform', '-chdir=terraform', 'output', '-json', 'deployment']))
    cicd = json.loads(call(['terraform', '-chdir=terraform', 'output', '-json', 'cicd']))
    if not cicd or cicd['artifact_bucket'] != state['artifact_bucket']:
        raise SetupError('Complete the Terraform apply before initialization.')
    private_json(HA / 'deployment.json', deployment)
    call(['aws', 'eks', 'update-kubeconfig', '--name', deployment['cluster_name'], '--region', state['region']], show=True)
    call(['kubectl', 'get', 'nodes'], show=True)
    instances = [x['instance_id'] for x in deployment['app2'].values()] + list(deployment['es_instances'].values())
    wait_ssm(instances)
    if state.get('new_environment'):
        from remote import run_command
        for instance in instances:
            run_command(instance, ['set -e', 'cloud-init status --wait'])
        for instance in deployment['es_instances'].values():
            run_command(instance, ['set -e', 'systemctl is-active elasticsearch',
                                  'curl -fsS http://127.0.0.1:9200/_cluster/health'])
        call(['python3', 'docs/scripts/deploy.py', 'secrets'], show=True)
        call(['python3', 'docs/scripts/deploy.py', 'lbc'], show=True)
        database = deployment['db']['management']['secret_arn']
        if initialization_needed(state, database):
            key = Path(args.key or state.get('key_path', ''))
            if not key.is_file():
                raise SetupError('Provide the existing first-setup private key with --key PATH.')
            state['database_initialization'] = {'database': database, 'status': 'started'}
            private_json(SETTINGS, state)
            call(['python3', 'docs/scripts/init-db.py', '--key', str(key)], show=True)
            state['database_initialization']['status'] = 'complete'
            private_json(SETTINGS, state)
    else:
        call(['python3', 'docs/scripts/deploy.py', 'secrets', '--existing-only'], show=True)
        call(['kubectl', '-n', 'kube-system', 'rollout', 'status',
              'deployment/aws-load-balancer-controller', '--timeout=600s'], show=True)
    for name in ['PUBLISH_ENABLED', 'DEPLOY_ENABLED']:
        call(['gh', 'variable', 'set', name, '--repo', state['repo']], body='true')
    state['initialized'] = True
    private_json(SETTINGS, state)
    print('INITIALIZATION COMPLETE. Existing credentials were preserved; normal releases use the workflows.')


def release(args):
    state, _ = context(args, configured=True)
    if not state.get('initialized'):
        raise SetupError('Complete initialize before releasing the application.')
    existing = aws('s3api', 'head-object', '--bucket', state['artifact_bucket'], '--key',
                   'releases/current.json', missing=('404', 'NoSuchKey', 'NotFound'))
    if args.first and existing is not None:
        raise SetupError('An application release already exists; use a normal release without --first.')
    if not args.first and existing is None:
        raise SetupError('Use release --first for the first complete application deployment.')
    call(['gh', 'workflow', 'run', 'management-platform.yml', '--repo', state['repo'], '--ref', 'main',
          '-f', 'target=application', '-f', 'build_all=true', '-f', 'initialize_app2=' + ('true' if args.first else 'false')], show=True)
    print(f"Review the application run and approve its deployment: https://github.com/{state['repo']}/actions")


def manual_release(args):
    """Out-of-band operator deployment using the same builders, installers and release records."""
    state, _ = context(args, configured=True)
    require_tools(['kubectl'])
    if not state.get('initialized'):
        raise SetupError('Complete initialize before a manual application deployment.')
    # Leave automatic CD paused on either success or failure until the operator resumes it.
    call(['gh', 'variable', 'set', 'DEPLOY_ENABLED', '--repo', state['repo']], body='false')
    runs = gh(f"repos/{state['repo']}/actions/workflows/management-platform.yml/runs?branch=main&per_page=100")['workflow_runs']
    if any(item['status'] != 'completed' for item in runs):
        raise SetupError('Automatic CD is paused. Finish/cancel pending main deployment runs before manual deployment.')
    prepare_local(state)
    deployment = json.loads(call(['terraform', '-chdir=terraform', 'output', '-json', 'deployment']))
    private_json(HA / 'deployment.json', deployment)
    current = aws('s3api', 'head-object', '--bucket', state['artifact_bucket'], '--key',
                  'releases/current.json', missing=('404', 'NoSuchKey', 'NotFound'))
    if current is None and (not args.build or not args.initialize_app2):
        raise SetupError('The first manual release requires --build --initialize-app2.')
    if current is not None and args.initialize_app2:
        raise SetupError('--initialize-app2 is reserved for the first release; existing data volumes are retained.')
    os.environ['MP_RELEASE_ID'] = 'manual-' + uuid.uuid4().hex
    os.environ['MP_SOURCE_COMMIT'] = call(['git', 'rev-parse', 'HEAD']).strip()
    if args.build:
        require_tools(['docker', 'mvn', 'java'])
        if call(['git', 'status', '--porcelain']).strip():
            raise SetupError('Commit the manual build source first so the release records its exact commit.')
        call(['docker', 'info'], show=True)
        call(['mvn', '-B', '-ntp', 'verify'], show=True)
        call(['python3', 'docs/scripts/build-images.py', '--tag', os.environ['MP_RELEASE_ID']], show=True)
        import cicd
        for component in cicd.IMAGES:
            call(['python3', 'docs/scripts/cicd.py', 'publish', '--component', component,
                  '--tag', os.environ['MP_RELEASE_ID']], show=True)
        resolve = ['--components', json.dumps(cicd.IMAGES)]
    else:
        resolve = ['--rollback', args.release]
    if args.initialize_app2:
        resolve += ['--initialize-app2']
    call(['python3', 'docs/scripts/cicd.py', 'resolve', *resolve], show=True)
    call(['aws', 'eks', 'update-kubeconfig', '--name', deployment['cluster_name'],
          '--region', state['region']], show=True)
    call(['python3', 'docs/scripts/deploy.py', 'secrets', '--existing-only'], show=True)
    call(['python3', 'docs/scripts/deploy.py', 'apply'], show=True)
    if json.loads((HA / 'release.json').read_text())['deploy_app2']:
        command = ['python3', 'docs/scripts/cicd.py', 'deploy-vm', '--bundle', str(HA / 'app2-cd.tgz')]
        if args.initialize_app2:
            command += ['--initialize-empty']
        call(command, show=True)
    call(['python3', 'docs/scripts/cicd.py', 'verify'], show=True)
    call(['python3', 'docs/scripts/cicd.py', 'promote'], show=True)
    print('MANUAL RELEASE COMPLETE. Automatic CD remains paused.')
    print('Resume when ready: gh variable set DEPLOY_ENABLED --body true')


def status(args):
    state, _ = context(args, configured=True)
    call(['gh', 'run', 'list', '--repo', state['repo'], '--limit', '6'], show=True)
    if (HA / 'deployment.json').exists():
        print('Application URL: ' + json.loads((HA / 'deployment.json').read_text())['public_url'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ['configure', 'review-plan', 'initialize', 'release', 'manual-release', 'status']:
        command = sub.add_parser(name)
        command.add_argument('--repo')
        command.add_argument('--region')
        if name == 'configure':
            command.add_argument('--new-environment', action='store_true')
            command.add_argument('--vars', help='Existing authoritative Terraform variables; HCL or JSON')
            command.add_argument('--solo', action='store_true', help='Personal lab; self-review remains an explicit GitHub approval')
            command.add_argument('--reviewer')
            command.add_argument('--connection')
            command.add_argument('--state-bucket')
            command.add_argument('--state-key')
            command.add_argument('--oidc-format', choices=['legacy', 'immutable'])
        elif name == 'review-plan':
            command.add_argument('--run', type=int)
        elif name == 'initialize':
            command.add_argument('--key')
        elif name == 'release':
            command.add_argument('--first', action='store_true')
        elif name == 'manual-release':
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument('--release', help='An existing recorded release ID to deploy manually')
            source.add_argument('--build', action='store_true', help='Build/test/publish locally before deployment')
            command.add_argument('--initialize-app2', action='store_true', help='First release: prepare empty VM data volumes')
    args = parser.parse_args()
    os.umask(0o077)
    for path in [WORK, HA]:
        path.mkdir(exist_ok=True, mode=0o700)
        path.chmod(0o700)
    try:
        globals()[args.action.replace('-', '_')](args)
    except SetupError as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
