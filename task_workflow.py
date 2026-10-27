"""One request -> bounded project-owned plan -> verified patch or explicit blocker.

Profiles define scope/checks once. The task text supplies intent, never permissions.
No planner model, shell strings, automatic promotion, or background work is added.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

import agent_bridge as bridge
import proofloop as core
from coordinator import require


DEFAULT_LIMITS = {'max_repairs': 2, 'max_agent_calls': 6, 'agent_timeout_seconds': 180,
                  'check_timeout_seconds': 30, 'max_run_seconds': 600}
PROFILE_FIELDS = bridge.PLAN_FIELDS - {'goal'}


def read_profile(root, name):
    name = bridge.relative_name(name)
    require((root / name).exists(), 'project profile missing: ' + name)
    path = bridge.file_path(root, name)
    raw = path.read_bytes()
    profile = json.loads(raw)
    require(isinstance(profile, dict) and set(profile) == {'schema_version', 'plan'} and
            type(profile['schema_version']) is int and profile['schema_version'] == 1, 'invalid project profile')
    plan = profile['plan']
    require(isinstance(plan, dict) and PROFILE_FIELDS <= set(plan) and
            set(plan) <= PROFILE_FIELDS | {'test_adapter'}, 'invalid project profile fields')
    if isinstance(plan['editable'], list) and all(isinstance(item, str) for item in plan['editable']):
        require(all(bridge.normalized_name(name) != bridge.normalized_name(item) for item in plan['editable']),
                'project profile cannot be editable')
    bridge.validate_plan({**plan, 'goal': 'Validate project profile.', 'delivery': 'diff'}, name)
    for item in plan['editable']:
        editable = bridge.file_path(root, item, optional=True)
        require(not editable.exists() or not os.path.samefile(path, editable),
                'project profile cannot be editable through a file alias')
    expected = bridge.file_map(root, plan['files'])
    bridge.directory_modes(root, plan['files'])
    require(all(expected[n] is not None for n in set(plan['files']) - set(plan['editable'])),
            'declared noneditable profile files must exist')
    return profile, hashlib.sha256(raw).hexdigest()


def initialize_profile(args):
    root = bridge.checked_project_root(args.root)
    name = bridge.relative_name(args.profile)
    if args.from_plan:
        plan = bridge.validate_plan(core.load_json(bridge.plan_file(root, args.from_plan)), args.from_plan)
        scope = {k:v for k,v in plan.items() if k not in ('goal', 'requirements', 'delivery')}
    else:
        require(args.editable and args.protected and args.check_json,
                'provide --from-plan, or --editable, --protected and --check-json')
        editable, protected = args.editable, args.protected
        scope = {**DEFAULT_LIMITS, 'files': sorted(set(editable + protected + (args.context or []))),
                 'editable': editable, 'protected': protected,
                 'quality': [json.loads(value) for value in args.check_json], 'specialists': []}
    profile = {'schema_version': 1, 'plan': scope}
    require(all(bridge.normalized_name(name) != bridge.normalized_name(item) for item in scope['editable']),
            'project profile cannot be editable')
    bridge.validate_plan({**scope, 'goal': 'Validate project profile.', 'delivery': 'diff'}, name)
    expected = bridge.file_map(root, scope['files'])
    bridge.directory_modes(root, scope['files'])
    require(all(expected[n] is not None for n in set(scope['files']) - set(scope['editable'])),
            'declared noneditable profile files must exist')
    with core.project_lock(root):
        path = core.safe_write_path(root, name)
        core.write_json(path, profile, exclusive=True)
    print(core.canonical_json({'profile': str(path), 'sha256': bridge.digest(path),
                               'editable': scope['editable'], 'protected': scope['protected'],
                               'next_action': 'Run proofloop fix --root ROOT --request "describe the fix".'}), end='')
    return 0


def prepare(root, run_id, request, profile_name):
    require(isinstance(run_id, str) and core.RUN_ID_RE.fullmatch(run_id), 'invalid task run ID')
    require(isinstance(request, str) and 0 < len(request.strip()) <= 4000, 'request must contain 1..4000 characters')
    profile, profile_hash = read_profile(root, profile_name)
    relative = f'.proof/tasks/{run_id}'
    request_hash = hashlib.sha256(request.encode()).hexdigest()
    plan = {**profile['plan'], 'goal': request, 'delivery': 'diff',
            'requirements': [{'id': 'TASK', 'description': request}]}
    plan_name = relative + '/plan.json'
    bridge.validate_plan(plan, plan_name)
    intake_name = relative + '/intake.json'
    with core.project_lock(root):
        bridge.private_run_directory(root, relative)
        intake_path = root / intake_name
        if intake_path.exists():
            intake = core.load_json(core.safe_project_file(root, intake_name))
            fields = {'schema_version', 'run_id', 'request_sha256', 'profile_path', 'profile_sha256', 'evidence'}
            require(isinstance(intake, dict) and set(intake) == fields and
                    type(intake['schema_version']) is int and intake['schema_version'] == 1 and
                    intake['run_id'] == run_id, 'invalid task intake evidence')
            require(intake['request_sha256'] == request_hash, 'request changed; use a new run ID')
            require(intake['profile_path'] == profile_name and intake['profile_sha256'] == profile_hash,
                    'project profile changed; use a new authorized run')
            expected_files = {relative + '/' + n for n in ('request.txt', 'profile.json', 'plan.json')}
            require(isinstance(intake['evidence'], dict) and set(intake['evidence']) == expected_files,
                    'task intake evidence changed: incomplete file map')
            for name, expected in intake['evidence'].items():
                require(bridge.digest(core.safe_project_file(root, name)) == expected, 'task intake evidence changed')
            require(core.load_json(core.safe_project_file(root, plan_name)) == plan and
                    core.load_json(core.safe_project_file(root, relative + '/profile.json')) == profile and
                    (root / relative / 'request.txt').read_bytes() == request.encode(),
                    'task intake evidence changed: request/profile/plan disagree')
        else:
            # Each file is exclusive. Partial intake is retained, never silently replayed.
            evidence = {}
            for name, data in [('request.txt', request.encode()),
                               ('profile.json', core.canonical_json(profile).encode()),
                               ('plan.json', core.canonical_json(plan).encode())]:
                path = core.safe_write_path(root, relative + '/' + name)
                core.atomic_write(path, data, exclusive=True)
                evidence[relative + '/' + name] = bridge.digest(path)
            core.write_json(core.safe_write_path(root, intake_name),
                            {'schema_version': 1, 'run_id': run_id, 'request_sha256': request_hash,
                             'profile_path': profile_name, 'profile_sha256': profile_hash, 'evidence': evidence}, exclusive=True)
    return relative + '/plan.json', {'profile_sha256': profile_hash, 'request_sha256': request_hash,
                                    'intake': intake_name}


def blocker(reason, status=None):
    status = status or {}
    lower = reason.lower()
    if 'profile' in lower and ('missing' in lower or 'not found' in lower):
        code, action = 'missing_project_profile', 'Configure this project once with profile-init using its file scope and trusted checks.'
    elif 'request changed' in lower or 'profile changed' in lower or 'plan changed' in lower:
        code, action = 'scope_changed', 'Review the changed request/profile and start a new authorized run; the old run is preserved.'
    elif 'resource collision' in lower:
        code, action = 'prior_run_unresolved', 'Inspect the prior run holding the project resource and reconcile its recorded process/evidence before starting new work.'
    elif 'write lock' in lower:
        code, action = 'writer_lock_held', 'Inspect the owning process before reconciling the lock; never remove it merely because it is old.'
    elif 'budget' in lower or 'repair limit' in lower:
        code, action = 'budget_exhausted', 'Review failed checks/findings; revise the scope or explicitly authorize a new budget.'
    elif 'no progress' in lower or 'no changes' in lower:
        code, action = 'no_progress', 'Review the last proposal and findings; add the missing requirement or capability before a new run.'
    elif 'cancel' in lower:
        code, action = 'cancelled', 'Inspect the saved candidate and evidence. This run will not resume after cancellation.'
    elif 'patch' in lower or 'umask' in lower:
        code, action = 'patch_delivery_failed', 'Inspect the candidate and patch-check evidence; split incompatible permission changes or use a separately authorized apply workflow.'
    elif 'outside completed' in lower or 'source' in lower or 'candidate' in lower or 'evidence changed' in lower:
        code, action = 'state_changed', 'Inspect source/candidate/evidence changes before another run. No patch is certified for the changed state.'
    elif status.get('active_stage') or 'unfinished stage' in lower:
        code, action = 'uncertain_stage', 'Inspect the recorded process and stage evidence. Do not redispatch or clear active state blindly.'
    elif 'unittest' in lower or 'test adapter' in lower or 'runtime' in lower or 'codex' in lower or 'process' in lower or 'git is required' in lower:
        code, action = 'runtime_failure', 'Inspect the recorded stderr and cleanup evidence; resolve the runtime failure before a new run.'
    else:
        code, action = 'invalid_task_or_state', 'Inspect the reason and saved evidence, and correct the project profile or task input.'
    findings = []
    for item in status.get('feedback', []) or []:
        if isinstance(item, str):
            findings.append(item)
        elif isinstance(item, dict):
            for check in item.get('commands', []):
                if check.get('exit_code') != 0:
                    findings.append({'command': check.get('argv'), 'exit_code': check.get('exit_code'),
                                     'stderr_tail': check.get('stderr', '')[-1500:]})
            adapter = item.get('test_adapter') or {}
            findings.extend({'test': row.get('id'), 'status': row.get('status')}
                            for row in adapter.get('tests', []) if row.get('status') != 'pass')
            findings.extend({'collection': row.get('file'), 'error': row.get('error', '')[-1500:]}
                            for row in adapter.get('collection_errors', []))
            findings.extend({'fixture': row.get('id'), 'status': row.get('status'),
                             'detail': row.get('detail', '')[-1500:]} for row in adapter.get('fixture_errors', []))
            if any((adapter.get('coverage') or {}).values()):
                findings.append({'test_coverage': adapter['coverage']})
            findings.extend(item.get('findings', []))
    return {'code': code, 'reason': reason, 'findings': findings,
            'next_action': action}


def execute(root, request, *, run_id, profile_name='proofloop-project.json', host=None):
    root = bridge.checked_project_root(root)
    loop = None
    metadata = {}
    try:
        plan_path, metadata = prepare(root, run_id, request, profile_name)
        loop = bridge.AgentBridge(root, run_id, plan_path, host=host)
        result = loop.advance()
        prepare(root, run_id, request, profile_name)  # Recheck frozen task/profile evidence after native work.
        if result['state'] != 'completed_advisory':
            reason = result.get('reason') or 'task did not complete'
            return {'outcome': 'blocked', 'run_id': run_id, **metadata, 'run': result,
                    'blocker': blocker(reason, result), 'acceptance': 'not_accepted'}
        require(result['delivery'] == 'diff' and result.get('diff'), 'completed task is missing verified diff evidence')
        record = loop.state['records'][-1]
        saved = core.load_json(core.safe_project_file(root, record['path']))
        require(saved['stage'] == 'deliver' and saved['result'] == result['diff'], 'delivered diff state differs from frozen evidence')
        patch = core.safe_project_file(root, result['diff']['path'])
        require(bridge.digest(patch) == result['diff']['sha256'] and
                result['diff']['candidate_sha256'] == result['candidate_sha256'], 'delivered diff hash/candidate mismatch')
        return {'outcome': 'verified_diff', 'run_id': run_id, **metadata, 'run': result,
                'diff': result['diff'], 'acceptance': 'not_accepted',
                'verification': 'local checks and fresh agent review of candidate content and declared file/directory modes; other filesystem metadata excluded',
                'next_action': ('Review the patch; apply it with umask ' + result['diff']['apply_umask'] +
                                ' and verify resulting permissions.' if result['diff']['apply_umask'] else
                                'Review the empty patch; source files have not been changed.')}
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        status = None
        if loop is not None and hasattr(loop, 'state'):
            # A failed freshness check must not be replaced with a success-shaped status.
            status = {'state': loop.state['status'], 'phase': loop.state['phase'],
                      'agent_calls': loop.state['agent_calls'], 'repair_rounds_used': loop.state['round'],
                      'active_stage': loop.state.get('active'), 'feedback': loop.state.get('feedback'),
                      'evidence': loop.relative}
        return {'outcome': 'blocked', 'run_id': run_id, **metadata, 'run': status,
                'blocker': blocker(f'invalid task or saved state ({type(exc).__name__}): {exc}'
                                   if isinstance(exc, (KeyError, TypeError, OverflowError)) else str(exc), status),
                'acceptance': 'not_accepted'}
    finally:
        if loop is not None:
            loop.close()


def command(args):
    run_id = args.run_id or ('fix-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    root = None
    try:
        root = bridge.checked_project_root(args.root)
        request = args.request
        if args.request_file:
            request = bridge.file_path(root, args.request_file).read_text(encoding='utf-8')
        result = execute(root, request, run_id=run_id, profile_name=args.profile)
    except (ValueError, OSError) as exc:
        result = {'outcome': 'blocked', 'run_id': run_id, 'run': None,
                  'blocker': blocker(str(exc)), 'acceptance': 'not_accepted'}
    if args.json:
        print(core.canonical_json(result), end='')
    elif result['outcome'] == 'verified_diff':
        run = result['run']
        print(('Verified diff: ' if result['diff']['files'] else 'Already satisfied; empty verified diff: ') + str(root / result['diff']['path']))
        print(f"Changed files: {len(result['diff']['files'])}; agent calls: {run['agent_calls']}; repair rounds: {run['repair_rounds_used']}")
        print('Checks passed and a fresh verifier reviewed the exact candidate. ' + result['next_action'])
    else:
        item = result['blocker']
        print('Blocked [' + item['code'] + ']: ' + item['reason'])
        if item['findings']:
            print('Latest findings: ' + json.dumps(item['findings'], ensure_ascii=False))
        print('Next action: ' + item['next_action'])
        evidence = root / '.proof' / 'tasks' / run_id if root else None
        print('Run: ' + run_id + '; evidence: ' + str(evidence) if evidence and evidence.is_dir() else 'No run was started.')
    return 0 if result['outcome'] == 'verified_diff' else 2
