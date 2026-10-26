import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import proofloop
from orchestration import SyntheticLoop


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plan = dict(adapter='synthetic-projection-v1', check='source-view-drift', source='source.json', view='view.json', acceptance_test='acceptance.json', allow_repair=True)
        self.write('source.json', {'fixture_classification': 'synthetic', 'items': [{'id': 'one'}, {'id': 'two'}]})
        self.write('view.json', {'record_count': 0, 'ids': [], 'fixture_classification': 'synthetic'})
        self.write('acceptance.json', {'done': 'source projection equals view'})
        self.write('plan.json', self.plan)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def open(self, run='demo'):
        return SyntheticLoop(self.root, run, 'plan.json')

    def test_real_failure_repair_recheck_and_idempotency(self):
        loop = self.open()
        try:
            self.assertEqual('completed_advisory', loop.advance()['state'])
            failed = json.loads((loop.directory / 'inspect.json').read_text())
            passed = json.loads((loop.directory / 'verify.json').read_text())
            self.assertEqual(1, failed['exit_code'])
            self.assertEqual(0, passed['exit_code'])
            self.assertNotEqual(failed['core_run'], passed['core_run'])
            self.assertEqual(1, json.loads((loop.directory / 'repair.json').read_text())['repair_rounds_used'])
            before = (self.root / failed['log']).read_bytes()
            event_count = loop.coordinator.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            self.assertEqual('completed_advisory', loop.advance()['state'])
            self.assertEqual(event_count, loop.coordinator.db.execute('SELECT COUNT(*) FROM events').fetchone()[0])
            self.assertEqual(before, (self.root / failed['log']).read_bytes())
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, proofloop.main(['validate', '--root', str(self.root), '--run-id', 'demo-verify']))
        finally:
            loop.close()

    def test_checkpoint_resume(self):
        loop = self.open()
        self.assertEqual('checkpoint', loop.advance(1)['state'])
        loop.close()
        loop = self.open()
        self.assertEqual('checkpoint', loop.advance(1)['state'])
        loop.close()
        loop = self.open()
        try:
            self.assertEqual('completed_advisory', loop.advance()['state'])
        finally:
            loop.close()

    def test_noop_does_not_consume_repair(self):
        self.write('view.json', {'record_count': 2, 'ids': ['one', 'two'], 'fixture_classification': 'synthetic'})
        loop = self.open()
        try:
            loop.advance()
            self.assertEqual(0, json.loads((loop.directory / 'repair.json').read_text())['repair_rounds_used'])
        finally:
            loop.close()

    def test_denied_repair(self):
        self.plan['allow_repair'] = False
        self.write('plan.json', self.plan)
        initial = (self.root / 'view.json').read_bytes()
        loop = self.open()
        try:
            self.assertEqual('blocked', loop.advance()['state'])
            self.assertEqual(initial, (self.root / 'view.json').read_bytes())
            self.assertEqual('queued', loop.row('verify')['state'])
        finally:
            loop.close()

    def test_unknown_check_blocks(self):
        self.plan['check'] = 'all-security'
        self.write('plan.json', self.plan)
        loop = self.open()
        try:
            self.assertEqual('blocked', loop.advance()['state'])
            self.assertEqual(0, loop.coordinator.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0])
        finally:
            loop.close()

    def test_changed_inputs_block_before_repair(self):
        for path in ['source.json', 'acceptance.json', 'plan.json', 'view.json']:
            with self.subTest(path=path):
                # Each independent fixture starts from its frozen inputs.
                original = (self.root / path).read_bytes()
                loop = self.open('change-' + path.split('.')[0])
                try:
                    loop.advance(1)
                    (self.root / path).write_bytes(original + b' ')
                    with self.assertRaisesRegex(ValueError, 'changed'):
                        loop.advance()
                    self.assertEqual('queued', loop.row('repair')['state'])
                finally:
                    (self.root / path).write_bytes(original)
                    loop.close()

    def test_uncertain_write_is_not_retried(self):
        loop = self.open()
        loop.advance(1)
        original = loop.event
        def interrupted(name, operation, **kwargs):
            if name == 'repair' and operation == 'result':
                raise RuntimeError('simulated process interruption after write')
            return original(name, operation, **kwargs)
        try:
            with patch.object(loop, 'event', side_effect=interrupted):
                with self.assertRaises(RuntimeError):
                    loop.advance()
            self.assertEqual('uncertain', loop.row('repair')['state'])
            attempts = loop.coordinator.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            with self.assertRaisesRegex(ValueError, 'view changed'):
                loop.advance()
            self.assertEqual(attempts, loop.coordinator.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0])
        finally:
            loop.close()

    def test_changed_output_after_repair_blocks_verification(self):
        loop = self.open()
        try:
            loop.advance(2)
            (self.root / 'view.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'view changed'):
                loop.advance()
        finally:
            loop.close()

    def test_non_synthetic_rejected(self):
        self.write('source.json', {'items': [{'id': 'one'}]})
        loop = self.open()
        try:
            with self.assertRaisesRegex(ValueError, 'synthetic'):
                loop.advance()
        finally:
            loop.close()

    def test_symlink_input_rejected(self):
        (self.root / 'view.json').unlink()
        (self.root / 'view.json').symlink_to(self.root / 'source.json')
        with self.assertRaises(ValueError):
            self.open()

    def test_resolved_aliases_rejected(self):
        self.plan['view'] = './source.json'
        self.write('plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'distinct'):
            self.open()

    def test_completed_replay_rejects_changed_or_missing_nested_log(self):
        loop = self.open()
        try:
            loop.advance()
            for stage in ('inspect', 'verify'):
                record = json.loads((loop.directory / (stage + '.json')).read_text())
                log = self.root / record['log']
                original = log.read_bytes()
                for mode in ('change', 'remove'):
                    with self.subTest(stage=stage, mode=mode):
                        if mode == 'change':
                            log.write_text('{}')
                        else:
                            log.unlink()
                        with self.assertRaises((ValueError, OSError)):
                            loop.advance()
                        log.write_bytes(original)
        finally:
            loop.close()

    def test_completed_replay_rejects_core_control_drift(self):
        loop = self.open()
        try:
            loop.advance()
            record = json.loads((loop.directory / 'verify.json').read_text())
            for relative in record['core_controls']:
                file = self.root / relative
                original = file.read_bytes()
                file.chmod(0o600)
                file.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'core control'):
                    loop.advance()
                file.write_bytes(original)
        finally:
            loop.close()
