#!/usr/bin/env python3
"""Frozen black-box acceptance for the native agent-status observer.

Only stdlib; no agent execution, credentials, database or project initialization.
Run from the candidate root: python3 pilot/acceptance.py
Fixtures and a copy of the CLI live outside the candidate in temporary directories.
"""
import contextlib
import hashlib
import importlib
import io
import json
import math
import os
import runpy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
CANDIDATE = Path(__file__).resolve().parent.parent
RUN = 'acceptance-run'
REQUIRED = {'schema_version', 'run_id', 'state', 'phase', 'active', 'reason',
            'cancellation_requested', 'budgets', 'deadline', 'time_remaining_seconds',
            'quality', 'verifier', 'freshness', 'historical', 'acceptance', 'classification'}


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def snapshot(root):
    """Ignore access times, but detect new entries, edits, modes and mtimes."""
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        value = ('symlink', os.readlink(path)) if path.is_symlink() else (
            ('file', digest(path)) if path.is_file() else ('directory',))
        result[str(path.relative_to(root))] = (value, info.st_mode, info.st_mtime_ns)
    return result


class IOAudit:
    """Audit both supported read APIs, including openat-style directory handles.

    Canonicalize the temporary base before hooks are installed: macOS /var and
    /private/var aliases must identify the same fixture. Keep lexical paths during
    callbacks so a replaced leaf still triggers; resolve only successful opens.
    Fixture mutations run with hooks suspended and never count as observer IO.
    """
    def __init__(self, base, before=None):
        self.base = Path(base).absolute()
        self.real_base = self.base.resolve()
        self.var_root = Path('/var').resolve()
        self.before = before
        self.opened = []
        self.fds = {}
        self.busy = False

    def path(self, value, dir_fd=None):
        path = Path(os.fsdecode(value))
        if not path.is_absolute():
            if dir_fd is not None:
                if dir_fd not in self.fds:
                    raise AssertionError('untracked dir_fd in observer open')
                path = self.fds[dir_fd] / path
            else:
                path = Path.cwd() / path
        path = Path(os.path.abspath(path))
        if path.is_relative_to('/var'):
            path = self.var_root / path.relative_to('/var')
        if path.is_relative_to(self.base):
            path = self.real_base / path.relative_to(self.base)
        return path

    def notify(self, path):
        if self.before:
            self.busy = True
            try:
                self.before(path)
            finally:
                self.busy = False

    def __enter__(self):
        self.original_path_open = Path.open
        self.original_os_open = os.open
        self.original_close = os.close
        def path_open(path, *args, **kwargs):
            if self.busy:
                return self.original_path_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get('mode', 'r')
            if 'r' not in mode and '+' not in mode:
                return self.original_path_open(path, *args, **kwargs)
            lexical = self.path(path)
            self.notify(lexical)
            stream = self.original_path_open(path, *args, **kwargs)
            self.opened.append(lexical.resolve())
            return stream
        def os_open(path, flags, mode=0o777, *, dir_fd=None):
            if self.busy:
                return self.original_os_open(path, flags, mode, dir_fd=dir_fd)
            lexical = self.path(path, dir_fd)
            self.notify(lexical)
            fd = self.original_os_open(path, flags, mode, dir_fd=dir_fd)
            actual = lexical.resolve()
            self.fds[fd] = actual
            self.opened.append(actual)
            return fd
        def close(fd):
            try:
                return self.original_close(fd)
            finally:
                self.fds.pop(fd, None)
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.object(Path, 'open', path_open))
        self.stack.enter_context(mock.patch.object(os, 'open', os_open))
        self.stack.enter_context(mock.patch.object(os, 'close', close))
        # Capability checks must describe the wrapped syscall, not fail merely
        # because instrumentation replaced its Python function identity.
        if self.original_os_open in os.supports_dir_fd:
            self.stack.enter_context(mock.patch.object(
                os, 'supports_dir_fd', os.supports_dir_fd | {os_open}))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


