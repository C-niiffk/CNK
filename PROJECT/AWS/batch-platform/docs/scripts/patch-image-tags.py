#!/usr/bin/env python3
"""Replace only the four existing ECS image expressions; preserve all other settings."""
from pathlib import Path


def patched(text):
    updated = text
    for key in ('${each.key}-service', 'application-service', 'agent-service', 'backend-service'):
        prefix = '${aws_ecr_repository.service["' + key + '"].repository_url}:'
        old = prefix + '${var.image_tag}'
        new = prefix + '${local.service_image_tags["' + key + '"]}'
        if text.count(new) == 1 and old not in text:
            continue
        if text.count(old) != 1 or new in text:
            raise RuntimeError('Cannot locate exactly one ECS image expression for ' + key + '; ecs.tf was not changed.')
        updated = updated.replace(old, new)
    return updated


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[2] / 'terraform/ecs.tf'
    try:
        original = path.read_text()
        result = patched(original)
        if result != original:
            path.write_text(result)
        print('ECS image tags patched. CPU, memory, networking, and task counts preserved.')
    except (RuntimeError, OSError) as error:
        raise SystemExit(str(error))
