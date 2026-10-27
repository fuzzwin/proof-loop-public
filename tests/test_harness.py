"""Real local processes with explicitly synthetic agent protocol fixtures."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent_bridge as bridge
import harness
import harness_checks as checks

SOURCE = Path(__file__).resolve().parents[1]
HOST = '''import json,sys,uuid
request=json.load(sys.stdin)
role=request['role']
if role=='builder':
 response={'summary':'synthetic fixture repair','changes':[
  {'path':'app.py','content':'def add(a,b): return a+b\\n'},
  {'path':'data.json','content':'{"answer":2}\\n'},
  {'path':'report.md','content':'Approved scope\\n'}]}
elif role=='verifier':
 response={'verdict':'pass','candidate_sha256':request['candidate_sha256'],'findings':[]}
else: response={'findings':['Synthetic diagnostic: inspect the actual failed checks.']}
result={k:request[k] for k in ('protocol_version','request_id','role','candidate_sha256')}
result.update(session_id='synthetic-'+str(uuid.uuid4()),response=response)
print(json.dumps(result))
'''
TOOL = '''import json,sys
request=json.load(sys.stdin)
print(json.dumps({'schema_version':1,'check_id':request['check_id'],'state':'pass',
                 'findings':[],'coverage':['Synthetic external adapter protocol']}))
'''


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'tests').mkdir()
        for path, value in {
            'app.py': 'def add(a,b): return a-b\n',
            'data.json': '{"answer":1}\n', 'report.md': 'TBD\n',
            'requirements.md': 'Addition and answer 2; report must say Approved scope.\n',
            'tests/test_app.py': 'import unittest\nfrom app import add\nclass T(unittest.TestCase):\n def test_add(self): self.assertEqual(3,add(1,2))\n',
            'fixture_host.py': HOST, 'external.py': TOOL,
        }.items():
            (self.root / path).write_text(value)
        self.task = {
            'schema_version': 1, 'goal': 'Meet the protected software, data and report requirements.',
            'files': ['app.py', 'data.json', 'report.md', 'requirements.md', 'tests/test_app.py',
                      'fixture_host.py', 'external.py'],
            'editable': ['app.py', 'data.json', 'report.md'],
            'protected': ['requirements.md', 'tests/test_app.py', 'fixture_host.py', 'external.py'],
            'requirements': ['requirements.md'],
            'checks': [
                {'id': 'unit', 'purpose': 'verification', 'criterion': 'Addition tests pass without skips.',
                 'adapter': 'python-unittest', 'start_dir': 'tests', 'pattern': 'test_*.py'},
                {'id': 'data', 'purpose': 'validation', 'criterion': 'The agreed answer is integer 2.',
                 'adapter': 'json-equals', 'path': 'data.json', 'assertions': [{'pointer': '/answer', 'equals': 2}]},
                {'id': 'report', 'purpose': 'validation', 'criterion': 'The report states its approved scope.',
                 'adapter': 'text', 'path': 'report.md', 'contains': ['Approved scope'], 'excludes': ['TBD']},
                {'id': 'tool', 'purpose': 'verification', 'criterion': 'The external adapter supplies scoped results.',
                 'adapter': 'command-json', 'argv': ['{python}', '-B', 'external.py'], 'controls': ['external.py'],
                 'coverage': ['Synthetic external adapter protocol']},
            ],
            'specialists': [{'id': 'diagnostic', 'instructions': 'Inspect failed checks.', 'when': ['quality_failure']}],
            'limits': {'max_repairs': 2, 'max_agent_calls': 6, 'agent_timeout_seconds': 5,
                       'check_timeout_seconds': 5, 'max_run_seconds': 60},
            'host': {'kind': 'command', 'argv': ['{python}', '-B', 'fixture_host.py'], 'controls': ['fixture_host.py']},
        }
        self.write_task()

    def write_task(self):
        (self.root / 'task.json').write_text(json.dumps(self.task))

    def open(self, run='trial'):
        value = harness.HarnessBridge(self.root, run, 'task.json')
        self.addCleanup(value.close)
        return value

    def cli(self, command, run='cli'):
        return subprocess.run([sys.executable, '-B', str(SOURCE / 'proofloop'), command,
                               '--root', str(self.root), '--task', 'task.json', '--run-id', run],
                              capture_output=True, text=True)

    def test_real_multidomain_failure_specialist_builder_verifier_and_replay(self):
        loop = self.open()
        result = loop.advance()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual('not_accepted', result['acceptance'])
        self.assertEqual(3, result['agent_calls'])
        self.assertEqual(1, result['repair_rounds_used'])
        self.assertEqual('pass', result['verification']['state'])
        self.assertEqual('pass', result['validation']['state'])
        records = [json.loads((self.root / r['path']).read_text()) for r in loop.state['records']]
        initial = records[0]['result']
        self.assertFalse(initial['passed'])
        self.assertEqual(['data', 'report'], initial['validation']['failed'])
        self.assertEqual(3, len(loop.state['threads']))
        self.assertEqual(result, loop.advance())
        self.assertEqual('current', harness.read_status(self.root, 'trial', 'task.json')['freshness']['state'])

    def test_check_only_never_launches_host_and_can_resume(self):
        loop = self.open()
        with patch.object(harness.CommandHost, 'run', side_effect=AssertionError('host launched')):
            result = loop.check()
            self.assertEqual('fail', result['verification']['state'])
            self.assertEqual(0, result['agent_calls'])
            self.assertEqual(result, loop.check())
        self.assertEqual('completed_advisory', loop.advance()['state'])

    def test_cli_check_failure_and_run_success(self):
        checked = self.cli('harness-check')
        self.assertEqual(2, checked.returncode, checked.stderr)
        self.assertEqual(0, json.loads(checked.stdout)['agent_calls'])
        result = self.cli('harness-run')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('completed_advisory', json.loads(result.stdout)['state'])

    def test_status_has_no_filesystem_writes_process_database_lock_or_codex(self):
        loop = self.open()
        loop.advance()
        before = bridge.tree_map(self.root)
        original_open = os.open
        def readonly(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            return original_open(path, flags, *args, **kwargs)
        with (patch.object(harness, 'HarnessBridge', side_effect=AssertionError('workflow')),
              patch.object(subprocess, 'Popen', side_effect=AssertionError('process')),
              patch.object(sqlite3, 'connect', side_effect=AssertionError('database')),
              patch.object(bridge, 'CodexHost', side_effect=AssertionError('Codex')),
              patch.object(bridge.core, 'project_lock', side_effect=AssertionError('lock')),
              patch.object(os, 'open', side_effect=readonly) as observed_open):
            # Preserve the capability declaration when instrumenting os.open.
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {observed_open}):
                report = harness.read_status(self.root, 'trial', 'task.json')
        self.assertEqual('current', report['freshness']['state'], report)
        self.assertEqual('pass', report['validation']['state'])
        self.assertEqual('not_accepted', report['acceptance'])
        self.assertEqual(before, bridge.tree_map(self.root))

    def test_zero_repair_budget_stops_before_any_host(self):
        self.task['limits']['max_repairs'] = 0
        self.write_task()
        result = self.open().advance()
        self.assertEqual('blocked', result['state'])
        self.assertEqual(0, result['agent_calls'])

    def test_json_boolean_does_not_satisfy_integer_criterion(self):
        (self.root / 'data.json').write_text('{"answer":true}')
        self.task['checks'][1]['assertions'][0]['equals'] = 1
        self.write_task()
        self.assertIn('data', self.open().check()['validation']['failed'])

    def test_escaped_json_pointers_and_missing_fields(self):
        self.assertEqual(3, checks.resolve_pointer({'a/b': [{'~key': 3}]}, '/a~1b/0/~0key'))
        for pointer in ('x', '/a~2b', '/trailing~'):
            with self.assertRaises(ValueError):
                checks.pointer_parts(pointer)
        self.task['checks'][1]['assertions'] = [{'pointer': '/missing', 'equals': 2}]
        self.write_task()
        self.assertEqual('fail', self.open().check()['validation']['state'])

    def test_skipped_and_empty_test_suites_never_pass(self):
        for index, test in enumerate((
            'import unittest\nclass T(unittest.TestCase):\n @unittest.skip("deliberate")\n def test_skip(self): pass\n',
            '# no tests\n')):
            (self.root / 'tests/test_app.py').write_text(test)
            result = self.open('case' + str(index)).check()
            self.assertIn('unit', result['verification']['failed'])

    def test_malformed_tool_protocol_stops_without_repair_or_agent(self):
        (self.root / 'external.py').write_text('print("not a scoped result")\n')
        loop = self.open()
        with self.assertRaisesRegex(ValueError, 'protocol uncertain'):
            loop.advance()
        self.assertEqual('blocked_uncertain', loop.state['status'])
        self.assertEqual(0, loop.state['agent_calls'])
        with self.assertRaisesRegex(ValueError, 'unfinished stage'):
            loop.advance()

    def test_false_pass_exit_disagreement_stops(self):
        (self.root / 'external.py').write_text(TOOL + '\nsys.exit(1)\n')
        with self.assertRaisesRegex(ValueError, 'result/exit disagree'):
            self.open().advance()

    def test_partial_external_coverage_cannot_pass(self):
        (self.root / 'external.py').write_text(TOOL.replace('Synthetic external adapter protocol', 'different case'))
        with self.assertRaisesRegex(ValueError, 'coverage does not match'):
            self.open().advance()

    def test_oversized_external_report_stops_with_raw_output_preserved(self):
        (self.root / 'external.py').write_text('print("x" * (256 * 1024 + 1))\n')
        loop = self.open()
        with self.assertRaisesRegex(ValueError, 'exceeds 256 KiB'):
            loop.advance()
        output = next(loop.directory.glob('steps/*/check-3/stdout.log'))
        self.assertGreater(output.stat().st_size, 256 * 1024)
        self.assertEqual(0, loop.state['agent_calls'])

    def test_snapshot_changes_block_and_preserve_project(self):
        (self.root / 'external.py').write_text('from pathlib import Path\nPath("data.json").write_text("changed")\n' + TOOL)
        with self.assertRaisesRegex(ValueError, 'changed the frozen snapshot'):
            self.open().advance()
        self.assertEqual('{"answer":1}\n', (self.root / 'data.json').read_text())

    def test_changed_contract_and_nested_evidence_block_replay(self):
        loop = self.open()
        loop.advance()
        log = next(loop.directory.glob('steps/000-quality-0/check-0/stdout.log'))
        log.write_text('tampered')
        report = harness.read_status(self.root, 'trial', 'task.json')
        self.assertEqual('stale', report['freshness']['state'])
        self.assertTrue(report['historical'])
        with self.assertRaisesRegex(ValueError, 'nested stage evidence'):
            loop.advance()

    def test_host_request_mismatch_blocks_and_preserves_failed_output(self):
        (self.root / 'fixture_host.py').write_text(HOST.replace("print(json.dumps(result))", "result['request_id']='wrong'\nprint(json.dumps(result))"))
        loop = self.open()
        with self.assertRaisesRegex(ValueError, 'does not match this request'):
            loop.advance()
        self.assertEqual(1, loop.state['agent_calls'])
        self.assertTrue(list(loop.directory.glob('steps/*specialist*/stdout.log')))

    def test_reused_host_session_cannot_verify_its_build(self):
        self.task['specialists'] = []
        self.write_task()
        (self.root / 'fixture_host.py').write_text(HOST.replace("'synthetic-'+str(uuid.uuid4())", "'synthetic-reused'"))
        loop = self.open()
        with self.assertRaisesRegex(ValueError, 'identity reused'):
            loop.advance()
        self.assertNotEqual('completed_advisory', loop.state['status'])

    def test_verifier_findings_require_another_builder_and_fresh_verifier(self):
        extra = '''from pathlib import Path
if role=='builder' and 'return a+b' in Path('app.py').read_text():
 response['changes'][0]['content']+='#reviewed\\n'
if role=='verifier' and '#reviewed' not in Path('app.py').read_text():
 response.update(verdict='fail',findings=['Include the requested review note.'])
'''
        (self.root / 'fixture_host.py').write_text(HOST.replace('result={k:request[k]', extra + 'result={k:request[k]'))
        result = self.open().advance()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual(2, result['repair_rounds_used'])
        self.assertEqual(5, result['agent_calls'])

    def test_failed_criteria_stop_at_two_repairs(self):
        extra = '''if role=='builder':
 response['changes'][0]['content']+='#'+str(uuid.uuid4())+'\\n'
 response['changes'][1]['content']='{"answer":3}\\n'
'''
        (self.root / 'fixture_host.py').write_text(HOST.replace('result={k:request[k]', extra + 'result={k:request[k]'))
        result = self.open().advance()
        self.assertEqual('blocked', result['state'])
        self.assertEqual('repair limit reached', result['reason'])
        self.assertEqual(2, result['repair_rounds_used'])
        self.assertEqual(4, result['agent_calls'])

    def test_call_budget_survives_checkpoint(self):
        self.task['limits']['max_agent_calls'] = 1
        self.write_task()
        loop = self.open()
        loop.advance(2)  # Initial failed checks and specialist dispatch.
        loop.close()
        resumed = self.open()
        with self.assertRaisesRegex(ValueError, 'dispatch budget'):
            resumed.advance()
        self.assertEqual(1, resumed.state['agent_calls'])

    def test_tampered_state_quality_is_rejected(self):
        loop = self.open()
        loop.check()
        loop.state['quality']['passed'] = True
        loop.save()
        with self.assertRaisesRegex(ValueError, 'differs from stage evidence'):
            loop.advance()

    def test_status_rejects_unknown_or_inconsistent_result(self):
        plan = harness.compile_task(self.task, 'task.json')
        loop = self.open()
        loop.check()
        result = copy.deepcopy(loop.state['quality'])
        result['checks'][1]['state'] = 'pass'
        with self.assertRaisesRegex(ValueError, 'verdict disagrees'):
            harness.quality_summary(result, plan)

    def test_check_only_does_not_require_codex_installed(self):
        self.task['host'] = {'kind': 'codex'}
        self.write_task()
        with patch.object(bridge, 'CodexHost', side_effect=AssertionError('Codex unavailable')):
            self.assertEqual(0, self.open().check()['agent_calls'])

    def test_timed_out_check_stops_and_preserves_cleanup_evidence(self):
        self.task['limits']['check_timeout_seconds'] = 1
        self.write_task()
        (self.root / 'external.py').write_text('import time\ntime.sleep(10)\n')
        loop = self.open()
        with self.assertRaisesRegex(ValueError, 'process timeout'):
            loop.advance()
        self.assertEqual('blocked_uncertain', loop.state['status'])
        self.assertEqual(0, loop.state['agent_calls'])
        cleanup = next(loop.directory.glob('steps/*/check-3/cleanup.json'))
        self.assertEqual('complete', json.loads(cleanup.read_text())['status'])

    def test_invalid_contracts_make_no_run_state(self):
        cases = []
        wrong = copy.deepcopy(self.task); wrong['checks'][0]['adapter'] = 'unknown'; cases.append(wrong)
        wrong = copy.deepcopy(self.task); wrong['checks'][1]['path'] = '../outside'; cases.append(wrong)
        wrong = copy.deepcopy(self.task); wrong['requirements'] = ['app.py']; cases.append(wrong)
        wrong = copy.deepcopy(self.task); wrong['checks'] = [wrong['checks'][0]]; cases.append(wrong)
        wrong = copy.deepcopy(self.task); wrong['limits']['max_repairs'] = True; cases.append(wrong)
        wrong = copy.deepcopy(self.task); wrong['protected'].remove('tests/test_app.py'); cases.append(wrong)
        wrong = copy.deepcopy(self.task); wrong['checks'][3]['controls'] = ['app.py']; cases.append(wrong)
        wrong = copy.deepcopy(self.task); wrong['host']['controls'] = ['app.py']; cases.append(wrong)
        for value in cases:
            (self.root / 'task.json').write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                self.open()
            self.assertFalse((self.root / '.proof').exists())

    def test_duplicate_json_keys_and_nonfinite_are_rejected_before_state(self):
        original = json.dumps(self.task)
        for raw in ('{"schema_version":1,' + original[1:], original.replace('"max_repairs": 2', '"max_repairs": NaN')):
            (self.root / 'task.json').write_text(raw)
            with self.assertRaises(ValueError):
                self.open()
        self.assertFalse((self.root / '.proof').exists())


if __name__ == '__main__':
    unittest.main()
