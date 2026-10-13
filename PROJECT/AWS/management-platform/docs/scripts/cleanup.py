#!/usr/bin/env python3
"""Management Platform lab cleanup. Run stages in order; never delete unrelated resources.

Requires: aws CLI, terraform, kubectl, helm, jq; existing .ha-work/deployment.json.
No stage silently skips a failed operation. Backups may contain secrets.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.ha-work'
TF = ROOT / 'terraform'
DEPLOY = WORK / 'deployment.json'
CONTEXT = WORK / 'cleanup-context.json'
INVENTORY = WORK / 'cleanup-inventory.json'
NAMESPACES = ('platform', 'gateway', 'grdc', 'applications', 'observability')


def run(*args, capture=False, check=True):
    print('+', ' '.join(map(str, args)), flush=True)
    p = subprocess.run(list(map(str, args)), text=True, capture_output=capture, check=check)
    return p.stdout.strip() if capture else ''


def aws(*args, json_output=True):
    cmd = ['aws', '--no-cli-pager', '--output', 'json' if json_output else 'text', *args]
    out = run(*cmd, capture=True)
    return json.loads(out) if json_output and out else ({} if json_output else out)


def tf(*args, capture=False):
    return run('terraform', f'-chdir={TF}', *args, capture=capture)


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + '\n')
    path.chmod(0o600)


def load(path):
    if not path.exists():
        raise RuntimeError(f'Missing {path}. Run prepare first or restore deployment metadata.')
    return json.loads(path.read_text())


def check_identity(meta, expected):
    ident = aws('sts', 'get-caller-identity')
    if ident['Account'] != expected:
        raise RuntimeError(f'Account mismatch: actual={ident["Account"]}, expected={expected}')
    region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    if region != meta['region']:
        raise RuntimeError(f'Region mismatch: {region!r} != {meta["region"]!r}; export AWS_REGION and AWS_DEFAULT_REGION')
    if meta['name'] != os.environ.get('MP_NAME'):
        raise RuntimeError('Set MP_NAME to the exact deployment name from deployment.json')
    return ident


def confirm(args, meta):
    if args.confirm_project != meta['name']:
        raise RuntimeError(f'Explicit confirmation required: --confirm-project {meta["name"]}')


def context_checked(meta):
    ctx = load(CONTEXT)
    check_identity(meta, ctx['account'])
    if ctx['name'] != meta['name'] or ctx['region'] != meta['region']:
        raise RuntimeError('Cleanup context mismatch')
    return ctx


def prepare(meta, args):
    expected = os.environ.get('MP_EXPECTED_ACCOUNT_ID')
    if not expected or not re.fullmatch(r'\d{12}', expected):
        raise RuntimeError('Export MP_EXPECTED_ACCOUNT_ID (12 digits) from an independently verified AWS account ID')
    ident = check_identity(meta, expected)
    workspace = tf('workspace', 'show', capture=True)
    if workspace != 'default':
        raise RuntimeError('Only the default Terraform workspace is supported')
    backend_file = TF / '.terraform/terraform.tfstate'
    backend = load(backend_file)['backend']
    if backend['type'] != 's3':
        raise RuntimeError('Expected S3 Terraform backend')
    cfg = backend['config']
    if cfg['key'] != 'management-platform/lab/terraform.tfstate' or cfg['region'] != meta['region']:
        raise RuntimeError('Unexpected Terraform state key or region')
    state = json.loads(tf('state', 'pull', capture=True))
    save(WORK / 'terraform-before-delete.tfstate', state)
    ec2_ids = list(meta['es_instances'].values()) + [v['instance_id'] for v in meta['app2'].values()]
    instances = aws('ec2', 'describe-instances', '--instance-ids', *ec2_ids)
    disks = [dict(instance=i['InstanceId'], volume=b['Ebs']['VolumeId'],
                  delete_on_termination=b['Ebs']['DeleteOnTermination'])
             for res in instances['Reservations'] for i in res['Instances']
             for b in i['BlockDeviceMappings'] if 'Ebs' in b]
    pv = json.loads(run('kubectl', 'get', 'pv', '-o', 'json', capture=True))
    pvs = [dict(name=x['metadata']['name'], volume=x['spec'].get('csi', {}).get('volumeHandle'),
                policy=x['spec'].get('persistentVolumeReclaimPolicy'))
           for x in pv['items'] if x['spec'].get('claimRef', {}).get('namespace') == 'observability']
    save(INVENTORY, {'name': meta['name'], 'disks': disks, 'pvs': pvs})
    save(CONTEXT, {'account': ident['Account'], 'name': meta['name'], 'region': meta['region'],
                   'bucket': cfg['bucket'], 'key': cfg['key'], 'workspace': workspace,
                   'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    print('PREPARED. No AWS resources have been changed. Review cleanup-inventory.json.')


def stop(meta):
    # Stop native services; avoid deleting compute before Kubernetes finalizers complete.
    for site in ('a', 'b'):
        iid = meta['app2'][site]['instance_id']
        command = aws('ssm', 'send-command', '--instance-ids', iid,
                      '--document-name', 'AWS-RunShellScript',
                      '--parameters', json.dumps({'commands': ['systemctl stop app2 filebeat-a filebeat-b node-exporter']}))
        cid = command['Command']['CommandId']
        run('aws', 'ssm', 'wait', 'command-executed', '--command-id', cid, '--instance-id', iid)
        invocation = aws('ssm', 'get-command-invocation', '--command-id', cid, '--instance-id', iid)
        if invocation.get('Status') != 'Success':
            raise RuntimeError(f'SSM stop failed on {iid}: {invocation.get("Status")}')
    run('kubectl', '-n', 'platform', 'scale', 'deployment', 'routing-a', 'routing-b', '--replicas=0')
    for ns in NAMESPACES:
        run('kubectl', '-n', ns, 'delete', 'targetgroupbindings', '--all', '--wait=true', '--timeout=300s')
    remaining = json.loads(run('kubectl', 'get', 'targetgroupbindings', '-A', '-o', 'json', capture=True))
    if remaining['items']:
        raise RuntimeError('TargetGroupBindings still exist; keep load balancer controller running')
    inv = load(INVENTORY)
    for pv in inv['pvs']:
        run('kubectl', 'patch', 'pv', pv['name'], '--type=merge',
            '-p', '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}')
    for ns in NAMESPACES:
        run('kubectl', 'delete', 'namespace', ns, '--wait=true', '--timeout=600s')
    for pv in inv['pvs']:
        probe = subprocess.run(['kubectl', 'get', 'pv', pv['name']], capture_output=True)
        if probe.returncode == 0:
            run('kubectl', 'wait', '--for=delete', f'pv/{pv["name"]}', '--timeout=600s')
    for kind, name in [('clusterrole', 'platform-prometheus'),
                       ('clusterrolebinding', 'platform-prometheus'),
                       ('storageclass', 'platform-gp3')]:
        run('kubectl', 'delete', kind, name, '--ignore-not-found')
    run('helm', 'uninstall', 'aws-load-balancer-controller', '-n', 'kube-system')
    print('STOPPED. Project namespaces and target bindings removed.')


def ecr(meta):
    """Empty ECR safely: delete manifest lists / OCI indexes before child images.

    Buildx images often include child manifests referenced by a parent index.
    Removing children first raises ImageReferencedByManifestList.
    """
    parent_types = {
        'application/vnd.docker.distribution.manifest.list.v2+json',
        'application/vnd.oci.image.index.v1+json',
    }
    for url in meta['ecr'].values():
        repo = url.split('/', 1)[1]
        for attempt in range(20):
            images = aws('ecr', 'describe-images', '--repository-name', repo).get('imageDetails', [])
            if not images:
                print('Emptied:', repo)
                break
            parents = [i for i in images if i.get('imageManifestMediaType') in parent_types]
            # Parent manifests first; deleting them releases their referenced child manifests.
            # If ECR does not report a parent type, tagged manifests are tried before
            # untagged children. Retry only dependency failures after actual progress.
            ordered = parents + [i for i in images if i not in parents and i.get('imageTags')] + [
                i for i in images if i not in parents and not i.get('imageTags')]
            progress = 0
            deferred = []
            for start in range(0, len(ordered), 100):
                batch = [{'imageDigest': i['imageDigest']} for i in ordered[start:start + 100]]
                result = aws('ecr', 'batch-delete-image', '--repository-name', repo,
                             '--image-ids', json.dumps(batch))
                failures = result.get('failures', [])
                unexpected = [f for f in failures
                              if f.get('failureCode') != 'ImageReferencedByManifestList']
                if unexpected:
                    raise RuntimeError(f'ECR delete failed for {repo}: {unexpected}')
                progress += len(result.get('imageIds', []))
                deferred.extend(failures)
            if not progress:
                raise RuntimeError(f'No ECR deletion progress for {repo}: {deferred}')
            if deferred:
                print(f'Retrying {len(deferred)} child manifest(s) after parent removal in {repo}.')
        else:
            raise RuntimeError(f'ECR cleanup exceeded retry limit for {repo}')
    print('ECR EMPTIED. Repositories will be deleted by Terraform.')


def approve_plan(label):
    if not sys.stdin.isatty():
        raise RuntimeError(f'{label}: interactive terminal required for plan approval')
    answer = input(f'Type APPLY {label} to execute the reviewed Terraform plan: ').strip()
    if answer != 'APPLY ' + label:
        raise RuntimeError('Terraform apply cancelled; no plan was applied')


def database(meta):
    # Already deleted databases cannot be unprotected; leave completed cleanup intact.
    db = aws_optional('rds', 'describe-db-instances', '--db-instance-identifier', f'{meta["name"]}-management')
    if db is None:
        print('DATABASE ALREADY REMOVED: skipping unprotect; continue with destroy/extras.')
        return
    # Apply configuration change first; final snapshot intentionally retained for separate review.
    snapshot = f'{meta["name"]}-management-final-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}'
    override = TF / 'cleanup.auto.tfvars'
    if override.exists():
        print('Reusing existing cleanup override:', override)
        tf('plan', '-out=cleanup-unprotect.tfplan')
        tf('show', '-no-color', 'cleanup-unprotect.tfplan')
        approve_plan('UNPROTECT')
        tf('apply', 'cleanup-unprotect.tfplan')
        print('DATABASE READY: deletion protection update applied.')
        return
    override.write_text('protect_data = false\nfinal_snapshot_identifiers = {\n'
                        f'  management = "{snapshot}"\n}}\n')
    save(WORK / 'final-snapshot.json', {'name': snapshot})
    print('Created cleanup.auto.tfvars; inspect Terraform plan before applying.')
    tf('plan', '-out=cleanup-unprotect.tfplan')
    tf('show', '-no-color', 'cleanup-unprotect.tfplan')
    approve_plan('UNPROTECT')
    tf('apply', 'cleanup-unprotect.tfplan')
    print('DATABASE READY: deletion protection update applied.')


def destroy(meta):
    status = aws_optional('rds', 'describe-db-instances', '--db-instance-identifier', f'{meta["name"]}-management')
    if status is not None and status['DBInstances'][0].get('DeletionProtection'):
        raise RuntimeError('RDS deletion protection still enabled. Apply reviewed unprotect plan first.')
    ec2_file = TF / 'ec2.tf'
    src = ec2_file.read_text()
    # Do not blindly edit HCL; user must explicitly change lifecycle in the named resource.
    marker = re.search(r'resource\s+"aws_ebs_volume"\s+"app2_data"\s*\{', src)
    if not marker:
        raise RuntimeError('Cannot locate aws_ebs_volume.app2_data in terraform/ec2.tf')
    block = src[marker.end():]
    next_resource = re.search(r'\nresource\s+"', block)
    relevant = block[:next_resource.start()] if next_resource else block
    if re.search(r'prevent_destroy\s*=\s*true', relevant):
        # Preserve a local copy; refuse ambiguous edits. This changes only the app2_data block.
        matches = list(re.finditer(r'prevent_destroy\s*=\s*true', relevant))
        if len(matches) != 1:
            raise RuntimeError('Ambiguous app2_data lifecycle configuration; edit manually')
        before = src[:marker.end()]
        after = src[marker.end():]
        at = matches[0]
        changed = relevant[:at.start()] + re.sub(r'true$', 'false', relevant[at.start():at.end()]) + relevant[at.end():]
        backup = WORK / 'ec2.tf.before-cleanup'
        if not backup.exists():
            backup.write_text(src)
            backup.chmod(0o600)
        ec2_file.write_text(before + changed + after[len(relevant):])
        print('Disabled prevent_destroy only for app2_data; backup saved.')
    # If a previous destroy already removed the DB, clear its orphaned backups/option groups.
    if status is None:
        cleanup_rds_residuals(meta)
    tf('plan', '-destroy', '-out=cleanup-destroy.tfplan')
    tf('show', '-no-color', 'cleanup-destroy.tfplan')
    approve_plan('DESTROY')
    proc = subprocess.run(['terraform', f'-chdir={TF}', 'apply', 'cleanup-destroy.tfplan'],
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(proc.stdout, end='', flush=True)
    if proc.returncode:
        if 'InvalidOptionGroupStateFault' not in proc.stdout:
            raise RuntimeError('Terraform destroy failed; state preserved. Review output before retrying.')
        # Terraform may have deleted the DB but failed to delete its option group.
        db_after = aws_optional('rds', 'describe-db-instances', '--db-instance-identifier', f'{meta["name"]}-management')
        if db_after is not None:
            raise RuntimeError('RDS still exists; do not remove snapshots while database is present. Rerun destroy later.')
        print('RECOVERY: RDS gone; removing dedicated final snapshots and option groups.')
        cleanup_rds_residuals(meta)
        print('RECOVERY: building a FRESH destroy plan (old plan is invalid after partial apply).')
        tf('plan', '-destroy', '-out=cleanup-destroy-retry.tfplan')
        tf('show', '-no-color', 'cleanup-destroy-retry.tfplan')
        approve_plan('DESTROY RETRY')
        tf('apply', 'cleanup-destroy-retry.tfplan')
    print('DESTROYED: Terraform apply completed.')


def aws_optional(*args):
    p = subprocess.run(['aws', '--no-cli-pager', '--output', 'json', *args], text=True, capture_output=True)
    if p.returncode == 0:
        return json.loads(p.stdout) if p.stdout.strip() else {}
    err = p.stderr
    if any(t in err for t in ('InvalidVolume.NotFound', 'DBSnapshotNotFound', 'DBInstanceNotFound',
                             'ResourceNotFoundException', 'RepositoryNotFoundException',
                             'InvalidKeyPair.NotFound', 'InvalidGroup.NotFound', 'Cluster not found')):
        return None
    raise RuntimeError('AWS query failed: ' + ' '.join(args[:2]) + ': ' + err)


def project_log_groups(meta):
    prefixes = [f'/aws/eks/{meta["cluster_name"]}',
                f'/aws/rds/instance/{meta["name"]}-management']
    names = []
    for prefix in prefixes:
        result = aws('logs', 'describe-log-groups', '--log-group-name-prefix', prefix)
        names.extend(g['logGroupName'] for g in result.get('logGroups', [])
                     if g['logGroupName'] == prefix or g['logGroupName'].startswith(prefix + '/'))
    return sorted(set(names))


def project_snapshots(meta):
    result = aws('rds', 'describe-db-snapshots', '--snapshot-type', 'manual',
                 '--db-instance-identifier', meta['name'] + '-management')
    return [x['DBSnapshotIdentifier'] for x in result.get('DBSnapshots', [])]


def project_option_groups(meta):
    """Only the project's generated Oracle 19 option groups, never defaults/shared groups."""
    result = aws('rds', 'describe-option-groups')
    prefix = meta['name'] + '-'
    return [g['OptionGroupName'] for g in result.get('OptionGroupsList', [])
            if g['OptionGroupName'].startswith(prefix)
            and g.get('OptionGroupDescription') == 'Managed by Terraform'
            and g.get('EngineName') == 'oracle-se2'
            and g.get('MajorEngineVersion') == '19']


