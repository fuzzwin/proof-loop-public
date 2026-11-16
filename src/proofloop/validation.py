"""Pure validation of plans, protocol responses, and recorded run evidence."""

import json
import sys

from . import core
from .coordinator import require
from .filesystem import directory_names, normalized_name, relative_name


PLAN_FIELDS = {'goal', 'files', 'editable', 'protected', 'quality', 'specialists',
               'max_repairs', 'max_agent_calls', 'agent_timeout_seconds',
               'check_timeout_seconds', 'max_run_seconds'}


def validate_plan(plan, path, *, legacy_paths=False):
    require(isinstance(plan, dict) and PLAN_FIELDS <= set(plan) and
            set(plan) <= PLAN_FIELDS | {'requirements', 'test_adapter', 'delivery'}, 'invalid agent plan fields')
    require(isinstance(plan['goal'], str) and 0 < len(plan['goal'].strip()) <= 20000, 'goal required')
    for key in ('files', 'editable', 'protected'):
        value = plan[key]
        require(isinstance(value, list) and value and len(value) <= 200, key + ' must be a bounded nonempty list')
        for name in value:
            relative_name(name, legacy=legacy_paths)
        require(len(value) == len(set(value)), key + ' contains duplicate paths')
        if not legacy_paths:
            require(len(value) == len({normalized_name(name) for name in value}), key + ' contains ambiguous path aliases')
    if not legacy_paths:
        directories = directory_names(plan['files'])
        require(len(directories) == len({normalized_name(name) for name in directories}),
                'declared parent directories contain ambiguous aliases')
    require(set(plan['editable']) <= set(plan['files']), 'editable paths must be declared files')
    require(set(plan['protected']) <= set(plan['files']), 'protected paths must be declared files')
    require(not set(plan['editable']) & set(plan['protected']), 'protected files cannot be editable')
    require((path not in plan['editable']) if legacy_paths else
            all(normalized_name(path) != normalized_name(name) for name in plan['editable']), 'plan cannot be editable')
    for key, low, high in [('max_repairs', 0, 2), ('max_agent_calls', 1, 12),
                           ('agent_timeout_seconds', 1, 600), ('check_timeout_seconds', 1, 300),
                           ('max_run_seconds', 1, 1800)]:
        require(type(plan[key]) is int and low <= plan[key] <= high, key + ' outside safe limit')
    require(isinstance(plan['quality'], list) and 1 <= len(plan['quality']) <= 8, 'quality commands required')
    for argv in plan['quality']:
        require(isinstance(argv, list) and argv and all(isinstance(a, str) and a and '\x00' not in a for a in argv),
                'quality commands must be explicit argv arrays, never shell strings')
    require(plan.get('delivery', 'apply') in ('diff', 'apply'), 'delivery must be diff or apply')
    requirements = plan.get('requirements', [])
    require(isinstance(requirements, list) and len(requirements) <= 50, 'invalid requirements')
    ids = set()
    for item in requirements:
        require(isinstance(item, dict) and set(item) == {'id', 'description'}, 'invalid requirement')
        require(isinstance(item['id'], str) and core.RUN_ID_RE.fullmatch(item['id']) and item['id'] not in ids,
                'requirement IDs must be unique')
        require(isinstance(item['description'], str) and 0 < len(item['description'].strip()) <= 4000,
                'requirement description required')
        ids.add(item['id'])
    if 'test_adapter' in plan:
        adapter = plan['test_adapter']
        require(isinstance(adapter, dict) and set(adapter) == {'type', 'tests', 'expected_ids'} and
                adapter['type'] == 'python-unittest', 'unsupported test adapter')
        require(isinstance(adapter['tests'], list) and adapter['tests'] and
                len(set(adapter['tests'])) == len(adapter['tests']) and
                all(n in plan['protected'] and n.endswith('.py') for n in adapter['tests']),
                'adapter tests must be exact protected Python files')
        require(isinstance(adapter['expected_ids'], list) and adapter['expected_ids'] and
                all(isinstance(n, str) and n.strip() for n in adapter['expected_ids']) and
                len(set(adapter['expected_ids'])) == len(adapter['expected_ids']), 'expected test IDs required')
    require(isinstance(plan['specialists'], list) and len(plan['specialists']) <= 2, 'at most two specialist roles')
    ids = set()
    for role in plan['specialists']:
        require(isinstance(role, dict) and set(role) == {'id', 'instructions', 'when'}, 'invalid specialist')
        require(isinstance(role['id'], str) and core.RUN_ID_RE.fullmatch(role['id']) and role['id'] not in ids,
                'unique specialist ID required')
        ids.add(role['id'])
        require(isinstance(role['instructions'], str) and 0 < len(role['instructions']) <= 10000, 'specialist instructions required')
        require(isinstance(role['when'], list) and role['when'] and
                all(x in ('quality_failure', 'verification_failure') for x in role['when']), 'invalid specialist trigger')
    return plan


