"""Versioned check adapters on a trusted local host; no acceptance promotion."""
import fnmatch
import hashlib
import json
from pathlib import Path
import sys

import agent_bridge as bridge
from coordinator import require

ADAPTERS = {
    'json-equals': 'Exact typed JSON assertions using JSON Pointers.',
    'text': 'Required and forbidden literal text in a UTF-8 artifact.',
    'python-unittest': 'Observed unittest counts; empty, skipped and incomplete suites fail.',
    'command-json': 'External tool adapter with explicit argv and a strict result protocol.',
}
MAX_CHECK_REPORT_BYTES = 256 * 1024


def strings(value, name, *, nonempty=True):
    require(isinstance(value, list) and len(value) <= 200 and
            (bool(value) or not nonempty) and
            all(isinstance(x, str) and 0 < len(x) <= 20000 and '\x00' not in x for x in value),
            name + ' must be a bounded list of nonempty strings')


def argv(value):
    strings(value, 'argv')
    require(len(value) <= 128, 'too many command arguments')


def validate_checks(checks, files, protected):
    require(isinstance(checks, list) and 1 <= len(checks) <= 16, '1..16 checks required')
    ids = set()
    common = {'id', 'purpose', 'criterion', 'adapter'}
    for check in checks:
        require(isinstance(check, dict), 'check must be an object')
        name = check.get('id')
        require(isinstance(name, str) and bridge.core.RUN_ID_RE.fullmatch(name) and name not in ids,
                'unique check ID required')
        ids.add(name)
        require(check.get('purpose') in ('verification', 'validation'), 'invalid check purpose')
        require(isinstance(check.get('criterion'), str) and 0 < len(check['criterion'].strip()) <= 20000,
                'each check needs an explicit criterion')
        kind = check.get('adapter')
        require(isinstance(kind, str) and kind in ADAPTERS, 'unknown check adapter')
        fields = {'json-equals': {'path', 'assertions'}, 'text': {'path', 'contains', 'excludes'},
                  'python-unittest': {'start_dir', 'pattern'}, 'command-json': {'argv', 'controls', 'coverage'}}[kind]
        require(set(check) == common | fields, 'invalid fields for check ' + name)
        if kind in ('json-equals', 'text'):
            bridge.relative_name(check['path'])
            require(check['path'] in files, 'check artifact must be a declared file')
        if kind == 'json-equals':
            assertions = check['assertions']
            require(isinstance(assertions, list) and 1 <= len(assertions) <= 200, 'JSON assertions required')
            for assertion in assertions:
                require(isinstance(assertion, dict) and set(assertion) == {'pointer', 'equals'},
                        'invalid JSON assertion')
                pointer_parts(assertion['pointer'])
                json.dumps(assertion['equals'], allow_nan=False)
        elif kind == 'text':
            strings(check['contains'], 'contains', nonempty=False)
            strings(check['excludes'], 'excludes', nonempty=False)
            require(check['contains'] or check['excludes'], 'at least one text assertion required')
        elif kind == 'python-unittest':
            directory = bridge.relative_name(check['start_dir'])
            pattern = check['pattern']
            require(isinstance(pattern, str) and 0 < len(pattern) <= 128 and
                    '/' not in pattern and '\\' not in pattern and '\x00' not in pattern,
                    'test pattern must be a bounded filename pattern')
            test_files = [p for p in files if p.startswith(directory + '/')]
            require(test_files and all(p in protected for p in test_files),
                    'all declared test-directory files must be protected')
            require(any(fnmatch.fnmatchcase(Path(p).name, pattern) for p in test_files),
                    'test pattern matches no declared protected tests')
        else:
            argv(check['argv'])
            strings(check['controls'], 'external check controls')
            require(len(set(check['controls'])) == len(check['controls']) and
                    set(check['controls']) <= set(protected), 'external check controls must be protected files')
            strings(check['coverage'], 'expected external coverage')
            require(len(set(check['coverage'])) == len(check['coverage']), 'duplicate expected coverage')
    require({x['purpose'] for x in checks} == {'verification', 'validation'},
            'declare both verification and validation criteria')
    return checks


def pointer_parts(pointer):
    require(isinstance(pointer, str) and len(pointer) <= 4096 and
            (pointer == '' or pointer.startswith('/')), 'invalid JSON Pointer')
    parts = [] if pointer == '' else pointer[1:].split('/')
    for part in parts:
        for index, char in enumerate(part):
            require(char != '~' or (index + 1 < len(part) and part[index + 1] in '01'),
                    'invalid JSON Pointer escape')
    return [p.replace('~1', '/').replace('~0', '~') for p in parts]


def resolve_pointer(value, pointer):
    for part in pointer_parts(pointer):
        if isinstance(value, list):
            require(part.isascii() and part.isdigit() and (part == '0' or not part.startswith('0')),
                    'invalid array index')
            value = value[int(part)]
        else:
            require(isinstance(value, dict), 'JSON Pointer traverses a scalar')
            value = value[part]
    return value


def exact(left, right):
    return json.dumps(left, sort_keys=True, separators=(',', ':'), allow_nan=False) == \
        json.dumps(right, sort_keys=True, separators=(',', ':'), allow_nan=False)


def value_hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def expand(arguments, workspace):
    return [sys.executable if a == '{python}' else a.replace('{workspace}', str(workspace))
            for a in arguments]


