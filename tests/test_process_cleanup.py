"""Permission-denial regression coverage using local processes, never agents."""
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agent_bridge import BridgeStop, ProcessRunner


@unittest.skipUnless(os.name == 'posix', 'POSIX process groups required')
class ProcessCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pids = []
        self.real_killpg = os.killpg

    def denied_signal(self, pid, sig):
        if sig == signal.SIGKILL:
            raise PermissionError(errno.EPERM, 'injected group denial')
        return self.real_killpg(pid, sig)

    def run_process(self, code, cancelled=lambda: False):
        return ProcessRunner().run([sys.executable, '-c', code], cwd=self.root,
            directory=self.root, timeout=3, cancelled=cancelled, launched=self.pids.append)

    def report(self):
        return json.loads((self.root / 'cleanup.json').read_text())

    def assert_reaped(self):
        with self.assertRaises(ChildProcessError):
            os.waitpid(self.pids[0], os.WNOHANG)

    def test_denied_signal_after_exit_requires_positive_group_absence(self):
        with mock.patch('agent_bridge.os.killpg', side_effect=self.denied_signal):
            result = self.run_process('print("done")')
        self.assertEqual(result['exit_code'], 0)
        self.assert_reaped()
        report = self.report()
        self.assertEqual(report['group'], 'absent')
        self.assertEqual(report['group_probe'], 'absent')
        self.assertIn('Errno 1', report['group_error'])
        self.assertEqual(report['status'], 'complete')

    def test_output_limit_reaps_live_leader_and_preserves_original_failure(self):
        with mock.patch('agent_bridge.os.killpg', side_effect=self.denied_signal), \
             mock.patch('agent_bridge.MAX_LOG_BYTES', 64):
            with self.assertRaisesRegex(BridgeStop, '^process output limit$'):
                self.run_process('import time; print("x"*1024, flush=True); time.sleep(60)')
        self.assert_reaped()
        report = self.report()
        self.assertTrue(report['leader_reaped'])
        self.assertEqual(report['group'], 'absent')
        self.assertEqual(report['original_error'], 'process output limit')

    def test_live_descendant_keeps_denied_cleanup_uncertain(self):
        heartbeat = self.root / 'heartbeat'
        child_code = ('import pathlib,time; p=pathlib.Path("heartbeat")\n'
                      'while True:\n p.write_text(str(time.monotonic_ns())); time.sleep(.01)')
        code = ('import subprocess,sys,time\n'
                f'subprocess.Popen([sys.executable,"-c",{child_code!r}])\n'
                'time.sleep(60)')
        try:
            with mock.patch('agent_bridge.os.killpg', side_effect=self.denied_signal):
                with self.assertRaisesRegex(BridgeStop, 'cleanup uncertain.*original failure: cancelled'):
                    self.run_process(code, cancelled=heartbeat.exists)
            self.assert_reaped()
            report = self.report()
            self.assertEqual(report['group_probe'], 'exists')
            self.assertEqual(report['status'], 'uncertain')
            before = heartbeat.read_text()
            time.sleep(.08)
            self.assertNotEqual(before, heartbeat.read_text())
        finally:
            for pid in self.pids:
                try:
                    self.real_killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_denied_probe_cannot_authorize_success_even_after_leader_exit(self):
        with mock.patch('agent_bridge.os.killpg', side_effect=PermissionError(errno.EPERM, 'denied')):
            with self.assertRaisesRegex(BridgeStop, 'cleanup uncertain'):
                self.run_process('pass')
        self.assert_reaped()
        self.assertEqual(self.report()['status'], 'uncertain')
        self.assertIn('Errno 1', self.report()['group_probe'])

    def test_failed_fallback_and_reap_are_bounded_and_recorded(self):
        proc = mock.Mock(pid=123456, stdin=None)
        proc.poll.return_value = None
        proc.kill.side_effect = PermissionError(errno.EPERM, 'leader denied')
        proc.wait.side_effect = subprocess.TimeoutExpired('synthetic', 5)
        with mock.patch('agent_bridge.os.killpg', side_effect=PermissionError(errno.EPERM, 'group denied')):
            with self.assertRaisesRegex(BridgeStop, 'cleanup uncertain.*original failure: cancelled'):
                ProcessRunner.cleanup(proc, self.root, BridgeStop('cancelled'))
        timeout = proc.wait.call_args.kwargs['timeout']
        self.assertGreaterEqual(timeout, 0)
        self.assertLessEqual(timeout, 5)
        report = self.report()
        self.assertFalse(report['leader_reaped'])
        self.assertIn('leader denied', report['leader_kill_error'])
        self.assertIn('timed out', report['reap_error'])
        self.assertEqual(report['status'], 'uncertain')


if __name__ == '__main__':
    unittest.main()
