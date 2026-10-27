"""Public observer compatibility for real checks and scripted worker results."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import agent_bridge as bridge
import task_workflow as tasks
from test_task_workflow import TaskHost


class RecordedHost(TaskHost):
    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        # Explicit synthetic process metadata; no live native inference.
        result.update(pid=os.getpid(), exit_code=0, elapsed_seconds=0,
                      argv=['synthetic-public-status-host'])
        return result


class PublicTaskStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'app.py').write_text('VALUE = 1\n')
        (self.root / 'check.py').write_text('from app import VALUE\nassert VALUE == 2\n')
        self.scope = {**tasks.DEFAULT_LIMITS, 'files': ['app.py', 'check.py'], 'editable': ['app.py'],
                      'protected': ['check.py'], 'quality': [['{python}', '-B', 'check.py']], 'specialists': []}
        self.profile = self.root / 'proofloop-project.json'
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        self.host = RecordedHost()

    def fix(self):
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='public', host=self.host)
        self.assertEqual('verified_diff', result['outcome'], result)
        return result

    def inspect(self):
        return bridge.read_status(self.root, 'public', '.proof/tasks/public/plan.json')

    def test_completed_task_is_current_without_host_database_or_writes(self):
        result = self.fix()
        before = bridge.tree_map(self.root)
        with mock.patch.object(bridge, 'AgentBridge', side_effect=AssertionError('execution host')):
            report = self.inspect()
        self.assertEqual({'state': 'current', 'issues': []}, report['freshness'])
        self.assertEqual(2, report['state_schema_version'])
        self.assertEqual(result['diff'], report['diff'])
        self.assertEqual('TASK', report['verifier']['requirements'][0]['id'])
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())
        self.assertEqual(before, bridge.tree_map(self.root))

    def test_current_status_uses_final_recheck_and_completed_replay_spends_nothing(self):
        self.fix()
        report = self.inspect()
        state = json.loads((self.root / '.proof/agent-runs/public/state.json').read_text())
        final = json.loads((self.root / state['records'][-2]['path']).read_text())
        self.assertTrue(final['stage'].startswith('recheck-'))
        self.assertEqual(final['result']['commands'], report['quality']['commands'])
        before = bridge.tree_map(self.root)
        self.fix()
        self.assertEqual(before, bridge.tree_map(self.root))
        self.assertEqual(2, len(self.host.calls))

    def test_changed_source_candidate_profile_and_patch_are_historical(self):
        result = self.fix()
        names = ['app.py', '.proof/agent-runs/public/candidate/app.py',
                 'proofloop-project.json', result['diff']['path']]
        for name in names:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'\nchanged\n')
                report = self.inspect()
                self.assertEqual('stale', report['freshness']['state'], report)
                self.assertTrue(report['historical'])
                path.write_bytes(original)

    def test_candidate_symlink_is_rejected_without_following_it(self):
        self.fix()
        candidate = self.root / '.proof/agent-runs/public/candidate'
        candidate.rename(candidate.with_name('candidate-saved'))
        candidate.symlink_to(candidate.with_name('candidate-saved'), target_is_directory=True)
        self.assertEqual('unreadable', self.inspect()['freshness']['state'])

    def test_changed_delivery_metadata_cannot_substitute_a_patch(self):
        self.fix()
        path = self.root / '.proof/agent-runs/public/state.json'
        state = json.loads(path.read_text())
        state['diff']['path'] = 'app.py'
        path.write_text(json.dumps(state))
        report = self.inspect()
        self.assertEqual('unreadable', report['freshness']['state'])
        self.assertNotIn('diff', report)

    def test_missing_intake_map_blocks_replay_without_calls(self):
        self.fix()
        path = self.root / '.proof/tasks/public/intake.json'
        intake = json.loads(path.read_text())
        intake['evidence'] = {}
        path.write_text(json.dumps(intake))
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='public', host=self.host)
        self.assertEqual('blocked', result['outcome'])
        self.assertEqual('state_changed', result['blocker']['code'])
        self.assertEqual(2, len(self.host.calls))
        self.assertEqual('unreadable', self.inspect()['freshness']['state'])

    def test_malformed_profile_is_a_blocker_without_traceback_or_dispatch(self):
        self.scope['editable'] = None
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='bad', host=self.host)
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('editable', result['blocker']['reason'])
        self.assertFalse((self.root / '.proof').exists())
        self.assertEqual([], self.host.calls)

    def test_missing_request_file_returns_json_blocker(self):
        result = subprocess.run([sys.executable, '-B', str(Path(tasks.__file__).with_name('proofloop')),
                                 'fix', '--root', str(self.root), '--request-file', 'missing.txt', '--json'],
                                capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertEqual('blocked', json.loads(result.stdout)['outcome'])
        self.assertNotIn('Traceback', result.stdout + result.stderr)

    def test_runtime_failure_with_active_work_requires_reconciliation(self):
        item = tasks.blocker('Codex process failed; inspect stderr', {'active_stage': {'stage': 'builder-1'}})
        self.assertEqual('uncertain_stage', item['code'])
        self.assertIn('Do not redispatch', item['next_action'])

    def test_python_test_adapter_and_managed_plan_are_inspectable(self):
        (self.root / 'test_app.py').write_text('import unittest\nfrom app import VALUE\n'
            'class Check(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2, VALUE)\n')
        self.scope['files'].append('test_app.py')
        self.scope['protected'].append('test_app.py')
        self.scope['test_adapter'] = {'type': 'python-unittest', 'tests': ['test_app.py'],
                                      'expected_ids': ['test_app.Check.test_value']}
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        self.fix()
        report = self.inspect()
        self.assertEqual('current', report['freshness']['state'], report)
        self.assertTrue(report['quality']['test_adapter']['passed'])

    def test_apply_run_remains_inspectable_after_promotion(self):
        from test_agent_bridge import ScriptedHost
        plan = {**self.scope, 'goal': 'Make VALUE equal 2.'}
        (self.root / 'plan.json').write_text(json.dumps(plan))
        class ApplyHost(ScriptedHost):
            def run(self, *args, **kwargs):
                result = super().run(*args, **kwargs)
                result.update(pid=os.getpid(), exit_code=0, elapsed_seconds=0, argv=['synthetic-apply-host'])
                return result
        loop = bridge.AgentBridge(self.root, 'apply', 'plan.json', host=ApplyHost())
        self.addCleanup(loop.close)
        self.assertEqual('completed_advisory', loop.advance()['state'])
        report = bridge.read_status(self.root, 'apply', 'plan.json')
        self.assertEqual('current', report['freshness']['state'], report)
        self.assertEqual('apply', report['delivery'])
        self.assertEqual('VALUE = 2\n', (self.root / 'app.py').read_text())
