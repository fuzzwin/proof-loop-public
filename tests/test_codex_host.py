"""Synthetic native-protocol tests: real local subprocesses, no model or network."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

from agent_bridge import BridgeStop, CodexHost, ProcessRunner, schema_for


FAKE_PROGRAM = r'''
import json, os, pathlib, subprocess, sys, time
base = pathlib.Path(__file__).parent
case = json.loads((base / 'synthetic-case.json').read_text())
args = sys.argv[1:]
final = pathlib.Path(args[args.index('--output-last-message') + 1])
schema = pathlib.Path(args[args.index('--output-schema') + 1])
prompt = sys.stdin.read()
(base / 'synthetic-invocation.json').write_text(json.dumps({
    'argv': args, 'cwd': os.getcwd(), 'prompt': prompt,
    'schema': json.loads(schema.read_text()),
    'api_key_present': 'OPENAI_API_KEY' in os.environ or 'CODEX_API_KEY' in os.environ,
    'pid': os.getpid(),
}))
print('synthetic diagnostic on stderr', file=sys.stderr, flush=True)
if case.get('hang'):
    heartbeat = base / 'synthetic-child-heartbeat'
    child_code = ('import pathlib,time,sys\np=pathlib.Path(sys.argv[1])\n'
                  'while True:\n p.write_text(str(time.monotonic_ns()))\n time.sleep(.02)\n')
    child = subprocess.Popen([sys.executable, '-c', child_code, str(heartbeat)])
    while not heartbeat.exists():
        time.sleep(.005)
    (base / 'synthetic-child.pid').write_text(str(child.pid))
    print('synthetic parent waiting', flush=True)
    time.sleep(60)
if 'final' in case:
    if case.get('symlink'):
        target = base / 'synthetic-final-target.json'
        target.write_text(json.dumps(case['final']))
        final.symlink_to(target)
    else:
        final.write_text(case.get('final_raw', json.dumps(case['final'])))
for event in case.get('events', []):
    print(json.dumps(event), flush=True)
if 'raw' in case:
    print(case['raw'], flush=True)
sys.exit(case.get('exit_code', 0))
'''


class CodexHostProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-native-protocol-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / 'synthetic workspace ; literal'
        self.workspace.mkdir()
        self.executable = self.root / 'synthetic codex ; literal'
        self.executable.write_text('#!' + sys.executable + '\n' + FAKE_PROGRAM)
        self.executable.chmod(0o700)
        self.host = CodexHost(str(self.executable))
        self.response = {'summary': 'synthetic proposal only', 'changes': []}
        self.thread_id = 'synthetic-native-thread-001'
        self.usage = {'input_tokens': 12, 'cached_input_tokens': 3, 'output_tokens': 4}
        self.prompt = 'Synthetic prompt with literal $(touch SHOULD_NOT_EXIST); no model call.'
        self.counter = 0
        self.pids = []

    def events(self, response=None):
        return [
            {'type': 'thread.started', 'thread_id': self.thread_id},
            {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'id': 'synthetic-message',
             'type': 'agent_message', 'text': json.dumps(self.response if response is None else response)}},
            {'type': 'turn.completed', 'usage': self.usage},
        ]

    def run_case(self, case=None, *, role='builder', timeout=3, cancelled=lambda: False):
        self.counter += 1
        self.directory = self.root / ('synthetic-call-' + str(self.counter))
        self.directory.mkdir()
        if case is None:
            case = {'events': self.events(), 'final': self.response}
        (self.root / 'synthetic-case.json').write_text(json.dumps(case))
        return self.host.run(role, self.prompt, 'synthetic-candidate-hash',
                             workspace=self.workspace, directory=self.directory,
                             timeout=timeout, cancelled=cancelled, launched=self.pids.append)

    def test_clean_completion_and_evidence_use_real_subprocess(self):
        result = self.run_case()
        self.assertEqual(result['response'], self.response)
        self.assertEqual(result['thread_id'], self.thread_id)
        self.assertEqual(result['usage'], self.usage)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(self.pids, [result['pid']])
        self.assertGreater(result['elapsed_seconds'], 0)
        self.assertIsNone(result['observed_model'])
        self.assertEqual(result['classification'], 'native_agent_report_not_portable_acceptance')
        self.assertEqual((self.directory / 'prompt.txt').read_text(), self.prompt)
        self.assertEqual(json.loads((self.directory / 'schema.json').read_text()), schema_for('builder'))
        self.assertIn('synthetic diagnostic', (self.directory / 'stderr.log').read_text())
        self.assertEqual(len((self.directory / 'stdout.log').read_text().splitlines()), 4)

    def test_cli_readonly_flags_literal_arguments_and_no_inherited_keys(self):
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-never-real',
                                         'CODEX_API_KEY': 'synthetic-never-real'}):
            result = self.run_case()
        invocation = json.loads((self.root / 'synthetic-invocation.json').read_text())
        args = invocation['argv']
        self.assertEqual(args[0], 'exec')
        self.assertIn('--ignore-user-config', args)
        self.assertIn('--ephemeral', args)
        self.assertIn('--skip-git-repo-check', args)
        self.assertIn('--json', args)
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertEqual(args[args.index('-c') + 1], 'approval_policy="never"')
        self.assertEqual(args[args.index('--color') + 1], 'never')
        self.assertEqual(args[args.index('-C') + 1], str(self.workspace))
        self.assertEqual(args[-1], '-')
        self.assertEqual(invocation['cwd'], str(self.workspace))
        self.assertEqual(invocation['prompt'], self.prompt)
        self.assertEqual(invocation['pid'], result['pid'])
        self.assertFalse(invocation['api_key_present'])
        self.assertFalse((self.workspace / 'SHOULD_NOT_EXIST').exists())
        self.assertEqual(result['argv'], [str(self.executable)] + args)

    def test_exact_single_nonempty_identity_required(self):
        for identities in ([], [None], [''], [123], [self.thread_id, self.thread_id],
                           [self.thread_id, 'synthetic-native-thread-002']):
            with self.subTest(identities=identities):
                events = [{'type': 'thread.started', 'thread_id': item} for item in identities]
                events += self.events()[1:]
                with self.assertRaisesRegex(ValueError, 'identity'):
                    self.run_case({'events': events, 'final': self.response})

    def test_exact_single_completion_required(self):
        for completions in ([], [self.events()[-1], self.events()[-1]]):
            with self.subTest(completions=len(completions)):
                with self.assertRaisesRegex(ValueError, 'not completed cleanly'):
                    self.run_case({'events': self.events()[:-1] + completions, 'final': self.response})

    def test_error_or_failed_event_rejected_even_with_completion(self):
        for event_type in ('error', 'turn.failed'):
            with self.subTest(event_type=event_type):
                with self.assertRaisesRegex(ValueError, 'not completed cleanly'):
                    self.run_case({'events': self.events() + [{'type': event_type}], 'final': self.response})

    def test_nonzero_exit_rejected_even_with_valid_protocol(self):
        with self.assertRaisesRegex(ValueError, 'process failed'):
            self.run_case({'events': self.events(), 'final': self.response, 'exit_code': 7})
        self.assertTrue((self.directory / 'final.json').exists())
        self.assertIn('synthetic diagnostic', (self.directory / 'stderr.log').read_text())

    def test_missing_final_or_native_message_rejected(self):
        cases = [{'events': self.events()},
                 {'events': [e for e in self.events() if e['type'] != 'item.completed'], 'final': self.response}]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, 'final response missing'):
                    self.run_case(case)

    def test_final_file_must_match_last_agent_message(self):
        alternate = {'summary': 'different synthetic proposal', 'changes': []}
        events = self.events()
        events.insert(-1, {'type': 'item.completed', 'item': {'type': 'agent_message',
                                                           'text': json.dumps(alternate)}})
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.run_case({'events': events, 'final': self.response})
        self.assertEqual(self.run_case({'events': events, 'final': alternate})['response'], alternate)

    def test_malformed_native_lines_and_nonobject_events_rejected(self):
        for case in ({'events': self.events(), 'raw': '{malformed synthetic json', 'final': self.response},
                     {'events': self.events() + [[]], 'final': self.response},
                     {'events': self.events() + [None], 'final': self.response}):
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    self.run_case(case)

    def test_malformed_final_json_and_native_message_rejected(self):
        with self.assertRaises(ValueError):
            self.run_case({'events': self.events(), 'final': self.response, 'final_raw': '{broken'})
        events = self.events()
        events[2]['item']['text'] = 'synthetic non-json message'
        with self.assertRaises(ValueError):
            self.run_case({'events': events, 'final': self.response})

    def test_symlink_final_rejected(self):
        with self.assertRaisesRegex(ValueError, 'final response missing'):
            self.run_case({'events': self.events(), 'final': self.response, 'symlink': True})

    def test_schema_response_is_checked_after_matching_protocol(self):
        for response in ({'summary': 'synthetic missing changes'},
                         {'summary': 'synthetic', 'changes': [], 'extra': True},
                         {'summary': 'synthetic', 'changes': [{'path': '../escape', 'content': 'synthetic'}]}):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    self.run_case({'events': self.events(response), 'final': response})

    def test_verifier_bound_to_candidate_and_consistent_verdict(self):
        for response in (
                {'verdict': 'pass', 'candidate_sha256': 'synthetic-wrong-hash', 'findings': []},
                {'verdict': 'pass', 'candidate_sha256': 'synthetic-candidate-hash', 'findings': ['synthetic defect']},
                {'verdict': 'fail', 'candidate_sha256': 'synthetic-candidate-hash', 'findings': []}):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    self.run_case({'events': self.events(response), 'final': response}, role='verifier')
        response = {'verdict': 'pass', 'candidate_sha256': 'synthetic-candidate-hash', 'findings': []}
        result = self.run_case({'events': self.events(response), 'final': response}, role='verifier')
        self.assertEqual(result['response'], response)

    @unittest.skipUnless(os.name == 'posix', 'process-group cleanup is POSIX-specific')
    def test_successful_parent_exit_stops_heartbeat_child(self):
        # Exercise ProcessRunner directly: the leader exits successfully while
        # its child is still executing and still owns inherited log handles.
        child_code = ('import pathlib,time,sys\np=pathlib.Path(sys.argv[1])\n'
                      'while True:\n p.write_text(str(time.monotonic_ns()))\n time.sleep(.02)\n')
        parent_code = ('import pathlib,subprocess,sys,time\n'
                       'child=subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]])\n'
                       'pathlib.Path(sys.argv[3]).write_text(str(child.pid))\n'
                       'while not pathlib.Path(sys.argv[2]).exists(): time.sleep(.005)\n')
        heartbeat = self.root / 'synthetic-success-child-heartbeat'
        child_pid = self.root / 'synthetic-success-child.pid'
        directory = self.root / 'synthetic-success-process'
        directory.mkdir()
        try:
            result = ProcessRunner().run(
                [sys.executable, '-c', parent_code, child_code, str(heartbeat), str(child_pid)],
                cwd=self.workspace, directory=directory, timeout=3,
                cancelled=lambda: False, launched=self.pids.append)
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(self.pids, [result['pid']])
            with self.assertRaises(ProcessLookupError):
                os.kill(result['pid'], 0)
            # Allow signal delivery, then require the child to make no progress.
            # A zombie awaiting init reaping is acceptable; execution is not.
            time.sleep(.05)
            before = heartbeat.read_text()
            time.sleep(.15)
            self.assertEqual(before, heartbeat.read_text(),
                             'synthetic child survived successful parent exit')
        finally:
            # Clean up even if the regression reappears or an earlier assertion
            # fails. Only target the child/group created by this test.
            if child_pid.exists():
                try:
                    os.kill(int(child_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for pid in self.pids:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(os.name == 'posix', 'process-group cleanup is POSIX-specific')
    def test_real_timeout_kills_parent_and_child(self):
        self.assert_stopped_group('timeout', timeout=.8)

    @unittest.skipUnless(os.name == 'posix', 'process-group cleanup is POSIX-specific')
    def test_real_cancellation_kills_parent_and_child(self):
        self.assert_stopped_group('cancelled', timeout=3,
                                  cancelled=lambda: (self.root / 'synthetic-child.pid').exists())

    def assert_stopped_group(self, reason, **kwargs):
        started = time.monotonic()
        with self.assertRaisesRegex(BridgeStop, reason):
            self.run_case({'hang': True}, **kwargs)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(len(self.pids), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(self.pids[0], 0)
        child = int((self.root / 'synthetic-child.pid').read_text())
        # A killed orphan may await init reaping. Test that the child stopped
        # executing rather than relying on ps access or immediate PID removal.
        heartbeat = self.root / 'synthetic-child-heartbeat'
        time.sleep(.05)
        before = heartbeat.read_text()
        time.sleep(.15)
        after = heartbeat.read_text()
        if before != after:
            os.kill(child, signal.SIGKILL)  # cleanup even when the assertion fails
        self.assertEqual(before, after, 'synthetic child survived process-group cleanup')
        self.assertIn('synthetic parent waiting', (self.directory / 'stdout.log').read_text())
        self.assertIn('synthetic diagnostic', (self.directory / 'stderr.log').read_text())

    def test_real_process_output_is_bounded(self):
        with mock.patch('agent_bridge.MAX_LOG_BYTES', 512):
            with self.assertRaisesRegex(ValueError, 'output limit'):
                self.run_case({'raw': 'synthetic output ' * 100})


if __name__ == '__main__':
    unittest.main()
