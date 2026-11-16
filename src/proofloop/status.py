"""Read-only inspection of saved bridge evidence and its freshness."""

import hashlib
import json
import os
import time

from . import core
from .coordinator import require
from .filesystem import (
    evidence_directory_mode,
    evidence_path_parts,
    fingerprint,
    open_evidence_root,
    read_evidence_bytes,
    relative_name,
)
from .validation import (
    is_sha256,
    parse_json,
    summarize_stage_result,
    validate_directory_modes,
    validate_file_modes,
    validate_plan,
    validate_source_map,
)


# The observer deliberately has no dependency on an instantiated execution host,
# coordinator, lock, or database. Keep its validation separate from resume logic.
def status_report(run_id):
    return {'schema_version': 1, 'run_id': run_id if isinstance(run_id, str) else None,
            'state': None, 'phase': None, 'active': None, 'reason': None,
            'cancellation_requested': None,
            'budgets': {name: {'used': None, 'limit': None, 'remaining': None}
                        for name in ('repairs', 'agent_calls')},
            'deadline': None, 'time_remaining_seconds': None,
            'quality': None, 'verifier': None,
            'freshness': {'state': 'unreadable', 'issues': []}, 'historical': True,
            'acceptance': 'not_accepted', 'classification': 'hash_consistency_only'}


