"""Focused observer tests; no execution host or model is needed."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import agent_bridge as bridge


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


@contextlib.contextmanager
def descriptor_opens(before, opened=None):
    """Schedule mutations at real opens and track descriptor-relative paths."""
    original_open, original_close = os.open, os.close
    descriptors = {}
    if opened is None:
        opened = []

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        lexical = Path(os.fsdecode(path))
        if not lexical.is_absolute():
            lexical = (descriptors[dir_fd] if dir_fd is not None else Path.cwd()) / lexical
        before(lexical)
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        actual = lexical.resolve()
        descriptors[fd] = actual
        opened.append(actual)
        return fd

    def closing(fd):
        try:
            return original_close(fd)
        finally:
            descriptors.pop(fd, None)

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(os, 'open', opening))
        stack.enter_context(mock.patch.object(os, 'close', closing))
        if original_open in os.supports_dir_fd:
            stack.enter_context(mock.patch.object(os, 'supports_dir_fd',
                                                  os.supports_dir_fd | {opening}))
        yield opened
    if descriptors:
        raise AssertionError('observer leaked descriptors')


class AgentStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Preserve system-parent aliases such as macOS /var in the supplied root.
        self.root = Path(temporary.name).absolute()
        (self.root / 'app.py').write_text('answer = 42\n')
        (self.root / 'requirements.txt').write_text('Answer is 42.\n')
        self.plan = {'goal': 'Keep answer 42',
                     'files': ['app.py', 'requirements.txt', 'future.py'],
                     'editable': ['app.py', 'future.py'], 'protected': ['requirements.txt'],
                     'quality': [['{python}', '-c', 'assert True']], 'specialists': [],
                     'max_repairs': 2, 'max_agent_calls': 6, 'agent_timeout_seconds': 180,
                     'check_timeout_seconds': 120, 'max_run_seconds': 1800}
        raw_plan = encoded(self.plan)
        (self.root / 'plan.json').write_bytes(raw_plan)
        sources = {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                   for name in ('app.py', 'requirements.txt')}
        sources['future.py'] = None
        self.state = {'schema_version': 1, 'run_id': 'unit', 'status': 'checkpoint',
                      'phase': 'quality', 'active': None, 'round': 0, 'agent_calls': 0,
                      'deadline': 1, 'threads': [], 'records': [],
                      'plan_sha256': hashlib.sha256(raw_plan).hexdigest(),
                      'expected': sources, 'initial': sources.copy()}
        self.directory = self.root / '.proof/agent-runs/unit'
        self.directory.mkdir(parents=True)
        self.save()

    def save(self):
        (self.directory / 'state.json').write_bytes(encoded(self.state))

    def inspect(self):
        return bridge.read_status(self.root, 'unit', 'plan.json')

    def test_current_expired_checkpoint_never_constructs_host(self):
        before = (self.directory / 'state.json').read_bytes()
        with mock.patch.object(bridge, 'AgentBridge', side_effect=AssertionError('execution')):
            report = self.inspect()
        self.assertEqual(report['freshness'], {'state': 'current', 'issues': []})
        self.assertEqual(report['time_remaining_seconds'], 0)
        self.assertEqual(report['acceptance'], 'not_accepted')
        self.assertEqual((self.directory / 'state.json').read_bytes(), before)

    def test_missing_editable_creation_is_stale(self):
        (self.root / 'future.py').write_text('new = True\n')
        self.assertEqual(self.inspect()['freshness']['state'], 'stale')

    def test_noneditable_missing_hash_is_invalid(self):
        self.state['initial']['requirements.txt'] = None
        self.save()
        report = self.inspect()
        self.assertEqual(report['freshness']['state'], 'unreadable')
        self.assertIsNone(report['quality'])

    def test_state_only_quality_does_not_become_a_summary(self):
        self.state['quality'] = {'passed': True}
        self.save()
        self.assertIsNone(self.inspect()['quality'])

    def test_active_cancelled_stage_is_indeterminate(self):
        self.state.update(status='cancelled', active={'stage': 'quality-0', 'coordinator_run': 'agent:unit:0'})
        self.save()
        report = self.inspect()
        self.assertEqual(report['state'], 'cancelled')
        self.assertEqual(report['freshness']['state'], 'indeterminate')
        self.assertTrue(report['historical'])

    def test_raw_path_aliases_are_rejected(self):
        for value in ('./plan.json', 'a//plan.json', 'a/../plan.json', '/plan.json'):
            with self.subTest(value=value):
                report = bridge.read_status(self.root, 'unit', value)
                self.assertEqual(report['freshness']['state'], 'unreadable')

    def test_duplicate_json_keys_and_overflow_are_rejected(self):
        path = self.directory / 'state.json'
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"deadline":1e999}'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                report = self.inspect()
                self.assertEqual(report['freshness']['state'], 'unreadable')
                json.dumps(report, allow_nan=False)

    def test_cancellation_content_change_is_observed_without_retry(self):
        cancel = self.directory / 'cancel.json'
        cancel.write_bytes(b'before')
        target = (self.root / 'app.py').resolve()
        fired = []

        def changing_open(path):
            if path == target and not fired:
                fired.append(True)
                cancel.write_bytes(b'after')

        with descriptor_opens(changing_open):
            report = self.inspect()
        self.assertEqual(fired, [True])
        self.assertEqual(report['freshness']['state'], 'indeterminate')

    def assert_cli_observes_changes(self, root):
        program = Path(__file__).resolve().parents[1] / 'proofloop'
        state_path = root / '.proof/agent-runs/unit/state.json'
        cancel = state_path.with_name('cancel.json')
        target = (root / 'app.py').resolve()
        for kind in ('state', 'cancel-created', 'cancel-removed', 'cancel-content'):
            with self.subTest(kind=kind, root=str(root)):
                self.save()
                cancel.unlink(missing_ok=True)
                if kind in ('cancel-removed', 'cancel-content'):
                    cancel.write_bytes(b'before')
                fired = []
                source_reads = []

                def changing_open(path):
                    if path == target:
                        source_reads.append(path)
                        if not fired:
                            fired.append(True)
                            if kind == 'state':
                                state_path.write_bytes(state_path.read_bytes() + b'\n')
                            elif kind == 'cancel-removed':
                                cancel.unlink()
                            else:
                                cancel.write_bytes(b'after')

                out, err = io.StringIO(), io.StringIO()
                argv = [str(program), 'agent-status', '--root', str(root),
                        '--run-id', 'unit', '--plan', 'plan.json']
                with contextlib.ExitStack() as stack:
                    stack.enter_context(descriptor_opens(changing_open))
                    stack.enter_context(mock.patch.object(sys, 'argv', argv))
                    stack.enter_context(mock.patch.object(sys, 'dont_write_bytecode', True))
                    stack.enter_context(contextlib.redirect_stdout(out))
                    stack.enter_context(contextlib.redirect_stderr(err))
                    with self.assertRaises(SystemExit) as stopped:
                        runpy.run_path(str(program), run_name='__main__')
                self.assertEqual(fired, [True], 'supplied source path must be read')
                self.assertEqual(source_reads, [target], 'inspection must not retry')
                self.assertEqual(stopped.exception.code, 2)
                self.assertEqual(err.getvalue(), '')
                report = json.loads(out.getvalue())
                self.assertEqual(report['freshness']['state'], 'indeterminate')
                self.assertTrue(report['historical'])
                changed = 'state.json' if kind == 'state' else 'cancel.json'
                self.assertIn('changed during inspection: .proof/agent-runs/unit/' + changed,
                              report['freshness']['issues'])

    def test_cli_detects_changes_with_supplied_temporary_root(self):
        self.assert_cli_observes_changes(self.root)

    def test_cli_detects_changes_through_unresolved_parent_alias(self):
        # Exercise the macOS system-parent alias shape on other POSIX hosts too.
        # The selected project root is a real directory; only its parent aliases.
        with tempfile.TemporaryDirectory() as temporary:
            parent_alias = Path(temporary).absolute() / 'parent-alias'
            parent_alias.symlink_to(self.root.parent, target_is_directory=True)
            root = parent_alias / self.root.name
            self.assertFalse(root.is_symlink())
            self.assertNotEqual(root, root.resolve(strict=True))
            self.assertEqual(root.resolve(strict=True), self.root.resolve(strict=True))
            self.assert_cli_observes_changes(root)

    def assert_swap_rejected(self, intermediate):
        if intermediate:
            (self.root / 'src').mkdir()
            (self.root / 'app.py').rename(self.root / 'src/app.py')
            for key in ('files', 'editable'):
                self.plan[key] = ['src/app.py' if name == 'app.py' else name
                                  for name in self.plan[key]]
            for key in ('expected', 'initial'):
                self.state[key]['src/app.py'] = self.state[key].pop('app.py')
            raw_plan = encoded(self.plan)
            (self.root / 'plan.json').write_bytes(raw_plan)
            self.state['plan_sha256'] = hashlib.sha256(raw_plan).hexdigest()
            self.save()
        target = (self.root / ('src' if intermediate else 'app.py')).resolve()
        parked = target.with_name(target.name + '-parked')
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary).resolve() / 'outside'
            if intermediate:
                shutil.copytree(target, outside)
            else:
                outside.write_bytes(target.read_bytes())
            fired = []

            def swap(path):
                if not fired and (path == target or
                                  (intermediate and path.is_relative_to(target))):
                    fired.append(path)
                    target.rename(parked)
                    target.symlink_to(outside, target_is_directory=intermediate)

            with descriptor_opens(swap) as opened:
                report = self.inspect()
            self.assertEqual(len(fired), 1, 'swap scheduling point was not reached')
            self.assertEqual([path for path in opened
                              if path == outside or path.is_relative_to(outside)], [])
            self.assertEqual(report['freshness']['state'], 'unreadable')
            self.assertTrue(report['historical'])
            self.assertTrue(report['freshness']['issues'])

    def test_leaf_swap_at_open_never_opens_external_target(self):
        self.assert_swap_rejected(False)

    def test_intermediate_swap_at_open_never_opens_external_target(self):
        self.assert_swap_rejected(True)

    def test_missing_descriptor_support_fails_closed(self):
        with mock.patch.object(os, 'supports_dir_fd', set()):
            with mock.patch.object(os, 'open', side_effect=AssertionError('unsupported open')):
                report = self.inspect()
        self.assertEqual(report['freshness']['state'], 'unreadable')
        self.assertIn('descriptor-relative nofollow', report['freshness']['issues'][0])
        json.dumps(report, allow_nan=False)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires POSIX FIFO')
    def test_fifo_is_rejected_without_blocking_or_reading(self):
        source = self.root / 'app.py'
        source.unlink()
        os.mkfifo(source)
        target = source.resolve()
        original_read = os.read
        original_fstat = os.fstat
        fifo_identity = source.stat()

        def reading(fd, size):
            info = original_fstat(fd)
            self.assertNotEqual((info.st_dev, info.st_ino),
                                (fifo_identity.st_dev, fifo_identity.st_ino),
                                'nonregular descriptor must be rejected before reading')
            return original_read(fd, size)

        with descriptor_opens(lambda path: None) as opened:
            with mock.patch.object(os, 'read', reading):
                report = self.inspect()
        self.assertIn(target, opened)
        self.assertEqual(report['freshness']['state'], 'unreadable')


if __name__ == '__main__':
    unittest.main()