def cleanup_rds_residuals(meta):
    """Only after the management DB is absent: purge its manual snapshots, then option groups."""
    db = aws_optional('rds', 'describe-db-instances', '--db-instance-identifier', meta['name'] + '-management')
    if db is not None:
        raise RuntimeError('Refusing RDS snapshot cleanup while the management DB exists')
    snaps = project_snapshots(meta)
    for snap in snaps:
        print('Deleting project RDS snapshot:', snap)
        aws('rds', 'delete-db-snapshot', '--db-snapshot-identifier', snap)
    for snap in snaps:
        print('Waiting for snapshot deletion:', snap)
        run('aws', '--no-cli-pager', 'rds', 'wait', 'db-snapshot-deleted',
            '--db-snapshot-identifier', snap)
    cleanup_option_groups(meta)


def cleanup_option_groups(meta):
    for name in project_option_groups(meta):
        # RDS may still hold an internal reference briefly after DB deletion.
        # Never remove the Terraform state entry to conceal an API failure.
        import time
        for attempt in range(6):
            proc = subprocess.run(['aws', '--no-cli-pager', 'rds', 'delete-option-group',
                                   '--option-group-name', name], capture_output=True, text=True)
            if proc.returncode == 0 or 'OptionGroupNotFoundFault' in proc.stderr:
                print('Deleted option group:', name)
                break
            if 'InvalidOptionGroupStateFault' in proc.stderr and attempt < 5:
                print(f'Option group still in use; retry {attempt + 1}/5 in 30 seconds:', name)
                time.sleep(30)
                continue
            raise RuntimeError(f'Option group deletion failed for {name}: {proc.stderr.strip()}')