def read_status(root, run_id, plan_path, *, plan_validator=None, summary_reader=None,
                include_candidate=False):
    """Observe saved runs without constructing a host. Matching hashes are not authentication."""
    import math
    import re
    report = status_report(run_id)
    issues = report['freshness']['issues']
    severity = 'current'
    priorities = {'current': 0, 'stale': 1, 'unreadable': 2, 'indeterminate': 3}
    failures = (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
                NotImplementedError)
    snapshots = []
    root_fd = None

    def issue(kind, message):
        nonlocal severity
        if priorities[kind] > priorities[severity]:
            severity = kind
        issues.append(message)

    def check_hash(name, expected, *, expected_mode=None, check_mode=False):
        try:
            raw, mode = read_evidence_bytes(root_fd, name, optional=True, include_mode=True)
            actual = hashlib.sha256(raw).hexdigest() if raw is not None else None
            if actual != expected:
                issue('stale', 'hash changed or file missing: ' + name)
            if check_mode and mode != expected_mode:
                issue('stale', 'file permissions changed: ' + name)
        except failures as exc:
            issue('unreadable', 'cannot inspect ' + name + ': ' + str(exc))

    def check_directory(name, expected):
        try:
            if evidence_directory_mode(root_fd, name) != expected:
                issue('stale', 'directory permissions changed: ' + name)
        except FileNotFoundError:
            issue('stale', 'directory missing: ' + name)
        except failures as exc:
            issue('unreadable', 'cannot inspect directory ' + name + ': ' + str(exc))

    def check_private_directory(name, label):
        try:
            mode, has_acl = evidence_directory_mode(root_fd, name, include_acl=True)
            if mode & 0o077 or has_acl:
                issue('stale', label + ' evidence directory is not private')
        except failures as exc:
            issue('unreadable', 'cannot inspect ' + label + ' evidence privacy: ' + str(exc))

    try:
        require(isinstance(run_id, str) and core.RUN_ID_RE.fullmatch(run_id), 'invalid bridge run ID')
        plan_parts = evidence_path_parts(plan_path)
        managed = plan_parts == ('.proof', 'tasks', run_id, 'plan.json')
        root_fd = open_evidence_root(root)
        relative = f'.proof/agent-runs/{run_id}'
        state_name, cancel_name = relative + '/state.json', relative + '/cancel.json'
        raw_state = read_evidence_bytes(root_fd, state_name, optional=True)
        snapshots.append((state_name, raw_state))
        raw_cancel = read_evidence_bytes(root_fd, cancel_name, optional=True)
        snapshots.append((cancel_name, raw_cancel))
        report['cancellation_requested'] = raw_cancel is not None
        require(raw_state is not None, 'run state is missing')
        state = parse_json(raw_state)
        require(isinstance(state, dict), 'run state must be an object')
        require(type(state.get('schema_version')) is int and state['schema_version'] in (1, 2, 3),
                'unsupported state schema')
        isolated = state['schema_version'] >= 2
        mode_bound = state['schema_version'] == 3
        if mode_bound:
            check_private_directory(relative, 'run')
        if not managed:
            relative_name(plan_path, legacy=not mode_bound)
        report['state_schema_version'] = state['schema_version']
        require(state.get('run_id') == run_id, 'state run identity mismatch')
        require(isinstance(state.get('status'), str) and state['status'] in
                ('checkpoint', 'running', 'completed_advisory', 'blocked', 'blocked_uncertain', 'cancelled'),
                'invalid recorded state')
        report['state'] = state['status']
        require(isinstance(state.get('phase'), str) and state['phase'] in
                ('quality', 'verifier', 'builder', 'specialist', 'recheck', 'deliver', 'promote'), 'invalid recorded phase')
        report['phase'] = state['phase']
        require(state.get('reason') is None or isinstance(state['reason'], str), 'invalid reason')
        report['reason'] = state.get('reason')
        require('active' in state, 'active field is missing')
        active = state['active']
        if active is not None:
            issue('indeterminate', 'unfinished active stage; candidate may be changing')
            require(isinstance(active, dict) and set(active) == {'stage', 'coordinator_run'} and
                    all(isinstance(x, str) and x.strip() for x in active.values()),
                    'invalid active stage')
            report['active'] = active
        require(isinstance(state.get('records'), list), 'records must be a list')
        require(isinstance(state.get('threads'), list) and
                all(isinstance(x, str) and x.strip() for x in state['threads']) and
                len(set(state['threads'])) == len(state['threads']), 'invalid thread list')
        require(is_sha256(state.get('plan_sha256')), 'invalid frozen plan hash')
        deadline = state.get('deadline')
        require(type(deadline) in (int, float) and math.isfinite(deadline), 'invalid deadline')
        report['deadline'] = deadline
        report['time_remaining_seconds'] = max(0, deadline - time.time())
        raw_plan = read_evidence_bytes(root_fd, plan_path)
        plan = (plan_validator(parse_json(raw_plan), plan_path) if plan_validator else
                validate_plan(parse_json(raw_plan), plan_path, legacy_paths=not mode_bound))
        for name in plan['files']:
            evidence_path_parts(name)
        if hashlib.sha256(raw_plan).hexdigest() != state['plan_sha256']:
            issue('stale', 'frozen plan hash changed')
        for name, counter, limit in (('repairs', 'round', 'max_repairs'),
                                     ('agent_calls', 'agent_calls', 'max_agent_calls')):
            used = state.get(counter)
            report['budgets'][name]['limit'] = plan[limit]
            require(type(used) is int and 0 <= used <= plan[limit], 'invalid counter: ' + counter)
            report['budgets'][name].update(used=used, remaining=max(0, plan[limit] - used))
        require(len(state['threads']) <= state['agent_calls'], 'thread count exceeds agent calls')
        validate_source_map(state.get('expected'), plan)
        validate_source_map(state.get('initial'), plan)
        if include_candidate:
            report['candidate_sha256'] = fingerprint(state['expected'])
        for name in plan['protected']:
            require(state['expected'][name] == state['initial'][name], 'protected source map changed: ' + name)
        if isolated:
            validate_source_map(state.get('source_expected'), plan)
            if mode_bound:
                validate_file_modes(state.get('initial_modes'), state['initial'], plan)
                validate_file_modes(state.get('source_modes'), state['source_expected'], plan)
                validate_file_modes(state.get('candidate_modes'), state['expected'], plan)
                validate_directory_modes(state.get('directory_modes'), plan)
            require(is_sha256(state.get('adapter_sha256')), 'invalid test adapter hash')
            require(isinstance(state.get('candidate_history'), list) and state['candidate_history'] and
                    all(is_sha256(x) for x in state['candidate_history']) and
                    state['candidate_history'][0] == fingerprint(state['initial'], state['initial_modes'] if mode_bound else None, state['directory_modes'] if mode_bound else None) and
                    state['candidate_history'][-1] == fingerprint(state['expected'], state['candidate_modes'] if mode_bound else None, state['directory_modes'] if mode_bound else None), 'invalid candidate history')
            for name in set(plan['files']) - set(plan['editable']):
                require(state['expected'][name] == state['initial'][name] == state['source_expected'][name],
                        'noneditable source map changed: ' + name)
                if mode_bound:
                    require(state['candidate_modes'][name] == state['initial_modes'][name] == state['source_modes'][name],
                            'noneditable mode map changed: ' + name)
            report['delivery'] = plan.get('delivery', 'apply')
            report['candidate_sha256'] = fingerprint(state['expected'], state['candidate_modes'] if mode_bound else None, state['directory_modes'] if mode_bound else None)
            for name, expected in state['source_expected'].items():
                check_hash(name, expected, expected_mode=state['source_modes'][name] if mode_bound else None, check_mode=mode_bound)
            for name, expected in state['expected'].items():
                check_hash(relative + '/candidate/' + name, expected,
                           expected_mode=state['candidate_modes'][name] if mode_bound else None, check_mode=mode_bound)
            if mode_bound:
                for name, expected in state['directory_modes'].items():
                    check_directory(name, expected)
                    check_directory(relative + '/candidate' + ('' if name == '.' else '/' + name), expected)
            if report['delivery'] == 'diff':
                require(state['source_expected'] == state['initial'], 'diff run changed its source baseline')
                if mode_bound:
                    require(state['source_modes'] == state['initial_modes'], 'diff run changed its source permission baseline')
        else:
            for name, expected in state['expected'].items():
                check_hash(name, expected)
        if managed:
            require(isolated, 'managed plans require isolated run state')
            if mode_bound:
                check_private_directory(f'.proof/tasks/{run_id}', 'task')
            intake_name = f'.proof/tasks/{run_id}/intake.json'
            raw_intake = read_evidence_bytes(root_fd, intake_name)
            snapshots.append((intake_name, raw_intake))
            intake = parse_json(raw_intake)
            require(isinstance(intake, dict) and intake.get('run_id') == run_id and
                    type(intake.get('schema_version')) is int and intake['schema_version'] == 1,
                    'invalid task intake')
            require(set(intake) == {'schema_version', 'run_id', 'request_sha256', 'profile_path',
                                    'profile_sha256', 'evidence'} and is_sha256(intake.get('request_sha256')),
                    'invalid task intake fields')
            evidence = intake.get('evidence')
            required = {f'.proof/tasks/{run_id}/{name}' for name in ('request.txt', 'profile.json', 'plan.json')}
            require(isinstance(evidence, dict) and set(evidence) == required and
                    all(is_sha256(v) for v in evidence.values()), 'invalid intake evidence')
            require(evidence[plan_path] == state['plan_sha256'], 'intake plan differs from run')
            require(evidence[f'.proof/tasks/{run_id}/request.txt'] == intake['request_sha256'],
                    'intake request hash differs from saved request')
            for name, expected in evidence.items():
                check_hash(name, expected)
            saved_request = read_evidence_bytes(root_fd, f'.proof/tasks/{run_id}/request.txt')
            saved_profile = parse_json(read_evidence_bytes(root_fd, f'.proof/tasks/{run_id}/profile.json'))
            require(hashlib.sha256(saved_request).hexdigest() == intake['request_sha256'],
                    'saved request differs from intake')
            require(isinstance(saved_profile, dict) and set(saved_profile) == {'schema_version', 'plan'} and
                    type(saved_profile['schema_version']) is int and saved_profile['schema_version'] == 1 and
                    isinstance(saved_profile['plan'], dict), 'invalid frozen profile')
            request = saved_request.decode('utf-8')
            expected_plan = {**saved_profile['plan'], 'goal': request, 'delivery': 'diff',
                             'requirements': [{'id': 'TASK', 'description': request}]}
            require(plan == expected_plan, 'saved plan differs from request and profile')
            profile_name = intake.get('profile_path')
            relative_name(profile_name, legacy=not mode_bound)
            require(is_sha256(intake.get('profile_sha256')), 'invalid intake profile hash')
            check_hash(profile_name, intake['profile_sha256'])
            current_profile = read_evidence_bytes(root_fd, profile_name, optional=True)
            if current_profile is not None and hashlib.sha256(current_profile).hexdigest() == intake['profile_sha256']:
                require(parse_json(current_profile) == saved_profile, 'current profile differs from frozen profile')
        steps = evidence_path_parts(relative + '/steps')
        delivered = None
        stages = []
        for index, record in enumerate(state['records']):
            summary_key = None
            try:
                require(isinstance(record, dict) and is_sha256(record.get('sha256')),
                        'invalid completed record')
                parts = evidence_path_parts(record.get('path'))
                require(parts[:len(steps)] == steps and len(parts) == len(steps) + 2 and
                        parts[-1] in (('result.json', 'recovered-result.json') if isolated else ('result.json',)),
                        'completed record must be in this run steps')
                require(record.get('coordinator_run') == f'agent:{run_id}:{index}',
                        'completed record identity mismatch')
                # Invalidate the previous summary if the latest record of this
                # kind is unreadable, instead of implying an old pass is latest.
                for kind in ('quality', 'recheck', 'verifier'):
                    if parts[-2].startswith(f'{index:03d}-{kind}-'):
                        kind = 'quality' if kind == 'recheck' else kind
                        summary_key = kind
                        report[kind] = None
                raw = read_evidence_bytes(root_fd, record['path'])
                if hashlib.sha256(raw).hexdigest() != record['sha256']:
                    issue('stale', 'completed result hash changed: ' + record['path'])
                data = parse_json(raw)
                require(isinstance(data, dict), 'completed result must be an object')
                stage = data.get('stage')
                require(isinstance(stage, str) and
                        re.fullmatch(r'(?:quality|recheck|verifier|builder)-[0-9]+|specialist-[0-9]+-[A-Za-z0-9][A-Za-z0-9_-]{0,63}|promote|deliver', stage),
                        'invalid completed stage')
                require(parts[-2] == f'{index:03d}-{stage}', 'stage directory mismatch')
                require(parts[-1] != 'recovered-result.json' or stage == 'promote', 'invalid recovered stage')
                stages.append(stage)
                evidence = data.get('evidence')
                require(isinstance(evidence, dict), 'nested evidence must be a hash map')
                stage_parts = parts[:-1]
                # Validate every path before reading any nested evidence.
                for name, expected in evidence.items():
                    nested = evidence_path_parts(name)
                    require(len(nested) > len(stage_parts) and nested[:len(stage_parts)] == stage_parts and
                            nested != parts, 'nested evidence must stay within its stage')
                    require(is_sha256(expected), 'invalid nested evidence hash')
                summary_key, summary = (summary_reader(stage, data.get('result')) if summary_reader else
                                        summarize_stage_result(stage, data.get('result'), plan, state['expected']))
                if summary_key:
                    report[summary_key] = summary
                if stage == 'deliver':
                    delivery_result = data.get('result')
                    if isinstance(delivery_result, dict) and set(delivery_result) == {'blocked_reason'}:
                        require(report['state'] != 'completed_advisory' and isinstance(delivery_result['blocked_reason'], str) and
                                delivery_result['blocked_reason'].strip(), 'invalid delivery blocker')
                    else:
                        fields = {'path', 'sha256', 'candidate_sha256', 'files'} | ({'apply_umask'} if mode_bound else set())
                        require(isolated and isinstance(delivery_result, dict) and
                                set(delivery_result) == fields and
                                delivery_result['path'] == '/'.join(stage_parts) + '/verified.patch' and
                                is_sha256(delivery_result['sha256']) and
                                delivery_result['sha256'] == evidence.get(delivery_result['path']) and
                                delivery_result['candidate_sha256'] == fingerprint(state['expected'], state['candidate_modes'] if mode_bound else None, state['directory_modes'] if mode_bound else None) and
                                delivery_result['files'] == sorted(n for n in plan['files'] if state['initial'][n] != state['expected'][n]),
                                'invalid delivered patch evidence')
                        if mode_bound and delivery_result['files']:
                            require(isinstance(delivery_result['apply_umask'], str) and
                                    re.fullmatch(r'[0-7]{3}', delivery_result['apply_umask']), 'invalid patch application umask')
                            for name, expected in state['expected'].items():
                                applied = '/'.join(stage_parts) + '/patch-check/workspace/' + name
                                if expected is not None:
                                    require(evidence.get(applied) == expected, 'applied patch evidence differs from candidate')
                                check_hash(applied, expected, expected_mode=state['candidate_modes'][name], check_mode=True)
                            for name, expected in state['directory_modes'].items():
                                applied_dir = '/'.join(stage_parts) + '/patch-check/workspace' + ('' if name == '.' else '/' + name)
                                check_directory(applied_dir, expected)
                        elif mode_bound:
                            require(delivery_result['apply_umask'] is None, 'empty patch has unexpected umask')
                        delivered = delivery_result
                for name, expected in evidence.items():
                    check_hash(name, expected)
            except failures as exc:
                if summary_key:
                    report[summary_key] = None
                issue('unreadable', f'completed record {index}: {exc}')
        if report['state'] == 'completed_advisory':
            if report['quality'] is None or report['verifier'] is None:
                issue('unreadable', 'completed run lacks valid quality/verifier records')
            elif (not report['quality']['passed'] or report['verifier']['verdict'] != 'pass' or
                  report['verifier']['candidate_sha256'] != fingerprint(state['expected'], state['candidate_modes'] if mode_bound else None, state['directory_modes'] if mode_bound else None)):
                issue('stale', 'completed run disagrees with latest recorded evidence')
            if isolated:
                require(state.get('final_candidate_sha256') == fingerprint(state['expected'], state['candidate_modes'] if mode_bound else None, state['directory_modes'] if mode_bound else None) and
                        report['verifier'] and state.get('final_verifier_thread') == report['verifier']['thread_id'],
                        'completion differs from verified candidate')
                require(state['records'] and state['phase'] == ('deliver' if report['delivery'] == 'diff' else 'promote'),
                        'completed run lacks final delivery stage')
                require(len(stages) >= 3 and stages[-1] == state['phase'] and stages[-2].startswith('recheck-') and
                        stages[-3].startswith('verifier-'), 'completion lacks verifier and final check sequence')
                if report['delivery'] == 'diff':
                    require(delivered is not None and delivered == state.get('diff'), 'diff differs from delivered evidence')
                    report['diff'] = delivered
                else:
                    require(state['source_expected'] == state['expected'], 'completed promotion differs from candidate')
                    if mode_bound:
                        require(state['source_modes'] == state['candidate_modes'], 'completed promotion mode differs from candidate')
        if state['schema_version'] == 2:
            issue('indeterminate', 'legacy isolated state does not record file permissions')
    except failures as exc:
        issue('unreadable', 'cannot inspect run: ' + str(exc))
    finally:
        # Compare exact bytes, including marker creation/removal. No retry or
        # lock can turn a changing observation into a current one.
        try:
            for name, before in snapshots:
                try:
                    after = read_evidence_bytes(root_fd, name, optional=True)
                    if after != before:
                        issue('indeterminate', 'changed during inspection: ' + name)
                except failures as exc:
                    issue('indeterminate', 'cannot recheck ' + name + ': ' + str(exc))
        finally:
            if root_fd is not None:
                os.close(root_fd)
    report['freshness']['state'] = severity
    report['historical'] = severity != 'current'
    return report


def status_command(args):
    report = read_status(args.root, args.run_id, args.plan)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0 if report['freshness']['state'] == 'current' else 2
