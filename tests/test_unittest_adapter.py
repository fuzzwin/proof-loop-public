"""Real subprocess adapter tests with synthetic project code; no model calls."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import agent_bridge as bridge
from test_agent_bridge import ScriptedHost


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / 'project'
        self.workspace.mkdir()

    def run_suite(self, source, ids):
        (self.workspace / 'test_sample.py').write_text(source)
        directory = self.root / 'execution'
        directory.mkdir()
        report = directory / 'tests.json'
        expected = directory / 'expected.json'
        expected.write_text(json.dumps(ids))
        process = bridge.ProcessRunner().run(
            [sys.executable, '-B', str(Path(bridge.__file__).with_name('unittest_adapter.py')), str(report), str(expected), 'test_sample.py'],
            cwd=self.workspace, directory=directory, timeout=5, cancelled=lambda: False, launched=lambda pid: None)
        result = json.loads(report.read_text())
        return bridge.validate_test_report(result, {'expected_ids': ids, 'tests': ['test_sample.py']}, process)

    def test_skipped_tests_never_pass(self):
        result = self.run_suite("import unittest\nclass T(unittest.TestCase):\n @unittest.skip('not exercised')\n def test_one(self): pass\n", ['test_sample.T.test_one'])
        self.assertFalse(result['passed'])
        self.assertEqual('skipped', result['tests'][0]['status'])

    def test_skipped_subtest_never_passes_parent(self):
        result = self.run_suite("import unittest\nclass T(unittest.TestCase):\n def test_one(self):\n  with self.subTest(x=1): self.skipTest('unavailable')\n", ['test_sample.T.test_one'])
        self.assertFalse(result['passed'])
        self.assertEqual('skipped', result['tests'][0]['status'])

    def test_failing_subtest_is_failure(self):
        result = self.run_suite("import unittest\nclass T(unittest.TestCase):\n def test_one(self):\n  with self.subTest(x=1): self.assertEqual(1,2)\n", ['test_sample.T.test_one'])
        self.assertFalse(result['passed'])
        self.assertEqual('fail', result['tests'][0]['status'])

    def test_missing_expected_test_cannot_pass(self):
        result = self.run_suite('import unittest\nclass T(unittest.TestCase):\n def test_one(self): pass\n',
                                ['test_sample.T.test_one', 'test_sample.T.test_missing'])
        self.assertFalse(result['passed'])
        self.assertEqual(['test_sample.T.test_missing'], result['coverage']['missing'])

    def test_empty_suite_cannot_pass(self):
        result = self.run_suite('import unittest\n', ['test_sample.T.test_missing'])
        self.assertFalse(result['passed'])
        self.assertEqual(['test_sample.T.test_missing'], result['coverage']['missing'])

    def test_fixture_failure_cannot_pass(self):
        source = ('import unittest\nclass T(unittest.TestCase):\n'
                  ' @classmethod\n def tearDownClass(cls): raise RuntimeError("fixture broke")\n'
                  ' def test_one(self): pass\n')
        result = self.run_suite(source, ['test_sample.T.test_one'])
        self.assertFalse(result['passed'])
        self.assertEqual('error', result['fixture_errors'][0]['status'])

    def test_class_level_skip_cannot_pass(self):
        source = ('import unittest\nclass T(unittest.TestCase):\n'
                  ' @classmethod\n def setUpClass(cls): raise unittest.SkipTest("unavailable")\n'
                  ' def test_one(self): pass\n')
        result = self.run_suite(source, ['test_sample.T.test_one'])
        self.assertFalse(result['passed'])
        self.assertEqual('skipped', result['fixture_errors'][0]['status'])
        self.assertEqual(['test_sample.T.test_one'], result['coverage']['missing'])

    def test_collection_error_is_recorded_as_failed_quality(self):
        result = self.run_suite('import nonexistent_synthetic_dependency\n', ['test_sample.T.test_one'])
        self.assertFalse(result['passed'])
        self.assertIn('nonexistent_synthetic_dependency', result['collection_errors'][0]['error'])
        self.assertEqual(['test_sample.T.test_one'], result['coverage']['missing'])

    def test_import_time_system_exit_is_failed_collection(self):
        result = self.run_suite('import sys\nsys.exit(7)\n', ['test_sample.T.test_one'])
        self.assertFalse(result['passed'])
        self.assertIn('SystemExit: 7', result['collection_errors'][0]['error'])
        self.assertEqual(['test_sample.T.test_one'], result['coverage']['missing'])

    def test_real_multifile_repair_requirement_evidence_and_replay(self):
        example = Path(bridge.__file__).parent / 'examples' / 'order-total'
        shutil.copytree(example, self.workspace, dirs_exist_ok=True)
        changes = [
            {'path': 'pricing.py', 'content': "from decimal import Decimal, ROUND_HALF_UP\ndef discounted_price(price, discount):\n return (Decimal(str(price)) * (1 - Decimal(str(discount)))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"},
            {'path': 'order.py', 'content': "from decimal import Decimal\nfrom pricing import discounted_price\ndef order_total(lines):\n return sum((discounted_price(p, d) * q for p,q,d in lines), Decimal('0.00'))\n"}]
        class Host(ScriptedHost):
            def run(self, role, *args, **kwargs):
                result = super().run(role, *args, **kwargs)
                if role == 'verifier':
                    result['response']['requirements'] = [dict(id=r['id'], status='pass',
                        evidence=['adapter:python-unittest', 'file:requirements.txt'], reason='Synthetic verifier assessment')
                        for r in kwargs['requirements']]
                return result
        host = Host(builder=lambda n: changes)
        loop = bridge.AgentBridge(self.workspace, 'multifile', 'plan.json', host=host)
        self.addCleanup(loop.close)
        result = loop.advance()
        self.assertEqual('completed_advisory', result['state'])
        self.assertEqual(['order.py', 'pricing.py'], result['changed_files'])
        self.assertEqual(3, result['quality']['tests']['tests_run'])
        self.assertEqual(3, len(result['requirements']))
        self.assertEqual(['builder', 'verifier'], [r for r,p in host.calls])
        self.assertEqual(result, loop.advance())
        self.assertEqual(2, len(host.calls))