def schema_for(role, requirements=None):
    strings = {'type': 'array', 'items': {'type': 'string'}}
    if role == 'builder':
        properties = {'summary': {'type': 'string'}, 'changes': {'type': 'array', 'items': {
            'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
            'required': ['path', 'content'], 'additionalProperties': False}}}
    elif role == 'verifier':
        properties = {'verdict': {'type': 'string', 'enum': ['pass', 'fail']},
                      'candidate_sha256': {'type': 'string'}, 'findings': strings}
    else:
        properties = {'findings': strings}
    if role == 'verifier' and requirements:
        properties['requirements'] = {'type': 'array', 'items': {'type': 'object', 'properties': {
            'id': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['pass', 'fail', 'not_checked']},
            'evidence': {'type': 'array', 'items': {'type': 'string'}}, 'reason': {'type': 'string'}},
            'required': ['id', 'status', 'evidence', 'reason'], 'additionalProperties': False}}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def validate_response(role, value, candidate, requirements=None):
    require(isinstance(value, dict) and set(value) == set(schema_for(role, requirements)['properties']), 'invalid agent response fields')
    if role == 'builder':
        require(isinstance(value['summary'], str) and isinstance(value['changes'], list), 'invalid builder response')
        require(len(value['changes']) <= 200, 'too many proposed changes')
        for change in value['changes']:
            require(isinstance(change, dict) and set(change) == {'path', 'content'} and
                    isinstance(change['content'], str), 'invalid proposed change')
            relative_name(change['path'])
    else:
        require(isinstance(value['findings'], list) and all(isinstance(x, str) for x in value['findings']), 'invalid findings')
        if role == 'verifier':
            require(value['candidate_sha256'] == candidate, 'verifier reviewed a different candidate')
            require(value['verdict'] in ('pass', 'fail'), 'invalid verifier verdict')
            require((value['verdict'] == 'pass') == (not value['findings']), 'verdict/findings disagree')
    if role == 'verifier' and requirements:
        rows = value['requirements']
        require(isinstance(rows, list) and len(rows) == len(requirements), 'every requirement needs an assessment')
        seen = set()
        for row in rows:
            require(isinstance(row, dict) and set(row) == {'id', 'status', 'evidence', 'reason'}, 'invalid assessment')
            require(isinstance(row['id'], str) and row['id'] not in seen, 'duplicate requirement assessment')
            seen.add(row['id'])
            require(row['status'] in ('pass', 'fail', 'not_checked') and isinstance(row['reason'], str) and
                    bool(row['reason'].strip()), 'assessment status and reason required')
            require(isinstance(row['evidence'], list) and all(isinstance(e, str) and e.strip() for e in row['evidence']),
                    'invalid requirement evidence')
            require(row['status'] != 'pass' or bool(row['evidence']), 'passing requirement needs evidence')
        require(seen == {r['id'] for r in requirements}, 'requirement IDs do not match frozen plan')
        require(value['verdict'] != 'pass' or all(r['status'] == 'pass' for r in rows), 'unchecked requirement cannot pass')
    return value


def validate_test_report(report, adapter, run=None, *, check_runtime=True):
    require(isinstance(report, dict) and type(report.get('schema_version')) is int and
            report['schema_version'] in (1, 2), 'invalid test adapter report')
    base_fields = {'schema_version', 'runtime', 'tests', 'tests_run', 'passed', 'test_files'}
    require(set(report) == (base_fields if report['schema_version'] == 1 else
                            base_fields | {'collection_errors', 'fixture_errors', 'coverage'}),
            'invalid test adapter report')
    runtime = report['runtime']
    require(isinstance(runtime, dict) and set(runtime) == {'executable', 'version'} and
            all(isinstance(v, str) and v.strip() for v in runtime.values()), 'invalid test adapter runtime')
    if check_runtime:
        require(runtime == {'executable': sys.executable, 'version': sys.version}, 'test adapter runtime mismatch')
    rows = report['tests']
    require(isinstance(rows, list) and type(report['tests_run']) is int and len(rows) == report['tests_run'],
            'empty or incomplete test adapter report')
    require(all(isinstance(r, dict) and set(r) == {'id', 'status'} and isinstance(r['id'], str) and
                r['status'] in {'pass', 'fail', 'error', 'skipped', 'expected_failure', 'unexpected_success', 'incomplete'}
                for r in rows), 'invalid test outcome')
    ids = [r['id'] for r in rows]
    coverage = {'missing': sorted(set(adapter['expected_ids']) - set(ids)),
                'unexpected': sorted(set(ids) - set(adapter['expected_ids'])),
                'duplicate': sorted({name for name in ids if ids.count(name) > 1})}
    if report['schema_version'] == 1:
        require(rows, 'empty or incomplete test adapter report')
        require(not any(coverage.values()), 'test IDs differ from frozen expectation')
        errors = False
    else:
        require(report['coverage'] == coverage, 'test adapter coverage differs from frozen expectation')
        collection, fixtures = report['collection_errors'], report['fixture_errors']
        require(isinstance(collection, list) and all(isinstance(e, dict) and set(e) == {'file', 'error'} and
                e['file'] in adapter.get('tests', []) and isinstance(e['error'], str) and e['error'].strip()
                for e in collection), 'invalid test collection errors')
        require(isinstance(fixtures, list) and all(isinstance(e, dict) and set(e) == {'id', 'status', 'detail'} and
                isinstance(e['id'], str) and e['id'].strip() and e['status'] in ('error', 'skipped') and
                isinstance(e['detail'], str) and e['detail'].strip() for e in fixtures), 'invalid test fixture errors')
        errors = bool(collection or fixtures or any(coverage.values()))
    passed = bool(rows) and all(r['status'] == 'pass' for r in rows) and not errors
    require(type(report['passed']) is bool and report['passed'] == passed and
            (run is None or run['exit_code'] == (0 if passed else 1)),
            'test outcomes disagree with process exit')
    return report