def extras(meta):
    # Snapshot references must be removed BEFORE deleting orphaned option groups.
    cleanup_rds_residuals(meta)
    inv = load(INVENTORY)
    # Exact instance disk inventory, including retained ES root disks and CSI volumes.
    vids = set(x['volume'] for x in inv['disks']) | set(x['volume'] for x in inv['pvs'] if x.get('volume'))
    for vid in sorted(vids):
        result = aws_optional('ec2', 'describe-volumes', '--volume-ids', vid)
        if result is None:
            continue
        vol = result['Volumes'][0]
        if vol['State'] != 'available' or vol.get('Attachments'):
            raise RuntimeError(f'Retained EBS {vid} is not detached; stopping')
        aws('ec2', 'delete-volume', '--volume-id', vid)
        print('Deleted EBS:', vid)
    for group in project_log_groups(meta):
        aws('logs', 'delete-log-group', '--log-group-name', group)
        print('Deleted log group:', group)
    # The key was created manually by deployment instructions, not Terraform.
    key = meta.get('app2', {}).get('a', {}).get('key_name') or 'management-platform-lab'
    if key != 'management-platform-lab':
        raise RuntimeError('Unexpected EC2 key pair name; refusing automatic deletion')
    aws_optional('ec2', 'delete-key-pair', '--key-name', key)
    # Terraform-managed secrets can be scheduled for deletion. Do not force-delete shared secrets.
    for field in ('runtime_secret_arn', 'redis_secret_arn'):
        arn = meta.get(field)
        if not arn:
            continue
        secret = aws_optional('secretsmanager', 'describe-secret', '--secret-id', arn)
        if secret is not None and not secret.get('DeletedDate'):
            aws('secretsmanager', 'delete-secret', '--secret-id', arn, '--force-delete-without-recovery')
    print('EXTRAS REQUESTED: retained EBS, project RDS manual snapshots, logs, key and dedicated secrets.')
    print('Wait for asynchronous AWS deletion before running verify.')


