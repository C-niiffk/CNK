#!/usr/bin/env python3
"""Run init-db once per RDS resource ID; persist task progress in a non-secret SSM parameter."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def aws(*args):
    value = json.loads(subprocess.check_output(
        ['aws', '--no-cli-pager', '--output', 'json', *args], text=True) or '{}')
    if value.get('failures'):
        raise RuntimeError('AWS returned task failures: ' + json.dumps(value['failures']))
    return value


def save(name, value):
    aws('ssm', 'put-parameter', '--name', name, '--type', 'String', '--overwrite',
        '--value', json.dumps(value))


def initialize(values):
    name = values['cicd_init_parameter']
    db_id = values['cicd_db_resource_id']
    checkpoint = json.loads(aws('ssm', 'get-parameter', '--name', name)['Parameter']['Value'])
    if checkpoint.get('db_resource_id') != db_id:
        checkpoint = {'db_resource_id': db_id, 'status': 'new'}
    if checkpoint['status'] in ('succeeded', 'adopted'):
        print('Schema already initialized for this RDS resource. Skipping.')
        return
    if checkpoint['status'] == 'failed':
        raise RuntimeError('Previous init-db failed. Inspect its task/logs and partial Oracle users before retrying.')
    if checkpoint['status'] == 'new':
        checkpoint.update(status='launching', client_token=uuid.uuid4().hex,
                          created_at=time.time(), cluster=values['cluster_name'],
                          task_definition=values['init_task_definition'],
                          network=values['init_network_configuration'])
        save(name, checkpoint)
    if checkpoint['status'] == 'launching':
        # Persist the exact request BEFORE launch. A quick retry reuses the ECS client token.
        if time.time() - checkpoint['created_at'] > 1800:
            raise RuntimeError('Unconfirmed launch is older than 30 minutes. Locate the init task before retrying; do not blindly reset the checkpoint.')
        result = aws('ecs', 'run-task', '--cluster', checkpoint['cluster'],
                     '--task-definition', checkpoint['task_definition'], '--launch-type', 'FARGATE',
                     '--platform-version', '1.4.0', '--client-token', checkpoint['client_token'],
                     '--started-by', checkpoint['client_token'],
                     '--network-configuration', json.dumps(checkpoint['network']))
        if len(result.get('tasks', [])) != 1:
            raise RuntimeError('Expected exactly one initialization task.')
        checkpoint.update(status='running', task_arn=result['tasks'][0]['taskArn'])
        save(name, checkpoint)
    if checkpoint['status'] != 'running':
        raise RuntimeError('Unexpected initialization checkpoint status: ' + checkpoint['status'])
    arn = checkpoint['task_arn']
    print('Initialization task:', arn, flush=True)
    for attempt in range(120):
        tasks = aws('ecs', 'describe-tasks', '--cluster', checkpoint['cluster'], '--tasks', arn).get('tasks', [])
        if len(tasks) != 1:
            raise RuntimeError('Saved init task is no longer visible. Verify the schema manually; do not launch another init task automatically.')
        task = tasks[0]
        if task['lastStatus'] == 'STOPPED':
            containers = task.get('containers', [])
            passed = bool(containers) and all(c.get('exitCode') == 0 for c in containers)
            checkpoint['status'] = 'succeeded' if passed else 'failed'
            save(name, checkpoint)
            print(json.dumps({'stoppedReason': task.get('stoppedReason'), 'containers': [
                {'name': c['name'], 'exitCode': c.get('exitCode')} for c in containers]}, indent=2))
            if not passed:
                raise RuntimeError('Initialization failed. Inspect the /init log group; services have not been enabled.')
            print('Schema task passed. Initialization checkpoint saved.')
            return
        print(f'Waiting for init-db: {task["lastStatus"]} ({attempt + 1}/120)', flush=True)
        time.sleep(15)
    raise RuntimeError('init-db wait timed out. The saved task can be resumed; do not launch a duplicate task.')


def main():
    os.chdir(Path(__file__).resolve().parents[2])
    outputs = json.loads(subprocess.check_output(['terraform', '-chdir=terraform', 'output', '-json'], text=True))
    initialize({k: v['value'] for k, v in outputs.items()})


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, KeyError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
