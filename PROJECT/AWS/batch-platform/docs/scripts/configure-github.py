#!/usr/bin/env python3
"""One-time GitHub environments and a named main-branch ruleset, using gh authentication."""
import argparse
import json
import subprocess


def api(path, method='GET', body=None):
    args = ['gh', 'api', path, '--method', method]
    if body is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout or '{}')


def configure(repo, reviewer, solo):
    account = api('users/' + reviewer)
    for name, branches, approval in (
        ('app-lab', ['main'], False),
        ('terraform-plan', ['main'], False),
        ('infra-lab', ['main'], True),
        ('terraform-pr-plan', ['refs/pull/*/merge'], True),
    ):
        body = {'wait_timer': 0, 'prevent_self_review': False if solo else approval,
                'reviewers': [{'type': 'User', 'id': account['id']}] if approval else [],
                'can_admins_bypass': False,
                'deployment_branch_policy': {'protected_branches': False, 'custom_branch_policies': True}}
        api('repos/' + repo + '/environments/' + name, 'PUT', body)
        endpoint = 'repos/' + repo + '/environments/' + name + '/deployment-branch-policies'
        existing = api(endpoint)['branch_policies']
        for rule in existing:
            if rule['name'] not in branches or rule.get('type', 'branch') != 'branch':
                api(endpoint + '/' + str(rule['id']), 'DELETE')
        for branch in branches:
            if not any(r['name'] == branch and r.get('type', 'branch') == 'branch' for r in existing):
                api(endpoint, 'POST', {'name': branch, 'type': 'branch'})
        actual = api('repos/' + repo + '/environments/' + name)
        if approval and not any(r['type'] == 'required_reviewers' for r in actual.get('protection_rules', [])):
            raise RuntimeError('GitHub did not enable required reviewers for ' + name + '. Check repository visibility and GitHub plan support.')
        print('ENVIRONMENT READY:', name, '(approval required)' if approval else '(main only)')
    body = {
        'name': 'batch-platform-lab-main', 'target': 'branch', 'enforcement': 'active', 'bypass_actors': [],
        'conditions': {'ref_name': {'include': ['refs/heads/main'], 'exclude': []}},
        'rules': [
            {'type': 'deletion'}, {'type': 'non_fast_forward'},
            {'type': 'pull_request', 'parameters': {
                'dismiss_stale_reviews_on_push': True, 'require_code_owner_review': False,
                'require_last_push_approval': False, 'required_review_thread_resolution': True,
                'required_approving_review_count': 0 if solo else 1}},
            {'type': 'required_status_checks', 'parameters': {
                'required_status_checks': [{'context': 'CI gate'}],
                'strict_required_status_checks_policy': True}}
        ]
    }
    endpoint = 'repos/' + repo + '/rulesets'
    existing = api(endpoint)
    own = [r for r in existing if r['name'] == body['name'] and r.get('source') == repo]
    if len(own) > 1:
        raise RuntimeError('Multiple batch-platform rulesets found; inspect repository settings.')
    api(endpoint + '/' + str(own[0]['id']) if own else endpoint, 'PUT' if own else 'POST', body)
    print('MAIN PROTECTED: PR required; CI gate required; PR approvals:', 0 if solo else 1)
    if solo:
        print('SOLO LAB: review your PR before merging; infrastructure deployments still require your Environment approval.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--solo', action='store_true')
    args = parser.parse_args()
    configure(args.repo, args.reviewer, args.solo)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, KeyError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit('GITHUB SETUP FAILED: ' + str(error))