def verify(meta):
    state = json.loads(tf('state', 'pull', capture=True))
    managed = [r for r in state.get('resources', []) if r.get('mode', 'managed') == 'managed' and r.get('instances')]
    if managed:
        raise RuntimeError(f'Terraform state still contains {len(managed)} managed resources')
    issues = []
    inv = load(INVENTORY)
    for vid in set(x['volume'] for x in inv['disks']) | set(x['volume'] for x in inv['pvs'] if x.get('volume')):
        if aws_optional('ec2', 'describe-volumes', '--volume-ids', vid) is not None:
            issues.append('EBS ' + vid)
    if aws_optional('eks', 'describe-cluster', '--name', meta['cluster_name']) is not None:
        issues.append('EKS cluster')
    if aws_optional('rds', 'describe-db-instances', '--db-instance-identifier', meta['name'] + '-management') is not None:
        issues.append('RDS instance')
    if project_snapshots(meta):
        issues.append('RDS manual snapshots')
    if project_option_groups(meta):
        issues.append('RDS project option groups')
    if project_log_groups(meta):
        issues.append('CloudWatch project logs')
    for url in meta['ecr'].values():
        repo = url.split('/', 1)[1]
        if aws_optional('ecr', 'describe-repositories', '--repository-names', repo) is not None:
            issues.append('ECR ' + repo)
    for iid in list(meta['es_instances'].values()) + [x['instance_id'] for x in meta['app2'].values()]:
        result = aws_optional('ec2', 'describe-instances', '--instance-ids', iid)
        if result and any(i['State']['Name'] not in ('terminated',) for r in result['Reservations'] for i in r['Instances']):
            issues.append('EC2 ' + iid)
    for field in ('runtime_secret_arn', 'redis_secret_arn'):
        if meta.get(field):
            sec = aws_optional('secretsmanager', 'describe-secret', '--secret-id', meta[field])
            if sec and not sec.get('DeletedDate'):
                issues.append('Secret ' + field)
    key = aws_optional('ec2', 'describe-key-pairs', '--key-names', 'management-platform-lab')
    if key and key.get('KeyPairs'):
        issues.append('EC2 key pair')
    # Enumerate project-tagged resources that could remain after Terraform destroy.
    project = meta['project']
    for label, query in [
        ('NAT gateways', ('ec2', 'describe-nat-gateways', '--filter', f'Name=tag:Project,Values={project}')),
        ('Elastic IPs', ('ec2', 'describe-addresses', '--filters', f'Name=tag:Project,Values={project}')),
        ('EBS volumes', ('ec2', 'describe-volumes', '--filters', f'Name=tag:Project,Values={project}')),
    ]:
        result = aws(*query)
        keyname = {'NAT gateways':'NatGateways', 'Elastic IPs':'Addresses', 'EBS volumes':'Volumes'}[label]
        rows = result.get(keyname, [])
        if label == 'NAT gateways':
            rows = [r for r in rows if r['State'] != 'deleted']
        if rows:
            issues.append(f'{label}: {len(rows)} project-tagged')
    # Independently verify services whose historical ARNs may remain in Tagging API.
    groups = aws('ec2', 'describe-security-groups', '--filters',
                 f'Name=tag:Project,Values={project}').get('SecurityGroups', [])
    if groups:
        issues.append(f'Security groups: {len(groups)} project-tagged')
    instances = aws('ec2', 'describe-instances', '--filters',
                    f'Name=tag:Project,Values={project}').get('Reservations', [])
    remaining_instances = [i['InstanceId'] for r in instances for i in r.get('Instances', [])
                           if i['State']['Name'] != 'terminated']
    if remaining_instances:
        issues.append('Active project EC2 instances: ' + ', '.join(remaining_instances))
    lb_names = [b['LoadBalancerName'] for b in
                aws('elbv2', 'describe-load-balancers').get('LoadBalancers', [])
                if b['LoadBalancerName'].startswith(meta['name'] + '-')]
    if lb_names:
        issues.append('Project load balancers: ' + ', '.join(lb_names))
    cache_snapshots = [s['SnapshotName'] for s in
                       aws('elasticache', 'describe-snapshots').get('Snapshots', [])
                       if meta['name'] in s.get('SnapshotName', '')]
    if cache_snapshots:
        issues.append('ElastiCache snapshots: ' + ', '.join(cache_snapshots))
    # Resource Groups Tagging API catches additional still-tagged project resources
    # (ALB/NLB, target groups, Redis, networking, IAM, etc.).
    tags = ['Key=Project,Values=' + meta['project'],
            'Key=Environment,Values=' + meta['environment']]
    tagged = aws('resourcegroupstaggingapi', 'get-resources', '--tag-filters', *tags,
                 '--resources-per-page', '100')
    live_arns = [x['ResourceARN'] for x in tagged.get('ResourceTagMappingList', [])]
    # This API may include previously deleted resources for a while. Report for review,
    # rather than claim that an empty Terraform state proves deletion.
    if live_arns:
        print('WARNING: tagged resource inventory still reports:', *live_arns, sep='\n  ')
        print('INFO: Tagging API may retain deleted-resource history; live AWS checks determine failure.')
    if issues:
        raise RuntimeError('CLEANUP INCOMPLETE: ' + '; '.join(issues))
    print('PASS: tracked infrastructure, volumes, snapshots, log groups, repositories and state checks passed.')
    print('Check the AWS billing dashboard and any manually created untagged/shared resources separately.')