# The runner lives outside builder-editable files. Counts are observed on a
# trusted host, not authenticated against malicious code in the same process.
UNITTEST_DRIVER = '''import contextlib,io,json,os,sys,unittest
config=json.load(sys.stdin)
sys.path.insert(0,os.getcwd())
stream=io.StringIO()
with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
    suite=unittest.defaultTestLoader.discover(config['start_dir'],pattern=config['pattern'])
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
counts={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'skipped':len(result.skipped),'expected_failures':len(result.expectedFailures),
        'unexpected_successes':len(result.unexpectedSuccesses)}
passed=counts['tests']>0 and all(v==0 for k,v in counts.items() if k!='tests')
print(json.dumps({'counts':counts,'output':stream.getvalue()[-20000:]}))
sys.exit(0 if passed else 1)
'''


def validate_external(value, check_id, coverage):
    require(isinstance(value, dict) and set(value) ==
            {'schema_version', 'check_id', 'state', 'findings', 'coverage'}, 'invalid external check result')
    require(type(value['schema_version']) is int and value['schema_version'] == 1,
            'unsupported external check schema')
    require(value['check_id'] == check_id, 'external result names a different check')
    require(value['state'] in ('pass', 'fail'), 'external check must report pass or fail')
    strings(value['coverage'], 'coverage')
    require(value['coverage'] == coverage, 'external coverage does not match the frozen contract')
    strings(value['findings'], 'findings', nonempty=False)
    require((value['state'] == 'pass') == (not value['findings']), 'external state/findings disagree')
    return value


def summarize(checks):
    result = {}
    for purpose in ('verification', 'validation'):
        selected = [c for c in checks if c['purpose'] == purpose]
        failed = [c['id'] for c in selected if c['state'] != 'pass']
        result[purpose] = {'state': 'pass' if selected and not failed else 'fail',
                           'checked': [c['id'] for c in selected], 'failed': failed}
    return result


def run_checks(workspace, checks, directory, runner, remaining, cancelled, launched):
    results = []
    for index, check in enumerate(checks):
        if cancelled():
            raise bridge.BridgeStop('cancelled')
        remaining()
        folder = directory / ('check-' + str(index))
        folder.mkdir()
        kind, observed, process = check['adapter'], {}, None
        if kind in ('json-equals', 'text'):
            try:
                raw = bridge.file_path(workspace, check['path']).read_bytes()
                if kind == 'json-equals':
                    value = bridge._status_json(raw)
                    assertions = []
                    for assertion in check['assertions']:
                        try:
                            actual = resolve_pointer(value, assertion['pointer'])
                            assertions.append({'pointer': assertion['pointer'], 'actual_sha256': value_hash(actual),
                                               'matches': exact(actual, assertion['equals'])})
                        except (KeyError, IndexError, ValueError):
                            assertions.append({'pointer': assertion['pointer'], 'missing': True, 'matches': False})
                    observed = {'assertions': assertions}
                    passed = all(a['matches'] for a in assertions)
                else:
                    value = raw.decode('utf-8')
                    observed = {'missing': [s for s in check['contains'] if s not in value],
                                'forbidden': [s for s in check['excludes'] if s in value]}
                    passed = not observed['missing'] and not observed['forbidden']
            except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                observed, passed = {'error': str(exc)}, False
        else:
            arguments = ([sys.executable, '-I', '-B', '-c', UNITTEST_DRIVER] if kind == 'python-unittest'
                         else expand(check['argv'], workspace))
            request = {'start_dir': check['start_dir'], 'pattern': check['pattern']} if kind == 'python-unittest' else {
                'schema_version': 1, 'check_id': check['id'], 'criterion': check['criterion'],
                'purpose': check['purpose'], 'coverage': check['coverage']}
            process = runner.run(arguments, cwd=workspace, directory=folder, timeout=remaining(),
                                 cancelled=cancelled, launched=launched, stdin=json.dumps(request))
            remaining()
            try:
                output = (folder / 'stdout.log').read_bytes()
                require(len(output) <= MAX_CHECK_REPORT_BYTES, 'check report exceeds 256 KiB')
                observed = bridge._status_json(output)
                if kind == 'python-unittest':
                    require(isinstance(observed, dict) and set(observed) == {'counts', 'output'} and
                            isinstance(observed['output'], str), 'invalid unittest report')
                    counts = observed['counts']
                    require(isinstance(counts, dict) and set(counts) ==
                            {'tests', 'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'} and
                            all(type(v) is int and v >= 0 for v in counts.values()), 'invalid unittest counts')
                    passed = counts['tests'] > 0 and all(v == 0 for k, v in counts.items() if k != 'tests')
                    require(process['exit_code'] == (0 if passed else 1), 'unittest counts/exit disagree')
                else:
                    validate_external(observed, check['id'], check['coverage'])
                    require(process['exit_code'] >= 0, 'external check terminated by signal')
                    passed = observed['state'] == 'pass'
                    require((process['exit_code'] == 0) == passed, 'external result/exit disagree')
            except (ValueError, TypeError, KeyError, RecursionError) as exc:
                raise bridge.BridgeStop('check protocol uncertain: ' + check['id'] + ': ' + str(exc)) from exc
        result = {k: check[k] for k in ('id', 'purpose', 'adapter', 'criterion')}
        result.update(state='pass' if passed else 'fail', observations=observed,
                      contract=check,
                      evidence_kind='deterministic_file_check' if process is None else 'trusted_process_report')
        if process is not None:
            result['process'] = process
        require(len(bridge.core.canonical_json(result).encode()) <= MAX_CHECK_REPORT_BYTES,
                'check report exceeds 256 KiB')
        bridge.core.write_json(folder / 'check.json', result, exclusive=True)
        results.append(result)
    return {'passed': all(c['state'] == 'pass' for c in results), 'checks': results, **summarize(results)}
