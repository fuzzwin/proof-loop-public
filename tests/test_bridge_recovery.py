"""Recovery/acceptance regressions. All agent responses are scripted, never live models."""
import argparse
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_bridge as bridge
import proofloop as core
from test_agent_bridge import AgentBridgeTests, ScriptedHost


class RecoveryTests(unittest.TestCase):
    setUp = AgentBridgeTests.setUp
    save_plan = AgentBridgeTests.save_plan
    open = AgentBridgeTests.open
    def two_files(self):
        (self.root / 'other.py').write_text('OTHER = 1\n')
        self.plan['files'].append('other.py')
        self.plan['editable'].append('other.py')
        self.save_plan()
        self.host = ScriptedHost(builder=lambda n: [{'path': 'app.py', 'content': 'VALUE = 2\n'},
                                                    {'path': 'other.py', 'content': 'OTHER = 2\n'}])

    def interrupt_promotion(self):
        loop = self.open()
        atomic = core.atomic_write
        def fail_second(path, *args, **kwargs):
            if path == self.root / 'other.py':
                raise OSError('synthetic promotion interruption')
            return atomic(path, *args, **kwargs)
        with patch('agent_bridge.core.atomic_write', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'promotion interruption'):
                loop.advance()
        return loop

    def test_intermediate_repair_stays_private_until_verified(self):
        loop = self.open()
        loop.advance(3)
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())
        self.assertEqual('VALUE = 2\n', (loop.candidate_root / 'app.py').read_text())
        self.assertEqual(['builder'], [r for r,p in self.host.calls])
        self.assertEqual('completed_advisory', loop.advance()['state'])
        self.assertEqual('VALUE = 2\n', (self.root / 'app.py').read_text())

    def test_failed_verification_never_promotes(self):
        self.host = ScriptedHost(builder=lambda n: [{'path': 'app.py', 'content': f'VALUE = 2\n# {n}\n'}],
                                 verifier=lambda n: ['Unmet requirement'])
        result = self.open().advance()
        self.assertEqual('blocked', result['state'])
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())

    def test_partial_promotion_recovers_without_model_calls_and_replays(self):
        self.two_files()
        loop = self.interrupt_promotion()
        self.assertEqual('VALUE = 2\n', (self.root / 'app.py').read_text())
        self.assertEqual('OTHER = 1\n', (self.root / 'other.py').read_text())
        self.assertEqual('blocked_uncertain', loop.state['status'])
        calls = len(self.host.calls)
        loop.close()
        recovered = self.open()
        result = recovered.recover_promotion()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual('OTHER = 2\n', (self.root / 'other.py').read_text())
        self.assertEqual(result, recovered.advance())
        self.assertEqual(result, recovered.recover_promotion())
        self.assertEqual(calls, len(self.host.calls))

    def test_explicit_recovery_can_finish_after_original_deadline(self):
        self.two_files()
        loop = self.interrupt_promotion()
        calls = len(self.host.calls)
        loop.state['deadline'] = time.time() - 1
        loop.save()
        result = loop.recover_promotion()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual('OTHER = 2\n', (self.root / 'other.py').read_text())
        self.assertEqual(calls, len(self.host.calls))

    def test_recovery_refuses_directory_mode_drift_before_writing(self):
        self.two_files()
        loop = self.interrupt_promotion()
        original_mode = self.root.stat().st_mode & 0o777
        self.root.chmod(original_mode ^ 0o010)
        with self.assertRaisesRegex(ValueError, 'directory permissions changed before promotion'):
            loop.recover_promotion()
        self.assertEqual('OTHER = 1\n', (self.root / 'other.py').read_text())
        self.assertEqual('blocked_uncertain', loop.state['status'])
        self.assertIsNotNone(loop.state['active'])
        self.root.chmod(original_mode)
        self.assertEqual('completed_advisory', loop.recover_promotion()['state'])

    def test_recovery_does_not_persist_completion_if_final_freshness_fails(self):
        self.two_files()
        loop = self.interrupt_promotion()
        original_fresh = loop.fresh
        def fail_final(source=True):
            if source and (self.root / 'other.py').read_text() == 'OTHER = 2\n':
                raise ValueError('synthetic final freshness failure')
            return original_fresh(source=source)
        with patch.object(loop, 'fresh', side_effect=fail_final):
            with self.assertRaisesRegex(ValueError, 'final freshness failure'):
                loop.recover_promotion()
        persisted = json.loads(loop.state_path.read_text())
        self.assertEqual('blocked_uncertain', persisted['status'])
        self.assertIsNotNone(persisted['active'])
        self.assertEqual('completed_advisory', loop.recover_promotion()['state'])

    def test_recovery_does_not_persist_completion_if_coordinator_validation_fails(self):
        self.two_files()
        loop = self.interrupt_promotion()
        original_validate = bridge.validate_existing_coordinator
        calls = []
        def fail_completed(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise ValueError('coordinator evidence missing or invalid')
            return original_validate(*args, **kwargs)
        with patch('agent_bridge.validate_existing_coordinator', side_effect=fail_completed):
            with self.assertRaisesRegex(ValueError, 'coordinator evidence missing or invalid'):
                loop.recover_promotion()
        persisted = json.loads(loop.state_path.read_text())
        self.assertEqual('blocked_uncertain', persisted['status'])
        self.assertIsNotNone(persisted['active'])

    def test_recovery_retains_uncertain_state_if_evidence_changes_at_final_status(self):
        self.two_files()
        loop = self.interrupt_promotion()
        status = loop.status
        changed = []
        def mutate_before_final_status():
            if loop.state['status'] == 'completed_advisory' and not changed:
                with contextlib.closing(sqlite3.connect(self.root / '.proof/coordinator.sqlite')) as db:
                    db.execute("DELETE FROM operations WHERE key NOT LIKE '%:submit'")
                    db.commit()
                changed.append(True)
            return status()
        with patch.object(loop, 'status', side_effect=mutate_before_final_status):
            with self.assertRaisesRegex(ValueError, 'coordinator evidence changed since inspection'):
                loop.recover_promotion()
        self.assertEqual([True], changed)
        persisted = json.loads(loop.state_path.read_text())
        self.assertEqual('blocked_uncertain', persisted['status'])
        self.assertIsNotNone(persisted['active'])

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS extended ACL regression')
    def test_recovery_retains_uncertain_state_if_acl_changes_at_final_status(self):
        self.two_files()
        loop = self.interrupt_promotion()
        status = loop.status
        def add_acl_before_final_status():
            subprocess.run(['/bin/chmod', '+a', 'everyone allow read,execute', str(loop.directory)], check=True)
            return status()
        try:
            with patch.object(loop, 'status', side_effect=add_acl_before_final_status):
                with self.assertRaisesRegex(ValueError, 'ACL'):
                    loop.recover_promotion()
        finally:
            subprocess.run(['/bin/chmod', '-N', str(loop.directory)], check=True)
        persisted = json.loads(loop.state_path.read_text())
        self.assertEqual('blocked_uncertain', persisted['status'])
        self.assertIsNotNone(persisted['active'])

    def test_recovery_refuses_external_edit_and_retains_it(self):
        self.two_files()
        loop = self.interrupt_promotion()
        (self.root / 'other.py').write_text('USER_EDIT = True\n')
        with self.assertRaisesRegex(ValueError, 'preserve external edit'):
            loop.recover_promotion()
        self.assertEqual('USER_EDIT = True\n', (self.root / 'other.py').read_text())

    def test_recovery_refuses_tampered_journal(self):
        self.two_files()
        loop = self.interrupt_promotion()
        (self.root / loop.state['promotion']['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'journal changed'):
            loop.recover_promotion()

    def test_recovery_rejects_changed_backup_of_already_promoted_file(self):
        self.two_files()
        loop = self.interrupt_promotion()
        journal = self.root / loop.state['promotion']['path']
        (journal.parent / 'before' / 'app.py').write_text('changed backup')
        with self.assertRaisesRegex(ValueError, 'backup changed'):
            loop.recover_promotion()
        self.assertEqual('OTHER = 1\n', (self.root / 'other.py').read_text())

    def test_cancelled_promotion_cannot_be_recovered(self):
        self.two_files()
        loop = self.interrupt_promotion()
        loop.cancel_path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            loop.recover_promotion()
        self.assertEqual('OTHER = 1\n', (self.root / 'other.py').read_text())

    def test_recovery_after_coordinator_result_before_state_save(self):
        loop = self.open()
        transition = loop.transition
        def interrupt(run, operation, **kwargs):
            result = transition(run, operation, **kwargs)
            if operation == 'result' and 'promote' in kwargs.get('artifact', ''):
                raise RuntimeError('lost promotion acknowledgement')
            return result
        with patch.object(loop, 'transition', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'lost promotion'):
                loop.advance()
        calls = len(self.host.calls)
        self.assertEqual('completed_advisory', loop.recover_promotion()['state'])
        self.assertEqual(calls, len(self.host.calls))

    def test_source_drift_during_verifier_prevents_promotion(self):
        run = self.host.run
        def drift(role, *args, **kwargs):
            result = run(role, *args, **kwargs)
            if role == 'verifier':
                (self.root / 'app.py').write_text('USER_EDIT = True\n')
            return result
        with patch.object(self.host, 'run', side_effect=drift):
            with self.assertRaisesRegex(ValueError, 'outside completed'):
                self.open().advance()
        self.assertEqual('USER_EDIT = True\n', (self.root / 'app.py').read_text())

    def test_requirement_evidence_must_cover_exact_ids(self):
        requirements = [{'id': 'R1', 'description': 'Must work'}]
        response = {'verdict': 'pass', 'candidate_sha256': 'candidate', 'findings': [], 'requirements': []}
        with self.assertRaisesRegex(ValueError, 'every requirement'):
            bridge.validate_response('verifier', response, 'candidate', requirements)
        response['requirements'] = [{'id': 'R1', 'status': 'not_checked', 'evidence': [], 'reason': 'No runtime'}]
        with self.assertRaisesRegex(ValueError, 'unchecked'):
            bridge.validate_response('verifier', response, 'candidate', requirements)
        response['requirements'][0].update(status='pass', reason='Observed output')
        with self.assertRaisesRegex(ValueError, 'needs evidence'):
            bridge.validate_response('verifier', response, 'candidate', requirements)

    def test_preflight_and_status_are_read_only(self):
        original = bridge.tree_map(self.root)
        with patch('agent_bridge.shutil.which', return_value='/synthetic/codex'):
            result = bridge.preflight(self.root, 'plan.json')
        self.assertTrue(result['ready'])
        self.assertEqual(original, bridge.tree_map(self.root))
        self.open().advance(1)
        before = bridge.tree_map(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.status_command(argparse.Namespace(root=str(self.root), run_id='trial', plan='plan.json'))
        self.assertEqual(before, bridge.tree_map(self.root))

    def test_usage_is_summed_once_and_unknown_is_not_zero(self):
        run = self.host.run
        def measured(*args, **kwargs):
            result = run(*args, **kwargs)
            result['usage'] = {'input_tokens': 20, 'cached_input_tokens': 5, 'output_tokens': 3}
            result['requested_model'] = 'host default'
            result['observed_model'] = None
            return result
        with patch.object(self.host, 'run', side_effect=measured):
            loop = self.open()
            result = loop.advance()
        self.assertEqual({'input_tokens': 40, 'cached_input_tokens': 10, 'output_tokens': 6}, result['usage']['totals'])
        self.assertEqual('complete', result['usage']['coverage'])
        self.assertIsNone(result['usage']['aud_cost'])
        self.assertEqual(result, loop.advance())

    def test_repeated_candidate_stops_without_another_verifier(self):
        self.host = ScriptedHost(verifier=lambda n: ['Still missing behavior'])
        with self.assertRaisesRegex(ValueError, 'no progress'):
            self.open().advance()
        self.assertEqual(['builder', 'verifier', 'builder'], [r for r,p in self.host.calls])
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())

del AgentBridgeTests