def state_bucket(meta, args):
    # The separate versioned-bucket cleanup from the original guide is deliberately not bypassed.
    ctx = context_checked(meta)
    verify(meta)
    if args.confirm_bucket != ctx['bucket']:
        raise RuntimeError('Pass --confirm-bucket with the exact dedicated backend bucket name')
    # Verify the supported project inventory immediately before deleting remote state.
    # User must also confirm the dedicated bucket name explicitly.
    state = json.loads(tf('state', 'pull', capture=True))
    save(WORK / 'terraform-after-destroy.json', state)
    owner = ['--expected-bucket-owner', ctx['account']]
    bucket, key = ctx['bucket'], ctx['key']
    objects = aws('s3api', 'list-object-versions', '--bucket', bucket, *owner)
    rows = objects.get('Versions', []) + objects.get('DeleteMarkers', [])
    allowed = {key, key + '.tflock'}
    other = {r['Key'] for r in rows if r['Key'] not in allowed}
    if other:
        raise RuntimeError(f'Bucket contains unrelated keys; refusing deletion: {sorted(other)}')
    current = aws('s3api', 'list-objects-v2', '--bucket', bucket, *owner).get('Contents', [])
    if any(x['Key'] == key + '.tflock' for x in current):
        raise RuntimeError('Current Terraform lock exists')
    if any(x['Key'] not in allowed for x in current):
        raise RuntimeError('Bucket has unrelated current objects')
    for i in range(0, len(rows), 1000):
        items = [{'Key': x['Key'], 'VersionId': x['VersionId']} for x in rows[i:i+1000]]
        result = aws('s3api', 'delete-objects', '--bucket', bucket,
                     '--delete', json.dumps({'Objects': items, 'Quiet': True}), *owner)
        if result.get('Errors'):
            raise RuntimeError(f'S3 deletion errors: {result["Errors"]}')
    after = aws('s3api', 'list-object-versions', '--bucket', bucket, *owner)
    if after.get('Versions') or after.get('DeleteMarkers'):
        raise RuntimeError('S3 versions still remain')
    aws('s3api', 'delete-bucket', '--bucket', bucket, *owner)
    print('STATE BUCKET DELETED:', bucket)
    print('FINISHED. Do not run Terraform against this removed backend.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'stop', 'ecr', 'database', 'destroy', 'extras', 'verify', 'state-bucket', 'inventory', 'empty-ecr', 'option-groups'])
    p.add_argument('--confirm-project')
    p.add_argument('--confirm-bucket')

    args = p.parse_args()
    meta = load(DEPLOY)
    os.environ['AWS_DEFAULT_REGION'] = os.environ.get('AWS_DEFAULT_REGION', meta['region'])
    if args.action == 'prepare':
        prepare(meta, args)
        return
    context_checked(meta)
    if args.action in ('stop', 'ecr', 'database', 'destroy', 'extras', 'state-bucket', 'empty-ecr', 'option-groups'):
        confirm(args, meta)
    actions = {'stop': stop, 'ecr': ecr, 'empty-ecr': ecr, 'database': database,
               'destroy': destroy, 'extras': extras, 'verify': verify,
               'inventory': lambda m: print(INVENTORY.read_text()),
               'option-groups': cleanup_rds_residuals,
               'state-bucket': lambda m: state_bucket(m, args)}
    actions[args.action](meta)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, KeyError, ValueError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        sys.exit(1)
