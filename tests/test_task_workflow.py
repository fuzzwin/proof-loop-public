"""One-request workflow with real local checks and scripted native responses."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import agent_bridge as bridge
import task_workflow as tasks
from test_agent_bridge import ScriptedHost


class TaskHost(ScriptedHost):
    def run(self, role, *args, **kwargs):
        result = super().run(role, *args, **kwargs)
        if role == 'verifier':
            response = result['response']
            response['requirements'] = [dict(id=r['id'], status=response['verdict'],
                evidence=['file:app.py', 'check:0'], reason='Synthetic exact-task assessment')
                for r in kwargs['requirements']]
        return result


class TaskWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'app.py').write_text('VALUE = 1\n')
        (self.root / 'check.py').write_text('from app import VALUE\nassert VALUE == 2\n')
        self.scope = {**tasks.DEFAULT_LIMITS, 'files': ['app.py', 'check.py'], 'editable': ['app.py'],
                      'protected': ['check.py'], 'quality': [['{python}', '-B', 'check.py']], 'specialists': []}
        self.profile()
        self.host = TaskHost()

    def profile(self):
        (self.root / 'proofloop-project.json').write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))

    def run_fix(self, request='Make VALUE equal 2.', run_id='fix', **kwargs):
        return tasks.execute(self.root, request, run_id=run_id, host=kwargs.get('host', self.host))

    def test_one_request_returns_applicable_patch_without_source_edits(self):
        result = self.run_fix()
        self.assertEqual('verified_diff', result['outcome'])
        self.assertFalse(result['run']['promoted'])
        self.assertEqual(['builder', 'verifier'], [r for r,p in self.host.calls])
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())
        patch = self.root / result['diff']['path']
        checked = subprocess.run(['git', 'apply', '--check', str(patch)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stderr)
        subprocess.run(['git', 'apply', str(patch)], cwd=self.root, check=True)
        self.assertEqual('VALUE = 2\n', (self.root / 'app.py').read_text())

    def test_completed_replay_keeps_all_hashes_and_calls(self):
        first = self.run_fix()
        before = bridge.tree_map(self.root)
        second = self.run_fix()
        self.assertEqual(first, second)
        self.assertEqual(before, bridge.tree_map(self.root))
        self.assertEqual(2, len(self.host.calls))

    def test_initial_checks_passing_still_loops_for_verifier_findings(self):
        (self.root / 'check.py').write_text('assert True\n')
        self.host = TaskHost(verifier=lambda n: ['VALUE still wrong'] if n == 1 else [])
        result = self.run_fix()
        self.assertEqual('verified_diff', result['outcome'])
        self.assertEqual(['verifier', 'builder', 'verifier'], [r for r,p in self.host.calls])
        self.assertEqual('VALUE = 1\n', (self.root / 'app.py').read_text())

    def test_mutated_diff_state_cannot_substitute_an_unverified_patch(self):
        first = self.run_fix()
        state_file = self.root / '.proof/agent-runs/fix/state.json'
        state = json.loads(state_file.read_text())
        state['diff']['path'] = 'app.py'
        state['diff']['sha256'] = bridge.digest(self.root / 'app.py')
        state_file.write_text(json.dumps(state))
        result = self.run_fix()
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('frozen evidence', result['blocker']['reason'])
        self.assertEqual(2, len(self.host.calls))

    def test_already_satisfied_task_returns_empty_diff_without_builder(self):
        (self.root / 'app.py').write_text('VALUE = 2\n')
        result = self.run_fix()
        self.assertEqual('verified_diff', result['outcome'])
        self.assertEqual([], result['diff']['files'])
        self.assertEqual(b'', (self.root / result['diff']['path']).read_bytes())
        self.assertEqual(['verifier'], [r for r,p in self.host.calls])

    def test_partial_intake_is_not_overwritten_or_dispatched(self):
        partial = self.root / '.proof/tasks/fix'
        partial.mkdir(parents=True)
        (partial / 'request.txt').write_text('preserved partial request')
        result = self.run_fix()
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('overwrite', result['blocker']['reason'])
        self.assertEqual('preserved partial request', (partial / 'request.txt').read_text())
        self.assertEqual([], self.host.calls)

    def test_changed_request_never_redispatches(self):
        self.run_fix()
        result = self.run_fix('Actually delete everything.')
        self.assertEqual('blocked', result['outcome'])
        self.assertEqual('scope_changed', result['blocker']['code'])
        self.assertEqual(2, len(self.host.calls))

    def test_changed_profile_never_redispatches(self):
        self.run_fix()
        self.scope['max_agent_calls'] = 9
        self.profile()
        result = self.run_fix()
        self.assertEqual('scope_changed', result['blocker']['code'])
        self.assertEqual(2, len(self.host.calls))

    def test_missing_profile_returns_actionable_blocker_without_dispatch(self):
        (self.root / 'proofloop-project.json').unlink()
        result = self.run_fix()
        self.assertEqual('missing_project_profile', result['blocker']['code'])
        self.assertIn('profile-init', result['blocker']['next_action'])
        self.assertFalse((self.root / '.proof').exists())
        self.assertEqual([], self.host.calls)

    def test_budget_failure_retains_failed_check_and_does_not_spend_builder_call(self):
        self.scope['max_agent_calls'] = 1
        self.profile()
        result = self.run_fix()
        self.assertEqual('budget_exhausted', result['blocker']['code'])
        self.assertTrue(result['run']['feedback'])
        self.assertFalse(result['run']['feedback'][0]['passed'])
        self.assertEqual([], self.host.calls)

    def test_intake_tampering_never_redispatches(self):
        self.run_fix()
        (self.root / '.proof/tasks/fix/request.txt').write_text('changed')
        result = self.run_fix()
        self.assertEqual('state_changed', result['blocker']['code'])
        self.assertEqual(2, len(self.host.calls))

    def test_source_drift_invalidates_previous_patch(self):
        self.run_fix()
        (self.root / 'app.py').write_text('USER_EDIT = True\n')
        result = self.run_fix()
        self.assertEqual('blocked', result['outcome'])
        self.assertEqual('state_changed', result['blocker']['code'])
        self.assertNotIn('diff', result)
        self.assertEqual(2, len(self.host.calls))

    def test_no_newline_and_space_filename_patch_is_applicable(self):
        self.scope['files'].append('new file.py')
        self.scope['editable'].append('new file.py')
        self.profile()
        self.host = TaskHost(builder=lambda n: [{'path': 'app.py', 'content': 'VALUE = 2'},
                                                 {'path': 'new file.py', 'content': 'A = 1'}])
        result = self.run_fix()
        self.assertEqual('verified_diff', result['outcome'])
        patch = self.root / result['diff']['path']
        checked = subprocess.run(['git', 'apply', '--check', str(patch)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stderr)
        subprocess.run(['git', 'apply', str(patch)], cwd=self.root, check=True)
        self.assertEqual(b'VALUE = 2', (self.root / 'app.py').read_bytes())
        self.assertEqual(b'A = 1', (self.root / 'new file.py').read_bytes())

    def test_binary_source_cannot_be_reported_as_verified_text_diff(self):
        (self.root / 'app.py').write_bytes(b'VALUE = 1\n\x00')
        result = self.run_fix()
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('binary diff', result['blocker']['reason'])
        self.assertEqual(b'VALUE = 1\n\x00', (self.root / 'app.py').read_bytes())

    def test_resource_collision_is_a_prior_run_blocker(self):
        item = tasks.blocker('resource collision')
        self.assertEqual('prior_run_unresolved', item['code'])
        self.assertIn('prior run', item['next_action'])

    def test_cli_without_profile_emits_structured_blocker(self):
        (self.root / 'proofloop-project.json').unlink()
        executable = Path(tasks.__file__).with_name('proofloop')
        result = subprocess.run(['python3', '-B', str(executable), 'fix', '--root', str(self.root),
                                 '--request', 'Fix VALUE.', '--json'], capture_output=True, text=True)
        self.assertEqual(2, result.returncode, result.stderr)
        outcome = json.loads(result.stdout)
        self.assertEqual('blocked', outcome['outcome'])
        self.assertEqual('missing_project_profile', outcome['blocker']['code'])

    def test_explicit_profile_setup_and_no_overwrite(self):
        args = argparse.Namespace(root=str(self.root), profile='configured.json', from_plan=None,
                                  editable=['app.py'], protected=['check.py'], context=[],
                                  check_json=['["{python}", "-B", "check.py"]'])
        with contextlib.redirect_stdout(io.StringIO()):
            tasks.initialize_profile(args)
        profile, digest = tasks.read_profile(self.root, 'configured.json')
        self.assertEqual(self.scope, profile['plan'])
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            with contextlib.redirect_stdout(io.StringIO()):
                tasks.initialize_profile(args)

    def test_profile_from_plan_carries_scope_not_old_task_requirements(self):
        plan = {**self.scope, 'goal': 'Old task', 'requirements': [{'id': 'OLD', 'description': 'Old intent'}]}
        (self.root / 'old-plan.json').write_text(json.dumps(plan))
        args = argparse.Namespace(root=str(self.root), profile='new-profile.json', from_plan='old-plan.json')
        with contextlib.redirect_stdout(io.StringIO()):
            tasks.initialize_profile(args)
        profile, digest = tasks.read_profile(self.root, 'new-profile.json')
        self.assertNotIn('goal', profile['plan'])
        self.assertNotIn('requirements', profile['plan'])
        self.assertEqual(self.scope, profile['plan'])