class Fixture:
    def __init__(self, root, completed=True):
        self.root = root
        root.mkdir()
        (root / 'app.py').write_text('answer = 42\n')
        (root / 'requirements.txt').write_text('Answer must be 42.\n')
        self.plan_path = root / 'plan.json'
        self.plan = {'goal': 'Keep answer equal to 42',
                     'files': ['app.py', 'requirements.txt', 'future.py'],
                     'editable': ['app.py', 'future.py'], 'protected': ['requirements.txt'],
                     'quality': [['{python}', '-c', 'assert True']],
                     'specialists': [{'id': 'diagnostic', 'instructions': 'Diagnose failure',
                                      'when': ['quality_failure', 'verification_failure']}],
                     'max_repairs': 2, 'max_agent_calls': 6,
                     'agent_timeout_seconds': 180, 'check_timeout_seconds': 120,
                     'max_run_seconds': 1800}
        write_json(self.plan_path, self.plan)
        self.directory = root / '.proof' / 'agent-runs' / RUN
        self.state_path = self.directory / 'state.json'
        self.cancel_path = self.directory / 'cancel.json'
        expected = {'app.py': digest(root / 'app.py'),
                    'requirements.txt': digest(root / 'requirements.txt'), 'future.py': None}
        self.candidate_hash = hashlib.sha256(encoded(expected)).hexdigest()
        self.state = {'schema_version': 1, 'run_id': RUN, 'plan_sha256': digest(self.plan_path),
                      'expected': expected, 'initial': expected.copy(), 'phase': 'quality',
                      'round': 0, 'agent_calls': 0, 'threads': [], 'deadline': time.time() + 1700,
                      'status': 'checkpoint', 'active': None, 'records': [], 'feedback': [],
                      'trigger': None}
        self.quality = {'passed': True, 'commands': [
            {'pid': 41001, 'exit_code': 0, 'elapsed_seconds': 0.1,
             'argv': ['python3', '-c', 'assert True'], 'stdout': 'ok\n', 'stderr': ''}]}
        response = {'verdict': 'pass', 'candidate_sha256': self.candidate_hash, 'findings': []}
        self.verifier = {'pid': 41002, 'exit_code': 0, 'elapsed_seconds': 0.2,
                         'argv': ['codex', 'exec'], 'thread_id': 'fixture-fresh-verifier',
                         'usage': {'input_tokens': 100, 'cached_input_tokens': 0, 'output_tokens': 20},
                         'response': response, 'requested_model': 'host default',
                         'observed_model': None,
                         'classification': 'native_agent_report_not_portable_acceptance'}
        if completed:
            self.add_stage('quality-0', self.quality, {
                'workspace/app.py': (root / 'app.py').read_bytes(),
                'workspace/requirements.txt': (root / 'requirements.txt').read_bytes(),
                'check-0/stdout.log': b'ok\n', 'check-0/stderr.log': b'',
                'check-0/cleanup.json': encoded({'status': 'complete', 'leader_reaped': True}),
                'launch-fixture.json': encoded({'pid': 41001, 'dispatch_ref': 'local-process:41001:fixture'})})
            self.add_stage('verifier-0', self.verifier, {
                'workspace/app.py': (root / 'app.py').read_bytes(),
                'workspace/requirements.txt': (root / 'requirements.txt').read_bytes(),
                'stdout.log': b''.join(encoded(x).replace(b'\n', b'') + b'\n' for x in [
                    {'type': 'thread.started', 'thread_id': 'fixture-fresh-verifier'},
                    {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(response)}},
                    {'type': 'turn.completed', 'usage': self.verifier['usage']}]),
                'stderr.log': b'', 'final.json': encoded(response),
                'schema.json': encoded({'type': 'object'}), 'prompt.txt': b'Inspect the frozen candidate.\n',
                'cleanup.json': encoded({'status': 'complete', 'leader_reaped': True}),
                'launch-fixture.json': encoded({'pid': 41002, 'dispatch_ref': 'local-process:41002:fixture'})})
            self.state.update(status='completed_advisory', phase='verifier', agent_calls=1,
                              threads=['fixture-fresh-verifier'], quality=self.quality,
                              final_candidate_sha256=self.candidate_hash,
                              final_verifier_thread='fixture-fresh-verifier')
        self.save()

    def save(self):
        write_json(self.state_path, self.state)

    def add_stage(self, stage, result, files):
        n = len(self.state['records'])
        directory = self.directory / 'steps' / f'{n:03d}-{stage}'
        evidence = {}
        for name, content in files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            evidence[str(path.relative_to(self.root))] = digest(path)
        path = directory / 'result.json'
        write_json(path, {'stage': stage, 'result': result, 'evidence': evidence})
        self.state['records'].append({'path': str(path.relative_to(self.root)), 'sha256': digest(path),
                                      'coordinator_run': f'agent:{RUN}:{n}'})

    def result(self, index):
        path = self.root / self.state['records'][index]['path']
        return path, json.loads(path.read_bytes())

    def rewrite_result(self, index, data):
        path, _ = self.result(index)
        write_json(path, data)
        self.state['records'][index]['sha256'] = digest(path)
        self.save()


class StatusAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program_temp = tempfile.TemporaryDirectory(prefix='proofloop-acceptance-code-')
        cls.code = Path(cls.program_temp.name)
        for path in CANDIDATE.glob('*.py'):
            shutil.copy2(path, cls.code / path.name)
        shutil.copy2(CANDIDATE / 'proofloop', cls.code / 'proofloop')
        cls.saved_modules = {name: sys.modules.pop(name, None) for name in
                             ('proofloop', 'agent_bridge', 'coordinator')}
        sys.path.insert(0, str(cls.code))
        cls.core = importlib.import_module('proofloop')
        cls.bridge = importlib.import_module('agent_bridge')
        cls.coordinator = importlib.import_module('coordinator')

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.code))
        for name, previous in cls.saved_modules.items():
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous
        cls.program_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='proofloop-acceptance-data-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture_count = 0

    def fixture(self, completed=True):
        self.fixture_count += 1
        return Fixture(self.base / f'project-{self.fixture_count}', completed)

    def arguments(self, fixture, run_id=RUN, plan='plan.json'):
        return ['agent-status', '--root', str(fixture.root), '--run-id', run_id, '--plan', plan]

    def decode(self, code, stdout, stderr, expected):
        self.assertEqual(code, expected, f'exit={code}; stdout={stdout!r}; stderr={stderr!r}')
        self.assertNotIn('Traceback', stdout + stderr)
        try:
            data = json.loads(stdout, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
        except (ValueError, TypeError) as exc:
            self.fail(f'agent-status must print one finite JSON object: {exc}; stdout={stdout!r}; stderr={stderr!r}')
        self.assertIsInstance(data, dict)
        self.assertTrue(REQUIRED <= set(data), f'missing interface fields: {REQUIRED - set(data)}')
        self.assertEqual(data['schema_version'], 1)
        self.assertIs(type(data['schema_version']), int)
        self.assertEqual(data['acceptance'], 'not_accepted')
        self.assertEqual(data['classification'], 'hash_consistency_only')
        self.assertIs(type(data['historical']), bool)
        self.assertIn(data['freshness']['state'], {'current', 'stale', 'indeterminate', 'unreadable'})
        issues = data['freshness']['issues']
        self.assertIsInstance(issues, list)
        self.assertTrue(all(isinstance(x, str) and x.strip() for x in issues))
        self.assertEqual(data['historical'], data['freshness']['state'] != 'current')
        if expected == 0:
            self.assertEqual(data['freshness']['state'], 'current')
            self.assertEqual(issues, [])
        else:
            self.assertNotEqual(data['freshness']['state'], 'current')
            self.assertTrue(issues)
        for key in ('deadline', 'time_remaining_seconds'):
            if data[key] is not None:
                self.assertIn(type(data[key]), (int, float))
                self.assertTrue(math.isfinite(data[key]))
        if data['time_remaining_seconds'] is not None:
            self.assertGreaterEqual(data['time_remaining_seconds'], 0)
        for name in ('repairs', 'agent_calls'):
            budget = data['budgets'][name]
            self.assertTrue({'used', 'limit', 'remaining'} <= set(budget))
            for value in (budget[k] for k in ('used', 'limit', 'remaining')):
                if value is not None:
                    self.assertIs(type(value), int)
            if all(budget[x] is not None for x in ('used', 'limit', 'remaining')):
                self.assertEqual(budget['remaining'], max(0, budget['limit'] - budget['used']))
        return data

    def cli(self, fixture, expected=0, entry='proofloop', **kwargs):
        before, code_before = snapshot(fixture.root), snapshot(self.code)
        home = self.base / 'empty-home'
        home.mkdir(exist_ok=True)
        env = {'PATH': str(home), 'HOME': str(home), 'CODEX_HOME': str(home / 'absent-codex'),
               'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
        # Intentionally no PYTHONDONTWRITEBYTECODE, credentials, or Codex executable.
        result = subprocess.run([sys.executable, str(self.code / entry), *self.arguments(fixture, **kwargs)],
                                cwd=self.code, env=env, text=True, capture_output=True, timeout=8)
        self.assertEqual(snapshot(fixture.root), before, 'status modified the project')
        self.assertEqual(snapshot(self.code), code_before, 'CLI wrote bytecode or modified its own files')
        self.assertEqual(list(home.iterdir()), [], 'status wrote home/auth/cache files')
        return self.decode(result.returncode, result.stdout, result.stderr, expected)

    def in_process(self, fixture, expected=0, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                with mock.patch.object(sys, 'argv', [str(self.code / 'proofloop'),
                                                      *self.arguments(fixture, **kwargs)]):
                    runpy.run_path(str(self.code / 'proofloop'), run_name='__main__')
                result = 0
            except SystemExit as exc:
                result = exc.code
        return self.decode(result, out.getvalue(), err.getvalue(), expected)

    def assert_summaries(self, data, fixture):
        self.assertIs(data['quality']['passed'], True)
        self.assertEqual({key: data['verifier'][key] for key in
                          ('verdict', 'thread_id', 'candidate_sha256', 'findings')},
                         {'verdict': 'pass', 'thread_id': 'fixture-fresh-verifier',
                          'candidate_sha256': fixture.candidate_hash, 'findings': []})

    def test_completed_native_records_and_public_cli_are_read_only(self):
        f = self.fixture()
        for entry in ('proofloop',):
            with self.subTest(entry=entry):
                data = self.cli(f, entry=entry)
                self.assertEqual(data['run_id'], RUN)
                self.assertEqual(data['state'], 'completed_advisory')
                self.assertEqual(data['phase'], 'verifier')
                self.assertIsNone(data['active'])
                self.assertIsNone(data['reason'])
                self.assertIs(data['cancellation_requested'], False)
                self.assertEqual(data['budgets']['repairs'], {'used': 0, 'limit': 2, 'remaining': 2})
                self.assertEqual(data['budgets']['agent_calls'], {'used': 1, 'limit': 6, 'remaining': 5})
                self.assertEqual(data['deadline'], f.state['deadline'])
                self.assert_summaries(data, f)

    def test_checkpoint_and_state_only_quality_are_not_completed_evidence(self):
        f = self.fixture(False)
        f.state['quality'] = {'passed': True}
        f.save()
        data = self.cli(f)
        self.assertEqual(data['state'], 'checkpoint')
        self.assertIsNone(data['quality'])
        self.assertIsNone(data['verifier'])

    def test_stable_blocked_and_cancelled_are_current(self):
        for state in ('blocked', 'cancelled'):
            with self.subTest(state=state):
                f = self.fixture(False)
                f.state.update(status=state, reason='recorded fixture stop')
                f.save()
                if state == 'cancelled':
                    write_json(f.cancel_path, {'requested_at': '2026-10-26T00:00:00Z'})
                data = self.cli(f)
                self.assertEqual(data['state'], state)
                self.assertEqual(data['reason'], 'recorded fixture stop')
                self.assertEqual(data['cancellation_requested'], state == 'cancelled')

    def test_active_stage_is_indeterminate_regardless_of_recorded_state(self):
        for state in ('running', 'blocked_uncertain', 'cancelled'):
            with self.subTest(state=state):
                f = self.fixture(False)
                active = {'stage': 'quality-0', 'coordinator_run': f'agent:{RUN}:0'}
                f.state.update(status=state, active=active)
                f.save()
                data = self.cli(f, 2)
                self.assertEqual(data['state'], state)
                self.assertEqual(data['active'], active)
                self.assertEqual(data['freshness']['state'], 'indeterminate')

    def test_expired_completed_run_and_exhausted_budgets_do_not_advance(self):
        f = self.fixture()
        f.state.update(deadline=1, round=2, agent_calls=6)
        f.save()
        data = self.cli(f)
        self.assertEqual(data['state'], 'completed_advisory')
        self.assertEqual(data['time_remaining_seconds'], 0)
        self.assertEqual(data['budgets']['repairs']['remaining'], 0)
        self.assertEqual(data['budgets']['agent_calls']['remaining'], 0)

    def test_plan_source_record_and_nested_log_drift_preserve_recorded_summaries(self):
        for kind in ('plan', 'source', 'result', 'nested'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'plan':
                    f.plan['goal'] += ' changed'
                    write_json(f.plan_path, f.plan)
                elif kind == 'source':
                    (f.root / 'app.py').write_text('answer = 0\n')
                elif kind == 'result':
                    path, _ = f.result(0)
                    path.write_bytes(path.read_bytes() + b'\n')
                else:
                    path, _ = f.result(1)
                    (path.parent / 'stdout.log').write_text('changed hash evidence\n')
                data = self.cli(f, 2)
                self.assertEqual(data['freshness']['state'], 'stale')
                self.assertEqual(data['state'], 'completed_advisory')
                self.assert_summaries(data, f)

    def test_declared_missing_editable_creation_and_existing_source_deletion_are_stale(self):
        for kind in ('creation', 'deletion'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'creation':
                    (f.root / 'future.py').write_text('new = True\n')
                else:
                    (f.root / 'app.py').unlink()
                self.cli(f, 2)

    def test_latest_completed_quality_and_verifier_are_used(self):
        f = self.fixture()
        quality = {'passed': False, 'commands': [{**f.quality['commands'][0], 'exit_code': 1}]}
        verifier = {**f.verifier, 'thread_id': 'fixture-second-verifier',
                    'response': {'verdict': 'fail', 'candidate_sha256': f.candidate_hash,
                                 'findings': ['Requirement unmet']}}
        f.add_stage('quality-1', quality, {'check-0/stdout.log': b'failure\n'})
        f.add_stage('verifier-1', verifier, {'stdout.log': b'opaque native log hash evidence\n'})
        f.state.update(status='blocked', reason='repair limit reached', round=2, agent_calls=2,
                       threads=['fixture-fresh-verifier', 'fixture-second-verifier'])
        f.save()
        data = self.cli(f)
        self.assertIs(data['quality']['passed'], False)
        self.assertEqual(data['verifier']['verdict'], 'fail')
        self.assertEqual(data['verifier']['thread_id'], 'fixture-second-verifier')
        self.assertEqual(data['verifier']['findings'], ['Requirement unmet'])

    def test_missing_and_malformed_state_print_json_without_traceback(self):
        for raw in (None, b'{', b'[]', b'null', b'42', b'"state"'):
            with self.subTest(raw=raw):
                f = self.fixture()
                if raw is None:
                    f.state_path.unlink()
                else:
                    f.state_path.write_bytes(raw)
                data = self.cli(f, 2)
                self.assertIsNone(data['quality'])
                self.assertIsNone(data['verifier'])

    def test_malformed_state_types_identities_and_counters_are_rejected(self):
        cases = [('schema_version', 2), ('schema_version', True), ('run_id', 'other'),
                 ('status', []), ('phase', {}), ('records', {}), ('active', []),
                 ('expected', []), ('initial', []), ('round', True), ('round', -1),
                 ('round', 3), ('agent_calls', False), ('agent_calls', -1),
                 ('agent_calls', 7), ('deadline', True), ('deadline', 'later'),
                 ('deadline', float('nan')), ('deadline', float('inf')), ('deadline', float('-inf'))]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                f = self.fixture()
                f.state[key] = value
                f.save()
                self.cli(f, 2)

    def test_expected_source_map_must_exactly_match_declared_files(self):
        for kind in ('missing-key', 'extra-key', 'bad-hash', 'bool-hash'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'missing-key':
                    del f.state['expected']['future.py']
                elif kind == 'extra-key':
                    f.state['expected']['undeclared.txt'] = None
                else:
                    f.state['expected']['app.py'] = True if kind == 'bool-hash' else 'not-a-hash'
                f.save()
                self.cli(f, 2)

    def test_malformed_plans_are_rejected_even_when_their_hash_matches(self):
        cases = [[], None, {'quality': 'echo unsafe'}, {'max_repairs': True},
                 {'files': ['app.py', 'requirements.txt', '../outside']},
                 {'files': ['app.py', 'requirements.txt', './future.py']},
                 {'files': ['app.py', 'requirements.txt', 'a//b']}]
        for replacement in cases:
            with self.subTest(replacement=replacement):
                f = self.fixture()
                value = {**f.plan, **replacement} if isinstance(replacement, dict) else replacement
                write_json(f.plan_path, value)
                f.state['plan_sha256'] = digest(f.plan_path)
                f.save()
                self.cli(f, 2)

    def test_unsafe_run_and_plan_arguments_print_json(self):
        f = self.fixture()
        for value in ('../escape', '/absolute', '.', 'bad/name', 'x' * 65):
            with self.subTest(run_id=value):
                self.cli(f, 2, run_id=value)
        for value in ('../plan.json', str(f.plan_path), './plan.json', 'a/../plan.json', 'a//plan.json'):
            with self.subTest(plan=value):
                self.cli(f, 2, plan=value)

    def test_symlinked_plan_state_source_result_and_nested_evidence_are_rejected(self):
        for kind in ('plan', 'state', 'source', 'result', 'nested', 'steps-directory'):
            with self.subTest(kind=kind):
                f = self.fixture()
                result_path, _ = f.result(0)
                path = {'plan': f.plan_path, 'state': f.state_path, 'source': f.root / 'app.py',
                        'result': result_path, 'nested': result_path.parent / 'check-0/stdout.log',
                        'steps-directory': f.directory / 'steps'}[kind]
                outside = self.base / f'outside-{kind}'
                path.rename(outside)
                path.symlink_to(outside, target_is_directory=outside.is_dir())
                self.cli(f, 2)

    def test_malformed_completed_record_and_nested_evidence_maps_are_rejected(self):
        cases = ['record-scalar', 'record-no-path', 'record-bool-hash', 'result-list',
                 'result-no-evidence', 'evidence-list', 'evidence-bool-hash',
                 'quality-bool-number', 'verifier-findings-string', 'verifier-bad-verdict',
                 'verifier-empty-thread', 'quality-commands-string']
        for kind in cases:
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind.startswith('record-'):
                    if kind == 'record-scalar':
                        f.state['records'][0] = 1
                    elif kind == 'record-no-path':
                        del f.state['records'][0]['path']
                    else:
                        f.state['records'][0]['sha256'] = True
                    f.save()
                else:
                    index = 1 if kind.startswith('verifier-') else 0
                    _, data = f.result(index)
                    if kind == 'result-list':
                        data = []
                    elif kind == 'result-no-evidence':
                        del data['evidence']
                    elif kind == 'evidence-list':
                        data['evidence'] = []
                    elif kind == 'evidence-bool-hash':
                        data['evidence'][next(iter(data['evidence']))] = True
                    elif kind == 'quality-bool-number':
                        data['result']['passed'] = 1
                    elif kind == 'quality-commands-string':
                        data['result']['commands'] = 'not a command list'
                    elif kind == 'verifier-findings-string':
                        data['result']['response']['findings'] = 'none'
                    elif kind == 'verifier-bad-verdict':
                        data['result']['response']['verdict'] = 'approved'
                    else:
                        data['result']['thread_id'] = ''
                    f.rewrite_result(index, data)
                self.cli(f, 2)

    def test_record_and_nested_paths_are_confined_before_any_outside_read(self):
        for kind in ('external-record', 'other-run-record', 'external-nested', 'sibling-stage-nested'):
            with self.subTest(kind=kind):
                f = self.fixture()
                forbidden = self.base / f'secret-{kind}.json'
                _, result = f.result(0)
                write_json(forbidden, result)
                if kind == 'other-run-record':
                    forbidden = f.root / '.proof/agent-runs/other/steps/000-quality-0/result.json'
                    write_json(forbidden, result)
                if kind.endswith('record'):
                    f.state['records'][0]['path'] = (str(forbidden.relative_to(f.root))
                        if kind == 'other-run-record' else str(forbidden))
                    f.state['records'][0]['sha256'] = digest(forbidden)
                    f.save()
                else:
                    _, result = f.result(1)
                    if kind == 'sibling-stage-nested':
                        first, _ = f.result(0)
                        forbidden = first.parent / 'unlisted-sibling-secret.txt'
                        forbidden.write_bytes(b'unlisted sibling secret\n')
                    result['evidence'] = {(str(forbidden.relative_to(f.root))
                        if kind == 'sibling-stage-nested' else str(forbidden)): digest(forbidden)}
                    f.rewrite_result(1, result)
                with IOAudit(self.base) as audit:
                    self.in_process(f, 2)
                self.assertNotIn(forbidden.resolve(), audit.opened,
                                 'observer opened evidence outside its allowed scope')

    def test_state_and_cancel_changes_during_inspection_are_indeterminate(self):
        for kind in ('state', 'cancel-created', 'cancel-removed', 'cancel-content'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind in ('cancel-removed', 'cancel-content'):
                    write_json(f.cancel_path, {'requested_at': 'before'})
                fired = []
                target = (f.root / 'app.py').resolve()
                def mutate_while_reading_source(path):
                    if path == target and not fired:
                        fired.append(True)
                        if kind == 'state':
                            f.state_path.write_bytes(f.state_path.read_bytes() + b'\n')
                        elif kind == 'cancel-removed':
                            f.cancel_path.unlink()
                        else:
                            write_json(f.cancel_path, {'requested_at': 'during'})
                with IOAudit(self.base, mutate_while_reading_source):
                    data = self.in_process(f, 2)
                self.assertEqual(fired, [True], 'fixture source must be inspected as hash evidence')
                self.assertEqual(data['freshness']['state'], 'indeterminate')

    def assert_swap_rejected(self, intermediate):
        f = self.fixture()
        first, _ = f.result(0)
        # Exercise an actual nested-evidence directory, not an unrelated path.
        target = ((first.parent / 'check-0') if intermediate else
                  (f.root / 'app.py')).resolve()
        outside = self.base.resolve() / 'outside-swap-target'
        if intermediate:
            shutil.copytree(target, outside)
        else:
            outside.write_bytes(target.read_bytes())
        parked = target.with_name(target.name + '-parked')
        fired = []
        def swap_before_open(path):
            if not fired and (path == target or
                              (intermediate and path.is_relative_to(target))):
                fired.append(str(path))
                target.rename(parked)
                target.symlink_to(outside, target_is_directory=intermediate)
        # Use the public CLI wrapper and decode after checking disclosure, so an
        # old current response cannot obscure the stronger no-external-open check.
        out, err = io.StringIO(), io.StringIO()
        with IOAudit(self.base, swap_before_open) as audit:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    with mock.patch.object(sys, 'argv', [str(self.code / 'proofloop'),
                                                        *self.arguments(f)]):
                        runpy.run_path(str(self.code / 'proofloop'), run_name='__main__')
                    code = 0
                except SystemExit as exc:
                    code = exc.code
        self.assertEqual(len(fired), 1, 'swap scheduling point was not reached')
        external = [str(path) for path in audit.opened
                    if path == outside or path.is_relative_to(outside)]
        self.assertEqual(external, [], 'observer opened the external swap target')
        self.decode(code, out.getvalue(), err.getvalue(), 2)

    def test_leaf_swap_at_open_is_rejected_without_external_read(self):
        self.assert_swap_rejected(False)

    def test_intermediate_swap_at_open_is_rejected_without_external_read(self):
        self.assert_swap_rejected(True)

    def test_status_never_constructs_execution_or_uses_process_database_or_lock(self):
        f = self.fixture()
        before = snapshot(f.root)
        forbidden = AssertionError('read-only status invoked an execution/write facility')
        with contextlib.ExitStack() as stack:
            for name in ('AgentBridge', 'Coordinator', 'CodexHost', 'ProcessRunner'):
                stack.enter_context(mock.patch.object(self.bridge, name, side_effect=forbidden))
            stack.enter_context(mock.patch.object(self.coordinator, 'Coordinator', side_effect=forbidden))
            for name in ('project_lock', 'atomic_write', 'write_json', 'append_event', 'safe_write_path'):
                stack.enter_context(mock.patch.object(self.core, name, side_effect=forbidden))
            for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
                stack.enter_context(mock.patch.object(subprocess, name, side_effect=forbidden))
            for name in ('kill', 'killpg', 'system'):
                stack.enter_context(mock.patch.object(os, name, side_effect=forbidden))
            stack.enter_context(mock.patch('sqlite3.connect', side_effect=forbidden))
            data = self.in_process(f)
        self.assert_summaries(data, f)
        self.assertEqual(snapshot(f.root), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
