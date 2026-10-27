"""Frozen regressions for executable quality checks in real project snapshots."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_bridge import AgentBridge


@unittest.skipUnless(os.name == 'posix', 'POSIX bridge only')
class SnapshotPermissionsTests(unittest.TestCase):
    def test_executable_checks_keep_permissions_in_candidate_and_worker_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'app.py').write_text('VALUE = 1\n')
            script = root / 'check.sh'
            script.write_text('#!/bin/sh\nexit 0\n')
            script.chmod(0o755)
            plan = {'goal': 'Preserve executable quality checks.', 'files': ['app.py', 'check.sh'],
                    'editable': ['app.py'], 'protected': ['check.sh'], 'quality': [['./check.sh']],
                    'specialists': [], 'max_repairs': 0, 'max_agent_calls': 1,
                    'agent_timeout_seconds': 5, 'check_timeout_seconds': 5, 'max_run_seconds': 30}
            (root / 'plan.json').write_text(json.dumps(plan))
            loop = AgentBridge(root, 'permissions', 'plan.json')
            try:
                summary = loop.advance(1)  # Real quality process only; never launches a model.
                self.assertEqual('checkpoint', summary['state'])
                quality_record = next(loop.directory.glob('steps/*quality*/result.json'))
                quality = json.loads(quality_record.read_text())['result']
                self.assertTrue(quality['passed'])
                self.assertEqual(0o755, (loop.root / 'check.sh').stat().st_mode & 0o777)
                snapshot = next(loop.directory.glob('steps/*/workspace/check.sh'))
                self.assertEqual(0o755, snapshot.stat().st_mode & 0o777)
                self.assertEqual(0o755, script.stat().st_mode & 0o777)
            finally:
                loop.close()

    def test_editing_an_executable_preserves_mode_through_repair_and_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            script = root / 'app.sh'
            script.write_text('#!/bin/sh\necho 0\n')
            script.chmod(0o755)
            (root / 'check.py').write_text("import subprocess\nassert subprocess.check_output(['./app.sh']).strip() == b'1'\n")
            plan = {'goal': 'Make app.sh print 1 while keeping it executable.', 'files': ['app.sh', 'check.py'],
                    'editable': ['app.sh'], 'protected': ['check.py'], 'quality': [['{python}', '-B', 'check.py']],
                    'specialists': [], 'max_repairs': 1, 'max_agent_calls': 2,
                    'agent_timeout_seconds': 5, 'check_timeout_seconds': 5, 'max_run_seconds': 30}
            (root / 'plan.json').write_text(json.dumps(plan))
            class Host:
                calls = 0
                def run(self, role, prompt, candidate, **kwargs):
                    self.calls += 1
                    kwargs['launched'](os.getpid())
                    response = ({'summary': 'Synthetic mode regression', 'changes': [
                        {'path': 'app.sh', 'content': '#!/bin/sh\necho 1\n'}]} if role == 'builder' else
                        {'verdict': 'pass', 'candidate_sha256': candidate, 'findings': []})
                    (kwargs['directory'] / 'synthetic.json').write_text(json.dumps(response))
                    return {'thread_id': 'synthetic-mode-' + str(self.calls), 'response': response, 'usage': None}
            loop = AgentBridge(root, 'executable-edit', 'plan.json', host=Host())
            try:
                result = loop.advance()
                self.assertEqual('completed_advisory', result['state'])
                self.assertEqual(0o755, script.stat().st_mode & 0o777)
                self.assertEqual(0o755, (loop.root / 'app.sh').stat().st_mode & 0o777)
                for snapshot in loop.directory.glob('steps/*/workspace/app.sh'):
                    self.assertEqual(0o755, snapshot.stat().st_mode & 0o777)
                self.assertEqual('#!/bin/sh\necho 1\n', script.read_text())
            finally:
                loop.close()


if __name__ == '__main__':
    unittest.main()