def parse_json(raw):
    """Decode JSON with unique keys and finite numbers."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON number: ' + value)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    # Also catches overflow such as 1e999, including in unused metadata.
    json.dumps(value, allow_nan=False)
    return value


def is_sha256(value):
    """Return whether the value is a lowercase hexadecimal SHA-256 digest."""
    return (isinstance(value, str) and len(value) == 64 and
            all(c in '0123456789abcdef' for c in value))


def validate_source_map(value, plan):
    """Require a hash for each declared file except an absent editable file."""
    require(isinstance(value, dict) and set(value) == set(plan['files']),
            'source map must exactly match declared files')
    for name, checksum in value.items():
        require(is_sha256(checksum) or (checksum is None and name in plan['editable']),
                'invalid source hash or missing noneditable file: ' + name)


def validate_file_modes(value, hashes, plan):
    """Require saved file modes to agree with the frozen source map."""
    require(isinstance(value, dict) and set(value) == set(plan['files']), 'mode map must match declared files')
    for name, mode in value.items():
        require((mode is None and hashes[name] is None) or
                (type(mode) is int and 0 <= mode <= 0o777 and hashes[name] is not None),
                'invalid recorded file mode: ' + name)


def validate_directory_modes(value, plan):
    """Require the exact set of declared file parent directories and their modes."""
    require(isinstance(value, dict) and set(value) == directory_names(plan['files']) and
            all(type(mode) is int and 0 <= mode <= 0o777 for mode in value.values()),
            'directory mode map must match declared file parents')


def validate_process_result(value):
    """Require a valid recorded process ID, exit code, duration, and argv."""
    import math
    require(isinstance(value, dict), 'process summary must be an object')
    require(type(value.get('pid')) is int and value['pid'] > 0, 'invalid recorded PID')
    require(type(value.get('exit_code')) is int, 'invalid recorded exit code')
    elapsed = value.get('elapsed_seconds')
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0,
            'invalid elapsed seconds')
    argv = value.get('argv')
    require(isinstance(argv, list) and argv and
            all(isinstance(x, str) and x and '\x00' not in x for x in argv),
            'invalid recorded argv')


def summarize_stage_result(stage, result, plan=None, expected=None):
    """Validate and extract the quality or verifier report for a recorded stage."""
    require(isinstance(result, dict), 'stage result must be an object')
    plan = plan or {}
    if stage.startswith(('quality-', 'recheck-')):
        require(type(result.get('passed')) is bool, 'quality.passed must be boolean')
        commands = result.get('commands')
        require(isinstance(commands, list) and commands, 'quality.commands must be a nonempty list')
        for command_result in commands:
            validate_process_result(command_result)
            require(isinstance(command_result.get('stdout'), str) and
                    isinstance(command_result.get('stderr'), str), 'invalid quality output')
        tests = result.get('test_adapter')
        if 'test_adapter' in plan:
            validate_test_report(tests, plan['test_adapter'], check_runtime=False)
            require(tests['test_files'] == {n: expected[n] for n in plan['test_adapter']['tests']},
                    'test file hashes differ from frozen candidate')
        else:
            require(tests is None, 'unexpected test adapter report')
        require(result['passed'] == (all(x['exit_code'] == 0 for x in commands) and
                                    (tests is None or tests['passed'])),
                'quality verdict disagrees with exit codes')
        summary = {'passed': result['passed'], 'commands': commands}
        if tests is not None:
            summary['test_adapter'] = tests
        return 'quality', summary
    if stage.startswith('verifier-'):
        validate_process_result(result)
        require(result['exit_code'] == 0, 'completed verifier process failed')
        thread = result.get('thread_id')
        require(isinstance(thread, str) and thread.strip(), 'nonempty verifier thread ID required')
        response = result.get('response')
        require(isinstance(response, dict) and is_sha256(response.get('candidate_sha256')),
                'invalid verifier candidate hash')
        validate_response('verifier', response, response['candidate_sha256'], plan.get('requirements'))
        return 'verifier', {**response, 'thread_id': thread}
    return None, None
