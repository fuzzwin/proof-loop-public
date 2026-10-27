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
        self.assertEqual(3, report['state_schema_version'])
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

    def test_syntax_error_in_candidate_returns_to_builder(self):
        (self.root / 'app.py').write_text('def broken(:\n')
        (self.root / 'test_app.py').write_text('import unittest\nfrom app import VALUE\n'
            'class Check(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2, VALUE)\n')
        self.scope['files'].append('test_app.py')
        self.scope['protected'].append('test_app.py')
        self.scope['test_adapter'] = {'type': 'python-unittest', 'tests': ['test_app.py'],
                                      'expected_ids': ['test_app.Check.test_value']}
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        result = self.fix()
        self.assertEqual(['builder', 'verifier'], [role for role, _ in self.host.calls])
        self.assertIn('SyntaxError', result['run']['feedback'][0]['test_adapter']['collection_errors'][0]['error'])
        self.assertEqual('current', self.inspect()['freshness']['state'])

    def test_missing_noneditable_context_blocks_before_dispatch(self):
        self.scope['files'].append('missing-context.md')
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='missing', host=self.host)
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('noneditable', result['blocker']['reason'])
        self.assertEqual([], self.host.calls)
        self.assertFalse((self.root / '.proof').exists())

    def test_bad_intake_request_hash_cannot_be_current(self):
        self.fix()
        path = self.root / '.proof/tasks/public/intake.json'
        intake = json.loads(path.read_text())
        intake['request_sha256'] = '0' * 64
        path.write_text(json.dumps(intake))
        self.assertEqual('unreadable', self.inspect()['freshness']['state'])

    def test_source_and_candidate_permission_changes_make_run_stale(self):
        self.fix()
        for name in ('app.py', 'check.py', '.proof/agent-runs/public/candidate/app.py'):
            with self.subTest(path=name):
                path = self.root / name
                original = path.stat().st_mode & 0o777
                path.chmod(original ^ 0o100)
                report = self.inspect()
                self.assertEqual('stale', report['freshness']['state'], report)
                replay = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='public', host=self.host)
                self.assertEqual('blocked', replay['outcome'])
                self.assertEqual(2, len(self.host.calls))
                path.chmod(original)

    def test_root_and_declared_parent_permissions_match_after_patch(self):
        (self.root / 'lib').mkdir(mode=0o700)
        (self.root / 'lib' / 'context.txt').write_text('protected context\n')
        self.root.chmod(0o700)
        (self.root / 'check.py').write_text("from pathlib import Path\nfrom app import VALUE\n"
            "assert VALUE == 2 and Path('.').stat().st_mode & 0o777 == 0o700 and Path('lib').stat().st_mode & 0o777 == 0o700\n")
        self.scope['files'].append('lib/context.txt')
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        result = self.fix()
        self.assertEqual('current', self.inspect()['freshness']['state'])
        patch = self.root / result['diff']['path']
        subprocess.run(['git', 'apply', str(patch)], cwd=self.root, check=True,
                       umask=int(result['diff']['apply_umask'], 8))
        subprocess.run([sys.executable, '-B', 'check.py'], cwd=self.root, check=True)

    def test_directory_permission_drift_is_stale(self):
        (self.root / 'lib').mkdir(mode=0o700)
        (self.root / 'lib' / 'context.txt').write_text('context\n')
        self.scope['files'].append('lib/context.txt')
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        self.fix()
        for path in (self.root, self.root / 'lib', self.root / '.proof/agent-runs/public/candidate/lib'):
            with self.subTest(path=str(path)):
                original = path.stat().st_mode & 0o777
                path.chmod(original ^ 0o010)
                self.assertEqual('stale', self.inspect()['freshness']['state'])
                replay = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='public', host=self.host)
                self.assertEqual('blocked', replay['outcome'])
                path.chmod(original)
        self.assertEqual(2, len(self.host.calls))

    def test_missing_prospective_parent_blocks_before_dispatch(self):
        self.scope['files'].append('missing-dir/new.py')
        self.scope['editable'].append('missing-dir/new.py')
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='missing-parent', host=self.host)
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('parent directory missing', result['blocker']['reason'])
        self.assertEqual([], self.host.calls)

    def test_quality_command_chmod_and_empty_directory_mutation_are_detected(self):
        for mutation in ("import os\nos.chmod('app.py', 0o755)\n", "from pathlib import Path\nPath('empty-dir').mkdir()\n"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    (root / 'app.py').write_text('VALUE = 1\n')
                    (root / 'check.py').write_text('from app import VALUE\nassert VALUE == 2\n')
                    (root / 'mutate.py').write_text(mutation)
                    scope = {**self.scope, 'files': ['app.py', 'check.py', 'mutate.py'],
                             'protected': ['check.py', 'mutate.py'],
                             'quality': [['{python}', '-B', 'mutate.py'], ['{python}', '-B', 'check.py']]}
                    (root / 'proofloop-project.json').write_text(json.dumps({'schema_version': 1, 'plan': scope}))
                    result = tasks.execute(root, 'Make VALUE equal 2.', run_id='mutate', host=self.host)
                    self.assertEqual('blocked', result['outcome'])
                    self.assertIn('changed the frozen snapshot', result['blocker']['reason'])
                    self.assertEqual([], self.host.calls)

    def test_failed_adapter_test_appears_in_blocker(self):
        (self.root / 'check.py').write_text('assert True\n')
        (self.root / 'test_app.py').write_text('import unittest\nfrom app import VALUE\n'
            'class Check(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2, VALUE)\n')
        self.scope['files'].append('test_app.py')
        self.scope['protected'].append('test_app.py')
        self.scope['test_adapter'] = {'type': 'python-unittest', 'tests': ['test_app.py'],
                                      'expected_ids': ['test_app.Check.test_value']}
        self.scope['max_repairs'] = 0
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='no-repair', host=self.host)
        self.assertEqual('blocked', result['outcome'])
        self.assertIn({'test': 'test_app.Check.test_value', 'status': 'fail'}, result['blocker']['findings'])
        self.assertEqual([], self.host.calls)

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
