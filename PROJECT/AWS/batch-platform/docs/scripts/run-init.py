#!/usr/bin/env python3
"""Launch the one-off private ECS schema task and require exitCode=0."""
import json
import os
from pathlib import Path
import subprocess


def aws(*args):
    return json.loads(subprocess.check_output(["aws", "--no-cli-pager", *args], text=True))


def main():
    os.chdir(Path(__file__).resolve().parents[2])
    outputs = json.loads(subprocess.check_output(
        ["terraform", "-chdir=terraform", "output", "-json"], text=True))
    values = {key: item["value"] for key, item in outputs.items()}
    result = aws(
        "ecs", "run-task", "--cluster", values["cluster_name"],
        "--task-definition", values["init_task_definition"],
        "--launch-type", "FARGATE", "--platform-version", "1.4.0",
        "--network-configuration", json.dumps(values["init_network_configuration"])
    )
    if result.get("failures") or not result.get("tasks"):
        raise SystemExit(json.dumps(result.get("failures", result), indent=2))

    task_arn = result["tasks"][0]["taskArn"]
    print("Initialization task:", task_arn, flush=True)
    subprocess.run([
        "aws", "ecs", "wait", "tasks-stopped",
        "--cluster", values["cluster_name"], "--tasks", task_arn
    ], check=True)
    task = aws("ecs", "describe-tasks", "--cluster", values["cluster_name"], "--tasks", task_arn)["tasks"][0]
    containers = [{key: container.get(key) for key in ["name", "exitCode", "reason"]}
                  for container in task["containers"]]
    print(json.dumps({"stoppedReason": task.get("stoppedReason"), "containers": containers}, indent=2))
    if not containers or not all(container.get("exitCode") == 0 for container in containers):
        raise SystemExit("Initialization failed. Check the /init log group; do not enable services.")
    print("Schema task passed. Check SCHEMA_INITIALIZED version=1 in logs, then enable services.")


if __name__ == "__main__":
    main()
