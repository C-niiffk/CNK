#!/usr/bin/env python3
"""Permanent teardown helpers for the batch-platform lab; see ../cleanup.md.

Python standard library only. Terraform still deletes the remaining managed
infrastructure. Each command is a separate, repeatable cleanup stage.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "cleanup-work"
CONTEXT = WORK / "context.json"
C = {}


def run(*args):
    return subprocess.check_output(list(args), text=True).strip()


def aws(*args, missing=(), payload=None, allow_missing_items=False):
    command = ["aws", "--region", C["region"], "--no-cli-pager",
               "--no-cli-auto-prompt", "--output", "json", *args]
    result = subprocess.run(command, text=True, capture_output=True,
                            input=None if payload is None else json.dumps(payload))
    if result.returncode:
        match = re.search(r"\(([^)]+)\) when calling", result.stderr)
        code = match.group(1) if match else ""
        if code in missing:
            return None
        raise RuntimeError(result.stderr.strip() or f"AWS CLI exit {result.returncode}")
    data = json.loads(result.stdout) if result.stdout.strip() else {}
    failures = data.get("failures", []) + data.get("Failures", []) + data.get("Errors", [])
    if allow_missing_items:
        failures = [f for f in failures if f.get("reason") != "MISSING"]
    if failures:
        raise RuntimeError("AWS reported item failures: " + json.dumps(failures))
    return data


def wait_until(label, ready, attempts=120, interval=15):
    for attempt in range(attempts):
        if ready():
            return
        print(f"Waiting: {label} ({attempt + 1}/{attempts})", flush=True)
        time.sleep(interval)
    raise RuntimeError(f"Timed out: {label}. AWS may still be deleting it. "
                       "Check its status, then rerun this stage; keep the state bucket.")


def save_context():
    CONTEXT.write_text(json.dumps(C, indent=2) + "\n")


def check_account():
    identity = aws("sts", "get-caller-identity")
    if identity["Account"] != C["account"]:
        raise RuntimeError("AWS account differs from the configured cleanup account.")


def managed_count(state):
    return sum(len(r.get("instances", [])) for r in state.get("resources", [])
               if r.get("mode") == "managed")


def prepare():
    global C
    expected = os.environ["BATCH_EXPECTED_ACCOUNT_ID"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    name = os.environ.get("BATCH_NAME", "batch-platform-lab")
    if not re.fullmatch(r"\d{12}", expected) or name != "batch-platform-lab":
        raise RuntimeError("This runbook targets BATCH_NAME=batch-platform-lab and a 12-digit account ID.")
    C = {"account": expected, "region": region, "name": name}
    check_account()
    if run("terraform", "-chdir=terraform", "workspace", "show") != "default":
        raise RuntimeError("This runbook uses the default Terraform workspace.")
    backend = json.loads(Path("terraform/.terraform/terraform.tfstate").read_text())["backend"]
    if backend["type"] != "s3":
        raise RuntimeError("Expected the initialized S3 backend.")
    config = backend["config"]
    if config.get("region") != region:
        raise RuntimeError("The backend region and AWS_REGION must match for this lab runbook.")
    state = json.loads(run("terraform", "-chdir=terraform", "state", "pull"))
    C.update(bucket=config["bucket"], key=config["key"], lineage=state.get("lineage"),
             log_bucket=f"{name}-alb-{expected}-{region}", master_secrets=[])
    for resource in state.get("resources", []):
        if resource.get("mode") != "managed":
            continue
        for instance in resource.get("instances", []):
            a = instance.get("attributes", {})
            tags = a.get("tags_all") or a.get("tags") or {}
            if tags and (tags.get("Project", "batch-platform") != "batch-platform"
                         or tags.get("Environment", "lab") != "lab"):
                raise RuntimeError("The selected Terraform state includes another project's resources.")
            if resource["type"] == "aws_s3_bucket" and resource["name"] == "alb_logs":
                C["log_bucket"] = a.get("bucket") or a["id"]
            if resource["type"] == "aws_db_instance" and resource["name"] == "oracle":
                if a.get("identifier") != name:
                    raise RuntimeError("RDS identifier differs from batch-platform-lab.")
                C["master_secrets"] += [s["secret_arn"] for s in a.get("master_user_secret", [])]
    if C["log_bucket"] == C["bucket"]:
        raise RuntimeError("The ALB log bucket must not be the Terraform state bucket.")
    if CONTEXT.exists():
        old = json.loads(CONTEXT.read_text())
        for key in ("account", "region", "name", "bucket", "key", "lineage"):
            if old[key] != C[key]:
                raise RuntimeError("cleanup-work belongs to another deployment; archive it first.")
        C["master_secrets"] = sorted(set(C["master_secrets"] + old["master_secrets"]))
    WORK.mkdir(mode=0o700, exist_ok=True)
    backup = WORK / "state-before-cleanup.json"
    if not backup.exists():
        backup.write_text(json.dumps(state, indent=2) + "\n")
    save_context()
    print(json.dumps({k: C[k] for k in ("account", "region", "name", "bucket", "key", "log_bucket")}, indent=2))
    print(f"Managed instances still in Terraform state: {managed_count(state)}")
    print("PREPARED. No AWS resources have been changed.")


def cluster_active():
    data = aws("ecs", "describe-clusters", "--clusters", C["name"], allow_missing_items=True)
    return any(c["status"] != "INACTIVE" for c in data.get("clusters", []))


def albs():
    names = {C["name"] + "-pub", C["name"] + "-int"}
    return [b for b in aws("elbv2", "describe-load-balancers")["LoadBalancers"]
            if b["LoadBalancerName"] in names]


def stop():
    name = C["name"]
    if cluster_active():
        arns = aws("ecs", "list-services", "--cluster", name)["serviceArns"]
        allowed = {name + "-" + x for x in ("frontend", "backend", "app1", "app2")}
        if any(a.rsplit("/", 1)[-1] not in allowed for a in arns):
            raise RuntimeError("Unexpected service in the cluster; inspect it before continuing.")
        for service in sorted(allowed):
            def current_service():
                return aws("ecs", "describe-services", "--cluster", name, "--services", service,
                           allow_missing_items=True).get("services", [])
            rows = current_service()
            if not rows or rows[0]["status"] == "INACTIVE":
                continue
            if rows[0]["status"] == "ACTIVE":
                print("Removing ECS service:", service, flush=True)
                aws("ecs", "update-service", "--cluster", name, "--service", service, "--desired-count", "0")
                aws("ecs", "delete-service", "--cluster", name, "--service", service, "--force")
            wait_until(service, lambda: all(s["status"] == "INACTIVE" for s in current_service()))
        tasks = set()
        for status in ("RUNNING", "STOPPED"):
            tasks.update(aws("ecs", "list-tasks", "--cluster", name, "--desired-status", status)["taskArns"])
        for arn in sorted(tasks):
            def task_finished():
                d = aws("ecs", "describe-tasks", "--cluster", name, "--tasks", arn, allow_missing_items=True)
                return all(t["lastStatus"] == "STOPPED" for t in d.get("tasks", []))
            if not task_finished():
                aws("ecs", "stop-task", "--cluster", name, "--task", arn, "--reason", "Permanent lab cleanup")
                wait_until(arn, task_finished)
    for lb in albs():
        arn = lb["LoadBalancerArn"]
        print("Deleting ALB:", lb["LoadBalancerName"], flush=True)
        aws("elbv2", "modify-load-balancer-attributes", "--load-balancer-arn", arn,
            "--attributes", "Key=deletion_protection.enabled,Value=false",
            "Key=access_logs.s3.enabled,Value=false")
        aws("elbv2", "delete-load-balancer", "--load-balancer-arn", arn)
    wait_until("project ALBs to disappear", lambda: not albs())
    print("STOPPED: ECS services/tasks and both ALBs are removed.")


def db():
    data = aws("rds", "describe-db-instances", "--db-instance-identifier", C["name"], missing=("DBInstanceNotFound",))
    return data["DBInstances"][0] if data else None


def snapshots():
    return [s for s in aws("rds", "describe-db-snapshots")["DBSnapshots"]
            if s.get("DBInstanceIdentifier") == C["name"]]


def backups():
    return [b for b in aws("rds", "describe-db-instance-automated-backups")["DBInstanceAutomatedBackups"]
            if b.get("DBInstanceIdentifier") == C["name"]]


def option_groups():
    return [g for g in aws("rds", "describe-option-groups")["OptionGroupsList"]
            if g["OptionGroupName"].startswith(C["name"] + "-") and g["EngineName"] == "oracle-se2"]


def database():
    instance = db()
    if instance:
        secret = instance.get("MasterUserSecret", {}).get("SecretArn")
        if secret and secret not in C["master_secrets"]:
            C["master_secrets"].append(secret)
            save_context()
        if instance["DBInstanceStatus"] != "deleting":
            if instance.get("DeletionProtection"):
                aws("rds", "modify-db-instance", "--db-instance-identifier", C["name"],
                    "--no-deletion-protection", "--apply-immediately")
            def removable():
                d = db()
                return d is None or d["DBInstanceStatus"] == "deleting" or (
                    not d.get("DeletionProtection") and d["DBInstanceStatus"] in
                    ("available", "failed", "incompatible-restore", "incompatible-network"))
            wait_until("RDS to accept deletion", removable, attempts=240)
            instance = db()
            if instance and instance["DBInstanceStatus"] != "deleting":
                print("Deleting RDS without a new final snapshot:", C["name"], flush=True)
                aws("rds", "delete-db-instance", "--db-instance-identifier", C["name"],
                    "--skip-final-snapshot", "--delete-automated-backups")
        wait_until("RDS instance deletion", lambda: db() is None, attempts=240)
    wait_until("backup sets to become retained or finish deletion",
               lambda: all(b.get("Status") in ("retained", "deleting") for b in backups()))
    for backup in backups():
        if backup.get("Status") != "deleting":
            print("Deleting retained automated backup:", backup["DbiResourceId"], flush=True)
            aws("rds", "delete-db-instance-automated-backup", "--dbi-resource-id", backup["DbiResourceId"],
                missing=("DBInstanceAutomatedBackupNotFound", "DBInstanceAutomatedBackupNotFoundFault"))
    wait_until("retained automated backups deletion", lambda: not backups())
    for snapshot in snapshots():
        if snapshot["SnapshotType"] != "manual":
            continue
        sid = snapshot["DBSnapshotIdentifier"]
        def current_snapshot():
            d = aws("rds", "describe-db-snapshots", "--db-snapshot-identifier", sid,
                    missing=("DBSnapshotNotFound",))
            return d["DBSnapshots"][0] if d else None
        def snapshot_ready():
            s = current_snapshot()
            if s and s["Status"] == "failed":
                raise RuntimeError(f"Snapshot {sid} is failed; inspect its deletion status in RDS.")
            return not s or s["Status"] in ("available", "deleting")
        wait_until(sid + " to become deletable", snapshot_ready, attempts=240)
        current = current_snapshot()
        if current and current["Status"] != "deleting":
            print("Deleting manual/final snapshot:", sid, flush=True)
            aws("rds", "delete-db-snapshot", "--db-snapshot-identifier", sid, missing=("DBSnapshotNotFound",))
        wait_until(sid + " deletion", lambda: current_snapshot() is None)
    wait_until("all project snapshots to disappear", lambda: not snapshots())
    groups = option_groups()
    for group in groups:
        og = group["OptionGroupName"]
        users = [d["DBInstanceIdentifier"] for d in aws("rds", "describe-db-instances")["DBInstances"]
                 if any(m["OptionGroupName"] == og for m in d.get("OptionGroupMemberships", []))]
        users += [s["DBSnapshotIdentifier"] for s in aws("rds", "describe-db-snapshots")["DBSnapshots"]
                  if s.get("OptionGroupName") == og]
        if users:
            raise RuntimeError(f"Option group {og} is used outside the deleted DB: {users}")
        print("Deleting unused option group:", og, flush=True)
        aws("rds", "delete-option-group", "--option-group-name", og, missing=("OptionGroupNotFoundFault",))
    wait_until("Oracle option groups deletion", lambda: not option_groups())
    print("DATABASE REMOVED: instance, snapshots, automated backups and option groups.")


def repositories():
    names = {C["name"] + "/" + s + "-service" for s in ("frontend", "backend", "agent", "application")}
    return [r for r in aws("ecr", "describe-repositories", "--registry-id", C["account"])["repositories"]
            if r["repositoryName"] in names]


def ecr():
    for repo in repositories():
        print("Deleting repository and all images:", repo["repositoryName"], flush=True)
        aws("ecr", "delete-repository", "--registry-id", C["account"],
            "--repository-name", repo["repositoryName"], "--force", missing=("RepositoryNotFoundException",))
    wait_until("four ECR repositories deletion", lambda: not repositories())
    print("ECR REMOVED: all four project repositories are absent.")


def s3(*args, bucket, missing=(), payload=None):
    return aws("s3api", *args, "--bucket", bucket, "--expected-bucket-owner", C["account"],
               missing=missing, payload=payload)


def bucket_exists(bucket):
    return s3("get-bucket-location", bucket=bucket, missing=("NoSuchBucket",)) is not None


def bucket_contents(bucket):
    versions = s3("list-object-versions", bucket=bucket)
    current = s3("list-objects-v2", bucket=bucket)
    uploads = s3("list-multipart-uploads", bucket=bucket)
    return (versions.get("Versions", []) + versions.get("DeleteMarkers", []),
            current.get("Contents", []), uploads.get("Uploads", []))


def purge_bucket(bucket, keys=None):
    """Delete all versions; keys limits a shared backend to exact project keys."""
    selected = lambda row: keys is None or row["Key"] in keys
    for _ in range(5):
        versions, objects, uploads = bucket_contents(bucket)
        targets = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in versions if selected(v)]
        version_keys = {v["Key"] for v in versions}
        targets += [{"Key": o["Key"]} for o in objects if selected(o) and o["Key"] not in version_keys]
        uploads = [u for u in uploads if selected(u)]
        if not targets and not uploads:
            return
        for i in range(0, len(targets), 1000):
            s3("delete-objects", "--delete", "file:///dev/stdin", bucket=bucket,
               payload={"Objects": targets[i:i + 1000], "Quiet": True})
        for upload in uploads:
            s3("abort-multipart-upload", "--key", upload["Key"], "--upload-id", upload["UploadId"],
               bucket=bucket, missing=("NoSuchUpload",))
        print(f"Cleared {len(targets)} object versions/objects and {len(uploads)} multipart uploads from {bucket}", flush=True)
    raise RuntimeError("S3 is still receiving objects. Stop the writer and rerun this stage.")


def logs_bucket():
    bucket = C["log_bucket"]
    if albs():
        raise RuntimeError("Run the stop stage first: ALBs still exist.")
    if bucket_exists(bucket):
        # Remove the log-delivery grant before emptying the bucket.
        s3("delete-bucket-policy", bucket=bucket, missing=("NoSuchBucketPolicy",))
        purge_bucket(bucket)
        if any(bucket_contents(bucket)):
            raise RuntimeError("The ALB log bucket is not empty.")
        print("ALB LOG BUCKET EMPTY. Terraform will delete it in the destroy plan.")
    else:
        print("ALB LOG BUCKET ALREADY ABSENT.")


def log_groups():
    rows = []
    for prefix in (f"/ecs/{C['name']}/", f"/aws/rds/instance/{C['name']}/",
                   f"/aws/ecs/containerinsights/{C['name']}/"):
        rows += aws("logs", "describe-log-groups", "--log-group-name-prefix", prefix)["logGroups"]
    return rows


def task_definitions(status):
    rows = []
    for suffix in ("frontend", "backend", "app1", "app2", "init"):
        family = C["name"] + "-" + suffix
        arns = aws("ecs", "list-task-definitions", "--family-prefix", family, "--status", status)["taskDefinitionArns"]
        rows += [a for a in arns if a.rsplit("/", 1)[-1].rsplit(":", 1)[0] == family]
    return rows


def secret_ids():
    return [C["name"] + "/runtime", *C["master_secrets"]]


def secret(secret_id):
    return aws("secretsmanager", "describe-secret", "--secret-id", secret_id,
               missing=("ResourceNotFoundException",))


def assert_empty_state():
    state = json.loads(run("terraform", "-chdir=terraform", "state", "pull"))
    if state.get("lineage") != C["lineage"] or managed_count(state):
        raise RuntimeError("Terraform still manages resources, or this is a different state. Finish destroy first.")
    return state


def extras():
    assert_empty_state()
    if db() or albs() or cluster_active():
        raise RuntimeError("Running infrastructure still exists; finish Terraform destroy first.")
    for sid in secret_ids():
        d = secret(sid)
        if not d:
            continue
        if d.get("OwningService"):
            print("Waiting for the owning AWS service to remove secret:", sid, flush=True)
        else:
            if d.get("ReplicationStatus"):
                raise RuntimeError("Secret replicas exist outside this single-region lab runbook.")
            if d.get("DeletedDate"):
                aws("secretsmanager", "restore-secret", "--secret-id", sid)
            aws("secretsmanager", "delete-secret", "--secret-id", sid, "--force-delete-without-recovery")
        wait_until("permanent deletion of " + sid, lambda: secret(sid) is None, attempts=80)
    for group in log_groups():
        print("Deleting log group:", group["logGroupName"], flush=True)
        aws("logs", "delete-log-group", "--log-group-name", group["logGroupName"],
            missing=("ResourceNotFoundException",))
    for arn in task_definitions("ACTIVE"):
        aws("ecs", "deregister-task-definition", "--task-definition", arn)
    inactive = task_definitions("INACTIVE")
    for i in range(0, len(inactive), 10):
        aws("ecs", "delete-task-definitions", "--task-definitions", *inactive[i:i + 10])
    pending = task_definitions("DELETE_IN_PROGRESS")
    print("EXTRAS CLEANUP REQUESTED.")
    print(f"Task definition revisions awaiting asynchronous AWS deletion: {len(pending)}")


def verify():
    assert_empty_state()
    name = C["name"]
    tags = ["Name=tag:Project,Values=batch-platform", "Name=tag:Environment,Values=lab"]
    counts = {
        "rds_instances": int(db() is not None),
        "rds_snapshots": len(snapshots()),
        "retained_automated_backups": len(backups()),
        "oracle_option_groups": len(option_groups()),
        "ecr_repositories": len(repositories()),
        "load_balancers": len(albs()),
        "active_ecs_clusters": int(cluster_active()),
        "active_or_inactive_task_definitions": len(task_definitions("ACTIVE")) + len(task_definitions("INACTIVE")),
        "log_groups": len(log_groups()),
        "secrets": sum(secret(sid) is not None for sid in secret_ids()),
        "alb_log_buckets": int(bucket_exists(C["log_bucket"])),
        "vpcs": len(aws("ec2", "describe-vpcs", "--filters", *tags)["Vpcs"]),
        "elastic_ips": len(aws("ec2", "describe-addresses", "--filters", *tags)["Addresses"]),
        "non_deleted_nat_gateways": sum(n["State"] != "deleted" for n in aws("ec2", "describe-nat-gateways", "--filter", *tags)["NatGateways"]),
        "iam_roles": sum(r["RoleName"] in {name + "-" + s for s in ("frontend-exec", "backend-exec", "app-exec", "init-exec", "task")}
                         for r in aws("iam", "list-roles")["Roles"]),
        "sns_topics": sum(t["TopicArn"].rsplit(":", 1)[-1] == name + "-alarms" for t in aws("sns", "list-topics")["Topics"]),
        "cloudmap_namespaces": sum(n["Name"] == name + ".internal" for n in aws("servicediscovery", "list-namespaces")["Namespaces"]),
        "cloudwatch_alarms": len(aws("cloudwatch", "describe-alarms", "--alarm-name-prefix", name + "-").get("MetricAlarms", []))
    }
    print(json.dumps(counts, indent=2))
    pending = task_definitions("DELETE_IN_PROGRESS")
    print(f"Non-runnable task definition revisions pending AWS background deletion: {len(pending)}")
    if any(counts.values()):
        raise RuntimeError("Cleanup is incomplete. Resolve the nonzero counts before removing backend state.")
    print("PASS: Terraform has no managed resources; project checks passed.")
    if pending:
        print("AWS metadata purge is still pending; these revisions cannot start new tasks.")


def state_bucket():
    verify()
    bucket, key = C["bucket"], C["key"]
    if not bucket_exists(bucket):
        raise RuntimeError("Backend bucket is already absent; do not run Terraform against this backend.")
    locks = s3("list-objects-v2", "--prefix", key + ".tflock", bucket=bucket).get("Contents", [])
    if any(o["Key"] == key + ".tflock" for o in locks):
        raise RuntimeError("A current Terraform lock file exists; finish the other Terraform operation first.")
    state = assert_empty_state()
    (WORK / "state-after-cleanup.json").write_text(json.dumps(state, indent=2) + "\n")
    purge_bucket(bucket, keys={key, key + ".tflock"})
    if any(bucket_contents(bucket)):
        print("SHARED BACKEND RETAINED:", bucket)
        print("Only this project's state and lock history were removed. Other keys/uploads remain untouched.")
    else:
        s3("delete-bucket", bucket=bucket)
        wait_until("state bucket deletion", lambda: not bucket_exists(bucket), attempts=20)
        print("STATE BUCKET DELETED:", bucket)
    print("FINISHED. Do not run Terraform using this removed project state.")


def main():
    global C
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "stop", "database", "ecr", "logs-bucket", "extras", "verify", "state-bucket"))
    stage = parser.parse_args().stage
    os.chdir(ROOT)
    os.umask(0o077)
    if stage == "prepare":
        prepare()
        return
    C = json.loads(CONTEXT.read_text())
    check_account()
    print(f"Stage={stage}; account={C['account']}; region={C['region']}; project={C['name']}", flush=True)
    {"stop": stop, "database": database, "ecr": ecr, "logs-bucket": logs_bucket,
     "extras": extras, "verify": verify, "state-bucket": state_bucket}[stage]()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, OSError, ValueError, subprocess.SubprocessError) as error:
        print("STOP:", error, file=sys.stderr)
        sys.exit(1)
