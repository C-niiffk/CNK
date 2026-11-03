#!/usr/bin/env python3
"""CI orchestration around the tested manual builders, renderer and VM installer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
import sys
from remote import aws, run

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.ha-work'
DOCKERFILES = {
    'management-server': 'management-plane/management-server/Dockerfile',
    'gateway': 'gateway/Dockerfile',
    'application-service': 'application-plane/app-service/Dockerfile',
    'global-lb': 'global-lb/Dockerfile',
    'grdc': 'grdc/Dockerfile',
    'rule-sync': 'management-plane/observability/rule-sync/Dockerfile',
}
IMAGES = list(DOCKERFILES)
JAVA = set(IMAGES) - {'grdc', 'rule-sync'}

def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    path.chmod(0o600)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run_id():
    manual = os.environ.get('MP_RELEASE_ID')
    if manual is not None:
        if not re.fullmatch(r'manual-[a-f0-9]{32}', manual):
            raise ValueError('Invalid manual release ID')
        return manual
    value = os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    if not re.fullmatch(r'[0-9]+-[0-9]+', value):
        raise ValueError('Invalid GitHub run/attempt')
    return value

def source_commit():
    return os.environ.get('MP_SOURCE_COMMIT') or os.environ['GITHUB_SHA']

def s3_json(key, value=None):
    path = WORK / 'transfer.json'
    bucket = os.environ['ARTIFACT_BUCKET']
    try:
        if value is not None:
            private_json(path, value)
            aws('s3api', 'put-object', '--bucket', bucket, '--key', key,
                '--body', str(path), '--server-side-encryption', 'AES256')
            return value
        aws('s3api', 'get-object', '--bucket', bucket, '--key', key, str(path))
        return json.loads(path.read_text())
    finally:
        path.unlink(missing_ok=True)

def select(paths):
    images = set()
    infra = deploy = False
    for path in paths:
        if path == '.github/workflows/management-platform.yml':
            images.update(IMAGES); infra = True
        if path.startswith('terraform/') or path == 'management-plane/observability/deploy/ec2/es-user-data.sh':
            infra = True
        if path.startswith(('application-plane/app-service/deploy/ec2/', 'application-plane/agent-client/deploy/ec2/')) or path in [
            'docs/scripts/install-app2.sh', 'docs/scripts/configure-app2.py',
            'docs/scripts/prepare-app2-volume.sh', 'docs/scripts/bundle-app2.py']:
            images.add('application-service')  # Package changed host files into a new bundle.
        if path == 'pom.xml' or path.startswith(('platform-common/', 'application-plane/agent-client/src/')) or path == 'application-plane/agent-client/pom.xml':
            images.update(JAVA)
        elif path.startswith('management-plane/observability/rule-sync/'):
            images.add('rule-sync')
        elif path.startswith('management-plane/') and '/deploy/' not in path and not path.startswith('management-plane/observability/'):
            images.add('management-server')
        elif path.startswith('gateway/') and '/deploy/' not in path:
            images.add('gateway')
        elif path.startswith('global-lb/') and '/deploy/' not in path:
            images.add('global-lb')
        elif path.startswith('application-plane/app-service/') and '/deploy/' not in path:
            images.add('application-service')
        elif path.startswith('grdc/') and '/deploy/' not in path:
            images.add('grdc')
        if '/deploy/' in path or path.startswith(('docs/scripts/', '.github/')) or path == '.dockerignore':
            deploy = True
        if path in ['.dockerignore', 'docs/scripts/build-images.py']:
            images.update(IMAGES)
    return {'images': sorted(images), 'infra': infra, 'deploy': deploy or bool(images), 'has_images': bool(images)}


def check_images(components):
    """Build PR images without AWS credentials or changing the manual publisher."""
    if not isinstance(components, list) or set(components) - set(IMAGES):
        raise ValueError('Invalid components')
    with tempfile.TemporaryDirectory(prefix='mp-check-') as directory:
        context = Path(directory)
        for name in ['pom.xml', 'platform-common', 'global-lb', 'management-plane', 'gateway', 'grdc', 'application-plane']:
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, context / name, ignore=shutil.ignore_patterns(
                    'deploy', 'target', '.git', '.idea', '*.iml', '*.pem', '*.key', '*.tfstate*', '__pycache__'))
            else:
                shutil.copy2(source, context / name)
        for component in components:
            run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--load',
                 '-f', str(context / DOCKERFILES[component]), '-t', 'mp-check:' + component, str(context)])


def changes(base='', target='', build_all=False, rollback=''):
    if target == 'infrastructure':
        return {'images': [], 'infra': True, 'deploy': False, 'has_images': False}
    if rollback:
        return {'images': [], 'infra': False, 'deploy': True, 'has_images': False}
    paths = run(['git', 'diff', '--name-only', base, 'HEAD'], True).splitlines() if base and set(base) != {'0'} else run(['git', 'ls-files'], True).splitlines()
    result = select(paths)
    if build_all or target == 'application':
        result.update(images=list(IMAGES), deploy=True, has_images=True)
    if target == 'application':
        result['infra'] = False
    return result


def prepare(build=False):
    region = os.environ['AWS_REGION']
    config = json.loads(os.environ['TF_VARS_JSON'])
    if not isinstance(config, dict) or config.get('region', region) != region:
        raise ValueError('TF_VARS_JSON must match AWS_REGION')
    if build:
        account = aws('sts', 'get-caller-identity')['Account']
        prefix = config.get('project', 'management-platform') + '-' + config.get('environment', 'lab')
        private_json(WORK / 'deployment.json', {'region': region, 'ecr': {
            k: f'{account}.dkr.ecr.{region}.amazonaws.com/{prefix}/{k}' for k in IMAGES}})
    else:
        bucket, key = os.environ['TF_STATE_BUCKET'], os.environ['TF_STATE_KEY']
        if not re.fullmatch(r'[a-z0-9.-]{3,63}', bucket) or not key or '\n' in key:
            raise ValueError('Invalid state bucket/key')
        config['region'] = region
        private_json(ROOT / 'terraform/ci.auto.tfvars.json', config)
        backend = ROOT / 'terraform/backend.hcl'
        backend.write_text('bucket = ' + json.dumps(bucket) + '\nkey = ' + json.dumps(key)
                           + '\nregion = ' + json.dumps(region) + '\nencrypt = true\nuse_lockfile = true\n')
        backend.chmod(0o600)
    print('CI input files prepared; values omitted.')

def publish(component, tag):
    meta = json.loads((WORK / 'deployment.json').read_text())
    repo = meta['ecr'][component]
    image_digest = aws('ecr', 'describe-images', '--repository-name', repo.split('/', 1)[1],
                       '--image-ids', 'imageTag=' + tag)['imageDetails'][0]['imageDigest']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_digest):
        raise ValueError('Invalid image digest')
    item = {'component': component, 'image': repo + '@' + image_digest,
            'tag': tag, 'commit': source_commit()}
    if component == 'application-service':
        private_json(WORK / 'image-refs.json', {component: item['image']})
        # Reuse the original bundler; no second implementation of JAR extraction.
        run(['python3', str(ROOT / 'docs/scripts/bundle-app2.py'), '--version', tag])
        path = WORK / ('app2-' + tag + '.tgz')
        key = 'candidates/' + run_id() + '/app2.tgz'
        aws('s3api', 'put-object', '--bucket', os.environ['ARTIFACT_BUCKET'], '--key', key,
            '--body', str(path), '--server-side-encryption', 'AES256')
        item['bundle'] = {'key': key, 'sha256': digest(path)}
    s3_json('candidates/' + run_id() + '/' + component + '.json', item)
    print('Candidate recorded:', component, tag)

def resolve(components, rollback='', initialize=False):
    meta = json.loads(run(['terraform', '-chdir=terraform', 'output', '-json', 'deployment'], True))
    private_json(WORK / 'deployment.json', meta)
    existing = aws('s3api', 'list-objects-v2', '--bucket', os.environ['ARTIFACT_BUCKET'],
                   '--prefix', 'releases/current.json').get('Contents', [])
    current = s3_json('releases/current.json') if any(x['Key'] == 'releases/current.json' for x in existing) else None
    if rollback:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', rollback):
            raise ValueError('Invalid rollback release ID')
        release = {**s3_json('releases/history/' + rollback + '.json'), 'id': run_id(), 'rollback_of': rollback}
    else:
        if not isinstance(components, list) or set(components) - set(IMAGES):
            raise ValueError('Invalid components')
        release = {'id': run_id(), 'commit': source_commit(),
                   'images': dict(current['images']) if current else {},
                   'app2_bundle': current.get('app2_bundle') if current else None}
        for component in components:
            item = s3_json('candidates/' + run_id() + '/' + component + '.json')
            if item['component'] != component or item['commit'] != source_commit():
                raise ValueError('Candidate provenance mismatch')
            release['images'][component] = item['image']
            if component == 'application-service':
                release['app2_bundle'] = item['bundle']
    release['previous'] = current['id'] if current else None
    if os.environ.get('MP_RELEASE_ID'):
        release['execution'] = 'manual'
    if set(release['images']) != set(IMAGES) or not release.get('app2_bundle'):
        raise ValueError('First release requires build_all=true, all six images and an App2 bundle')
    for name, ref in release['images'].items():
        if not re.fullmatch(re.escape(meta['ecr'][name]) + r'@sha256:[a-f0-9]{64}', ref):
            raise ValueError('Image reference outside expected ECR repository')
    private_json(WORK / 'image-refs.json', release['images'])
    changed = initialize or not current or current['app2_bundle'] != release['app2_bundle']
    if changed:
        path = WORK / 'app2-cd.tgz'
        bundle = release['app2_bundle']
        aws('s3api', 'get-object', '--bucket', os.environ['ARTIFACT_BUCKET'], '--key', bundle['key'], str(path))
        if digest(path) != bundle['sha256']:
            raise ValueError('App2 bundle checksum mismatch')
    private_json(WORK / 'release.json', {'release': release, 'deploy_app2': changed})
    print('Resolved release', release['id'], '; current release pointer unchanged.')

def plan_files(action):
    prefix = 'plans/' + run_id()
    path = WORK / 'infra.tfplan'
    metadata = {'commit': os.environ['GITHUB_SHA'], 'state_bucket': os.environ['TF_STATE_BUCKET'],
                'state_key': os.environ['TF_STATE_KEY'],
                'config_sha256': digest(ROOT / 'terraform/ci.auto.tfvars.json')}
    if action == 'save':
        metadata['plan_sha256'] = digest(path)
        aws('s3api', 'put-object', '--bucket', os.environ['ARTIFACT_BUCKET'], '--key', prefix + '/infra.tfplan',
            '--body', str(path), '--server-side-encryption', 'AES256')
        s3_json(prefix + '/metadata.json', metadata)
        print('Saved exact plan:', prefix + '/infra.tfplan')
    else:
        saved = s3_json(prefix + '/metadata.json')
        if any(saved.get(k) != v for k, v in metadata.items()):
            raise ValueError('Commit/backend/variables changed; generate a new plan')
        aws('s3api', 'get-object', '--bucket', os.environ['ARTIFACT_BUCKET'], '--key', prefix + '/infra.tfplan', str(path))
        if digest(path) != saved['plan_sha256']:
            raise ValueError('Plan checksum mismatch')

def run_command(host,commands,timeout=1800):
 result=aws('ssm','send-command','--instance-ids',host,'--document-name','AWS-RunShellScript',
  '--parameters',json.dumps({'commands':commands,'executionTimeout':[str(timeout)]}),'--timeout-seconds','120')
 command=result['Command']['CommandId'];until=time.monotonic()+timeout+180
 while time.monotonic()<until:
  try:status=aws('ssm','get-command-invocation','--command-id',command,'--instance-id',host)
  except subprocess.CalledProcessError:
   time.sleep(5);continue
  if status['Status']=='Success':return status.get('StandardOutputContent','')
  if status['Status'] in ['Failed','Cancelled','TimedOut','Cancelling']:
   raise RuntimeError('SSM command '+command+' '+status['Status']+': '+status.get('StandardErrorContent','')[-2000:])
  time.sleep(5)
 raise TimeoutError('Inspect SSM command '+command+' on '+host)

def wait_target(meta, instance, timeout=360):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        rows = aws('elbv2', 'describe-target-health', '--target-group-arn', meta['http_targets']['app2'])['TargetHealthDescriptions']
        if any(x['Target']['Id'] == instance and x['TargetHealth']['State'] == 'healthy' for x in rows):
            return
        time.sleep(10)
    raise TimeoutError('App2 ALB target is not healthy: ' + instance)

def ssm_script(bucket, prefix, checksum, meta, site, flags):
    # Transport/bootstrap only: the SAME install-app2.sh remains the application installer.
    args = shlex.join([site, *flags])
    return '\n'.join([
        'set -euo pipefail', 'umask 077', 'export PATH=/usr/local/bin:$PATH', 'work=$(mktemp -d /var/tmp/mp-deploy-XXXXXX)',
        "trap 'rm -rf -- \"$work\"' EXIT",
        'if ! command -v aws >/dev/null; then',
        '  dnf install -y unzip curl',
        '  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.31.0.zip -o "$work/awscli.zip"',
        '  unzip -q "$work/awscli.zip" -d "$work/cli"',
        '  "$work/cli/aws/install" --update', 'fi',
        'aws s3 cp ' + shlex.quote('s3://' + bucket + '/' + prefix + '/bundle.tgz') + ' "$work/bundle.tgz" --only-show-errors',
        'echo ' + shlex.quote(checksum) + '"  $work/bundle.tgz" | sha256sum -c - >/dev/null',
        'aws s3 cp ' + shlex.quote('s3://' + bucket + '/' + prefix + '/deployment.json') + ' "$work/deployment.json" --only-show-errors',
        'tar -xzf "$work/bundle.tgz" -C "$work"',
        'aws secretsmanager get-secret-value --region ' + shlex.quote(meta['region'])
        + ' --secret-id ' + shlex.quote(meta['runtime_secret_arn']) + ' --query SecretString --output text > "$work/runtime.json"',
        'bash "$work/docs/scripts/install-app2.sh" "$work/deployment.json" "$work/runtime.json" ' + args,
    ])

def verify():
    meta = json.loads((WORK / 'deployment.json').read_text())
    pending={}
    until=time.monotonic()+360
    while time.monotonic()<until:
     pending={}
     for name in ['management-a','management-b','gateway-a','gateway-b','app2']:
      targets=aws('elbv2','describe-target-health','--target-group-arn',meta['http_targets'][name])['TargetHealthDescriptions']
      live=[x for x in targets if x['TargetHealth']['State']!='draining']
      if len(live)<(2 if name=='app2' else 1) or any(x['TargetHealth']['State']!='healthy' for x in live):
       pending[name]=[x['TargetHealth']['State'] for x in live]
     if not pending:break
     time.sleep(10)
    else:raise SystemExit('Target health gate failed: '+json.dumps(pending))
    print('PASS: both-site management/gateway targets and both App2 targets are healthy.')
    print('Business/API authorization smoke tests remain separate.')



def deploy_vm(bundle, initialize=False):
    meta = json.loads((WORK / 'deployment.json').read_text())
    bundle = Path(bundle).resolve()
    if not bundle.is_file():
        raise ValueError('Missing bundle')
    os.environ['AWS_DEFAULT_REGION'] = meta['region']
    bucket = os.environ['ARTIFACT_BUCKET']
    prefix = 'bundles/' + uuid.uuid4().hex
    checksum = digest(bundle)
    for name, path in [('bundle.tgz', bundle), ('deployment.json', WORK / 'deployment.json')]:
        aws('s3api', 'put-object', '--bucket', bucket, '--key', prefix + '/' + name,
            '--body', str(path), '--server-side-encryption', 'AES256')
    flags = ['--reuse-release'] + (['--initialize-empty'] if initialize else [])
    for site in ['a', 'b']:
        if not initialize:
            wait_target(meta, meta['app2']['b' if site == 'a' else 'a']['instance_id'], timeout=60)
        run_command(meta['app2'][site]['instance_id'], [ssm_script(bucket, prefix, checksum, meta, site, flags)])
        wait_target(meta, meta['app2'][site]['instance_id'])
        print('App2 site', site, 'ready')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('changes'); c.add_argument('--base', default=''); c.add_argument('--all', action='store_true'); c.add_argument('--target', choices=['', 'application', 'infrastructure'], default=''); c.add_argument('--rollback', default='')
    c = sub.add_parser('prepare'); c.add_argument('--build', action='store_true')
    c = sub.add_parser('publish'); c.add_argument('--component', choices=IMAGES, required=True); c.add_argument('--tag', required=True)
    c = sub.add_parser('resolve'); c.add_argument('--components', default='[]'); c.add_argument('--rollback', default=''); c.add_argument('--initialize-app2', action='store_true')
    c = sub.add_parser('check-images'); c.add_argument('--components', required=True)
    c = sub.add_parser('deploy-vm'); c.add_argument('--bundle', required=True); c.add_argument('--initialize-empty', action='store_true')
    sub.add_parser('verify')
    sub.add_parser('promote')
    c = sub.add_parser('plan'); c.add_argument('operation', choices=['save', 'restore'])
    sub.add_parser('summary')
    a = p.parse_args()
    WORK.mkdir(exist_ok=True, mode=0o700); WORK.chmod(0o700)
    if a.action == 'changes':
        result = changes(a.base, a.target, a.all, a.rollback)
        print(json.dumps(result))
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                for k, v in result.items(): f.write(k + '=' + json.dumps(v, separators=(',', ':')) + '\n')
    elif a.action == 'check-images': check_images(json.loads(a.components))
    elif a.action == 'deploy-vm': deploy_vm(a.bundle, a.initialize_empty)
    elif a.action == 'verify': verify()
    elif a.action == 'prepare': prepare(a.build)
    elif a.action == 'publish': publish(a.component, a.tag)
    elif a.action == 'resolve': resolve(json.loads(a.components), a.rollback, a.initialize_app2)
    elif a.action == 'plan': plan_files(a.operation)
    elif a.action == 'promote':
        release = json.loads((WORK / 'release.json').read_text())['release']
        if release['id'] != run_id(): raise ValueError('Wrong release run')
        s3_json('releases/history/' + release['id'] + '.json', release)
        s3_json('releases/current.json', release)
        print('Release verified and promoted:', release['id'])
    elif a.action == 'summary':
        counts = {}
        for item in json.load(sys.stdin).get('resource_changes', []):
            actions = '/'.join(item['change']['actions'])
            if actions != 'no-op': counts[actions] = counts.get(actions, 0) + 1
        message = 'Terraform resource actions: ' + json.dumps(counts)
        print(message)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f: f.write(message + '\n')

if __name__ == '__main__':
    main()
