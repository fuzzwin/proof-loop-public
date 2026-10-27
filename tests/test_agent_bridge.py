"""Engine tests use explicitly synthetic host identities; no live inference."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import agent_bridge as bridge
import proofloop as core


class ScriptedHost:
    def __init__(self, builder=None, verifier=None, same_identity=False, mutate=False):
        self.calls = []
        self.builder = builder or (lambda n: [{'path': 'app.py', 'content': 'VALUE = 2\n'}])
        self.verifier = verifier or (lambda n: [])
        self.same_identity = same_identity
        self.mutate = mutate

    def run(self, role, prompt, candidate, **kw):
        self.calls.append((role, prompt))
        kw['launched'](os.getpid())
        if self.mutate:
            (kw['workspace'] / 'app.py').write_text('unauthorized snapshot edit')
        if role == 'builder':
            value = {'summary': 'BUILDER-SUMMARY-MUST-NOT-REACH-VERIFIER',
                     'changes': self.builder(sum(r == role for r,p in self.calls))}
        elif role == 'verifier':
            findings = self.verifier(sum(r == role for r,p in self.calls))
            value = {'verdict': 'fail' if findings else 'pass', 'candidate_sha256': candidate, 'findings': findings}
        else:
            value = {'findings': ['Synthetic specialist suggestion']}
        (kw['directory'] / 'synthetic-host.json').write_text(json.dumps(value))
        return {'thread_id': 'synthetic-thread-' + ('same' if self.same_identity else str(len(self.calls))),
                'response': value, 'usage': None, 'classification': 'synthetic_test_only'}


class AgentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'app.py').write_text('VALUE = 1\n')
        (self.root / 'check.py').write_text('from app import VALUE\nassert VALUE == 2, VALUE\n')
        self.plan = {'goal': 'Set VALUE to 2 while preserving tests.', 'files': ['app.py', 'check.py'],
                     'editable': ['app.py'], 'protected': ['check.py'],
                     'quality': [['{python}', '-B', 'check.py']], 'specialists': [],
                     'max_repairs': 2, 'max_agent_calls': 8, 'agent_timeout_seconds': 5,
                     'check_timeout_seconds': 5, 'max_run_seconds': 60}
        self.save_plan()
        self.host = ScriptedHost()

    def save_plan(self):
        (self.root / 'plan.json').write_text(json.dumps(self.plan))

    def open(self, host=None):
        loop = bridge.AgentBridge(self.root, 'trial', 'plan.json', host=host or self.host)
        self.addCleanup(loop.close)
        return loop

    def test_saved_run_identity_mismatch_cannot_replay(self):
        loop = self.open()
        self.assertEqual('completed_advisory', loop.advance()['state'])
        calls = len(self.host.calls)
        state = json.loads(loop.state_path.read_text())
        state['run_id'] = 'TRIAL'
        loop.state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'run identity mismatch'):
            loop.advance()
        self.assertEqual(calls, len(self.host.calls))

    def test_case_insensitive_run_id_alias_cannot_replay(self):
        loop = self.open()
        self.assertEqual('completed_advisory', loop.advance()['state'])
        alias_dir = self.root / '.proof/agent-runs/TRIAL'
        if not alias_dir.exists():
            return  # On case-sensitive filesystems these are distinct paths.
        calls = len(self.host.calls)
        alias = bridge.AgentBridge(self.root, 'TRIAL', 'plan.json', host=self.host)
        self.addCleanup(alias.close)
        with self.assertRaisesRegex(ValueError, 'run identity mismatch'):
            alias.advance()
        self.assertEqual(calls, len(self.host.calls))

    def test_existing_run_directory_without_state_never_redispatches(self):
        loop = self.open()
        self.assertEqual('completed_advisory', loop.advance()['state'])
        calls = len(self.host.calls)
        loop.close()
        shutil.rmtree(loop.directory)
        loop.directory.mkdir(mode=0o700)
        coordinator = self.root / '.proof/coordinator.sqlite'
        if coordinator.exists():
            coordinator.unlink()
        with self.assertRaisesRegex(ValueError, 'saved run state missing'):
            loop.advance()
        self.assertEqual(calls, len(self.host.calls))
        self.assertEqual([], list(loop.directory.iterdir()))
        self.assertFalse(coordinator.exists())

    def test_real_quality_failure_builder_and_fresh_verifier_then_replay(self):
        loop = self.open()
        result = loop.advance()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual('not_accepted', result['acceptance'])
        self.assertEqual(['builder', 'verifier'], [r for r,p in self.host.calls])
        self.assertNotIn('BUILDER-SUMMARY-MUST-NOT-REACH-VERIFIER', self.host.calls[-1][1])
        self.assertEqual(1, result['repair_rounds_used'])
        self.assertEqual('VALUE = 2\n', (self.root / 'app.py').read_text())
        self.assertEqual(result, loop.advance())
        self.assertEqual(2, len(self.host.calls))

    def test_matching_quality_still_needs_independent_verifier(self):
        (self.root / 'app.py').write_text('VALUE = 2\n')
        result = self.open().advance()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual(['verifier'], [r for r,p in self.host.calls])
        self.assertEqual(0, result['repair_rounds_used'])

    def test_verifier_finding_loops_back_to_builder_and_new_verifier(self):
        (self.root / 'app.py').write_text('VALUE = 2\n')
        self.host = ScriptedHost(builder=lambda n: [{'path':'app.py','content':'VALUE = 2\n# documented\n'}],
                                 verifier=lambda n: ['Document the change'] if n == 1 else [])
        result = self.open().advance()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual(['verifier', 'builder', 'verifier'], [r for r,p in self.host.calls])

    def test_specialist_only_runs_for_approved_matching_trigger(self):
        self.plan['specialists'] = [dict(id='python', instructions='Inspect failed Python quality checks.', when=['quality_failure']),
                                    dict(id='other', instructions='Review only verifier findings.', when=['verification_failure'])]
        self.save_plan()
        self.assertEqual('completed_advisory', self.open().advance()['state'])
        self.assertEqual(['specialist', 'builder', 'verifier'], [r for r,p in self.host.calls])

    def test_two_repairs_then_stop(self):
        self.host = ScriptedHost(builder=lambda n: [{'path':'app.py','content':f'VALUE = {n+2}\n'}])
        result = self.open().advance()
        self.assertEqual('blocked', result['state'])
        self.assertEqual(2, result['repair_rounds_used'])
        self.assertEqual(['builder', 'builder'], [r for r,p in self.host.calls])

    def test_zero_repair_budget_never_launches_builder(self):
        self.plan['max_repairs'] = 0
        self.save_plan()
        self.assertEqual('blocked', self.open().advance()['state'])
        self.assertEqual([], self.host.calls)

    def test_insufficient_budget_preserves_calls_and_survives_resume(self):
        self.plan['max_agent_calls'] = 1
        self.save_plan()
        loop = self.open()
        result = loop.advance()
        self.assertEqual('blocked', result['state'])
        self.assertIn('repair and fresh verifier', result['reason'])
        self.assertEqual(0, loop.state['agent_calls'])
        loop.close()
        self.assertEqual(result, self.open().advance())
        self.assertEqual([], self.host.calls)

    def test_completed_replay_rejects_root_and_nested_evidence_drift(self):
        loop = self.open()
        loop.advance()
        path = self.root / 'app.py'
        original = path.read_bytes()
        path.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'outside completed'):
            loop.advance()
        path.write_bytes(original)
        log = next(loop.directory.glob('steps/*/check-0/stdout.log'))
        log.write_text('changed evidence')
        with self.assertRaisesRegex(ValueError, 'nested stage evidence'):
            loop.advance()
        self.assertEqual(2, len(self.host.calls))

    def test_frozen_plan_and_protected_source_changes_block_before_dispatch(self):
        loop = self.open()
        loop.advance(1)
        (self.root / 'check.py').write_text('pass\n')
        with self.assertRaisesRegex(ValueError, 'outside completed'):
            loop.advance()
        self.assertEqual([], self.host.calls)
        self.plan['max_repairs'] = 1
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'plan changed'):
            loop.advance()

    def test_duplicate_worker_identity_cannot_verify_own_build(self):
        self.host = ScriptedHost(same_identity=True)
        loop = self.open()
        with self.assertRaisesRegex(ValueError, 'identity reused'):
            loop.advance()
        self.assertEqual('blocked_uncertain', loop.state['status'])

    def test_snapshot_mutation_and_outside_allowlist_cannot_be_applied(self):
        self.host = ScriptedHost(mutate=True)
        with self.assertRaisesRegex(ValueError, 'changed the frozen snapshot'):
            self.open().advance()
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())

    def test_invalid_second_change_prevents_all_edits(self):
        self.host = ScriptedHost(builder=lambda n: [{'path':'app.py','content':'VALUE = 2\n'},
                                                    {'path':'check.py','content':'pass\n'}])
        with self.assertRaisesRegex(ValueError, 'outside allowlist'):
            self.open().advance()
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())

    def test_quality_command_cannot_modify_its_snapshot(self):
        (self.root / 'check.py').write_text("from pathlib import Path\nPath('app.py').write_text('VALUE = 2')\n")
        with self.assertRaisesRegex(ValueError, 'quality command changed'):
            self.open().advance()
        self.assertEqual([], self.host.calls)
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())

    def test_interruption_after_apply_is_not_retried(self):
        loop = self.open()
        original = loop.transition
        def interrupted(run, operation, **kwargs):
            if operation == 'result' and 'builder' in kwargs.get('artifact', ''):
                raise RuntimeError('synthetic lost acknowledgement after write')
            return original(run, operation, **kwargs)
        with patch.object(loop, 'transition', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'lost acknowledgement'):
                loop.advance()
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())
        self.assertEqual('VALUE = 2\n', (loop.candidate_root / 'app.py').read_text())
        loop.close()
        with self.assertRaisesRegex(ValueError, 'unfinished stage'):
            self.open().advance()
        self.assertEqual(['builder'], [r for r,p in self.host.calls])

    def test_total_budget_survives_resume(self):
        loop = self.open()
        loop.advance(1)
        with patch('agent_bridge.time.time', return_value=loop.state['deadline']+1):
            with self.assertRaisesRegex(ValueError, 'elapsed-time budget'):
                loop.advance()
        self.assertEqual([], self.host.calls)

    def test_cancel_between_checkpoints_never_launches_next_agent(self):
        loop = self.open()
        loop.advance(1)
        bridge.cancel_command(argparse.Namespace(root=str(self.root), run_id='trial'))
        self.assertEqual('cancelled', loop.advance()['state'])
        self.assertEqual([], self.host.calls)

    def test_optional_new_file_can_be_created_only_when_declared_editable(self):
        self.plan['files'].append('new.py')
        self.plan['editable'].append('new.py')
        self.save_plan()
        self.host = ScriptedHost(builder=lambda n: [{'path':'app.py','content':'VALUE = 2\n'},
                                                    {'path':'new.py','content':'DOCUMENTED = True\n'}])
        self.assertEqual('completed_advisory', self.open().advance()['state'])
        self.assertTrue((self.root / 'new.py').exists())

    def test_invalid_contract_is_rejected_without_state_or_dispatch(self):
        self.plan['max_repairs'] = True
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'safe limit'):
            self.open()
        self.assertFalse((self.root / '.proof').exists())

    def test_plan_change_after_parsing_cannot_authorize_old_work(self):
        loop = self.open()
        self.plan.update(max_repairs=0, goal='Inspect only; do not modify files.')
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'plan changed since parsing'):
            loop.advance()
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())
        self.assertEqual([], self.host.calls)
        self.assertFalse(loop.state_path.exists())

    def test_cancellation_after_builder_returns_prevents_apply(self):
        loop = self.open()
        original = self.host.run
        def cancelled_return(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.cancel_path.write_text('{}')
            return result
        with patch.object(self.host, 'run', side_effect=cancelled_return):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                loop.advance()
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())
        self.assertEqual('cancelled', loop.state['status'])

    def test_cancellation_after_verifier_returns_prevents_completion(self):
        (self.root / 'app.py').write_text('VALUE = 2\n')
        loop = self.open()
        original = self.host.run
        def cancelled_return(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.cancel_path.write_text('{}')
            return result
        with patch.object(self.host, 'run', side_effect=cancelled_return):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                loop.advance()
        self.assertEqual('cancelled', loop.state['status'])
        self.assertNotIn('final_verifier_thread', loop.state)

    def test_deadline_after_builder_returns_prevents_apply(self):
        loop = self.open()
        original = self.host.run
        clock = [1000.0]
        def expired_return(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] += self.plan['max_run_seconds'] + 1
            return result
        with patch('agent_bridge.time.time', side_effect=lambda: clock[0]), patch.object(self.host, 'run', side_effect=expired_return):
            with self.assertRaisesRegex(ValueError, 'elapsed-time budget'):
                loop.advance()
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())
