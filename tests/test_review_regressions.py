"""Regression cases from the independent review of d317ca4.

Pins in these synthetic fixtures model approval; they do not assert custody.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import proofloop as core
from orchestration import SyntheticLoop


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'project'
        self.root.mkdir()
        self.pin = Path(self.temp.name) / 'pin.json'
        self.source = {'items': [{'id': 1}], 'fixture_classification': 'synthetic'}
        self.view = {'record_count': 1, 'ids': [1], 'fixture_classification': 'synthetic'}

    def write(self, relative, value):
        path = self.root / relative
        path.write_text(json.dumps(value))

    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = core.main(list(map(str, args)))
        return code, stdout.getvalue(), stderr.getvalue()

    def start(self, extra=None, acceptance_name='acceptance.json'):
        self.write('source.json', self.source)
        self.write('view.json', self.view)
        self.write(acceptance_name, {'required': 'exact projection'})
        self.assertEqual(0, self.call('init', self.root)[0])
        inputs = ['source.json', 'view.json']
        for name, data in (extra or {}).items():
            self.write(name, data)
            inputs.append(name)
        self.pin.write_text(json.dumps({'schema_version': 1,
                                       'acceptance_tests': core.snapshot(self.root, [acceptance_name])}))
        args = ['start', '--root', self.root, '--run-id', 'review', '--baseline-pin', self.pin,
                '--goal', 'Synthetic review regression', '--scope', 'local fixture',
                '--check', 'source-view-drift', '--acceptance-test', acceptance_name,
                '--done', 'Exact projection with fresh inputs']
        for name in inputs:
            args += ['--input', name]
        self.assertEqual(0, self.call(*args)[0])
        pin = core.load_json(self.pin)
        pin['approved_run_card_sha256'] = core.sha256(self.root / '.proof/runs/review/run-card.json')
        self.pin.write_text(json.dumps(pin))

    def make_receipt(self, mutate_log=None):
        relative = '.proof/runs/review/logs/drift.json'
        self.call('drift', '--root', self.root, '--run-id', 'review',
                  '--source', 'source.json', '--view', 'view.json', '--log', relative)
        log = core.load_json(self.root / relative)
        if mutate_log:
            mutate_log(log)
            self.write(relative, log)
        self.write('result.json', {'synthetic': True})
        path = self.root / '.proof/runs/review/receipt.json'
        receipt = core.load_json(path)
        receipt.update(run_state='passed_for_scope', checks={'source-view-drift': {
            'state': 'pass', 'reason': 'Synthetic acceptance fixture', 'log': {
                'path': relative, 'sha256': core.sha256(self.root / relative), 'runner': 'proofloop-drift'}}},
            independent_review={'status': 'not_applicable', 'reason': 'Synthetic local fixture'},
            claims=[{'label': 'Verified', 'claim_type': 'check_pass', 'check': 'source-view-drift',
                     'supports_checks': ['source-view-drift'], 'claim': 'Check source-view-drift passed.',
                     'evidence': [relative]}],
            coverage={'checked': ['record_count', 'ordered_ids', 'fixture_classification'], 'not_checked': []},
            outputs={'result.json': core.sha256(self.root / 'result.json')})
        path.write_text(json.dumps(receipt))
        return path

    def validate(self):
        code, stdout, stderr = self.call('validate', '--root', self.root, '--run-id', 'review',
                                       '--baseline-pin', self.pin)
        self.assertFalse(stderr)
        return code, json.loads(stdout)

    def test_literal_acceptance_prefix_input_cannot_be_hidden_by_acceptance_entry(self):
        name = '[acceptance-test] acceptance.json'
        self.start({name: {'original': True}})
        self.make_receipt()
        self.assertEqual(0, self.validate()[0])
        self.write(name, {'changed': True})
        code, result = self.validate()
        self.assertEqual(2, code)
        self.assertFalse(result['input_fresh'])
        self.assertIn(name, result['changed_inputs'])

    def test_literal_acceptance_prefix_is_not_stripped_from_input_path(self):
        name = '[acceptance-test] source.json'
        self.start({name: self.source})
        self.make_receipt()
        self.assertEqual(0, self.validate()[0])
        self.write(name, {'changed': True})
        self.assertFalse(self.validate()[1]['input_fresh'])

    def test_drift_rejects_boolean_count_and_nested_boolean_id(self):
        self.source['items'] = [{'id': {'nested': [1]}}]
        self.view.update(record_count=True, ids=[{'nested': [True]}])
        self.start()
        code, stdout, _ = self.call('drift', '--root', self.root, '--run-id', 'review',
                                   '--source', 'source.json', '--view', 'view.json',
                                   '--log', '.proof/runs/review/logs/drift.json')
        self.assertEqual(1, code)
        self.assertEqual('fail', json.loads(stdout)['state'])

    def test_prefixed_acceptance_filename_is_checked_literally(self):
        name = '[acceptance-test] contract.json'
        self.start(acceptance_name=name)
        self.make_receipt()
        self.assertEqual(0, self.validate()[0])
        self.write(name, {'changed': True})
        self.assertFalse(self.validate()[1]['input_fresh'])

    def test_drift_rejects_false_in_place_of_zero_id(self):
        self.source['items'] = [{'id': 0}]
        self.view['ids'] = [False]
        self.start()
        code, _, _ = self.call('drift', '--root', self.root, '--run-id', 'review',
                               '--source', 'source.json', '--view', 'view.json',
                               '--log', '.proof/runs/review/logs/drift.json')
        self.assertEqual(1, code)

    def test_old_false_pass_log_cannot_validate_boolean_projection(self):
        self.view.update(record_count=True, ids=[True])
        self.start()
        def old_pass(log):
            log.update(state='pass', exit_code=0, reason='generated view matches the declared source projection')
        self.make_receipt(old_pass)
        self.assertEqual(2, self.validate()[0])

    def test_log_expected_and_actual_types_are_recomputed(self):
        self.start()
        receipt = self.make_receipt()
        original_receipt = receipt.read_bytes()
        log_path = self.root / '.proof/runs/review/logs/drift.json'
        original_log = log_path.read_bytes()
        for field in ('actual', 'expected'):
            with self.subTest(field=field):
                log = json.loads(original_log)
                log[field]['ids'] = [True]
                log_path.write_text(json.dumps(log))
                data = json.loads(original_receipt)
                data['checks']['source-view-drift']['log']['sha256'] = core.sha256(log_path)
                receipt.write_text(json.dumps(data))
                self.assertEqual(2, self.validate()[0])

    def test_malformed_receipt_fields_produce_blocked_reports(self):
        self.start()
        path = self.make_receipt()
        original = path.read_bytes()
        mutations = {
            'checked-object': lambda r: r['coverage'].update(checked=[{}]),
            'unchecked-array': lambda r: r['coverage'].update(not_checked=[[]]),
            'run-state': lambda r: r.update(run_state=[]),
            'human-decision': lambda r: r.update(human_decision={}),
            'check-state': lambda r: r['checks']['source-view-drift'].update(state=[]),
            'review-state': lambda r: r['independent_review'].update(status=[]),
            'claim-check': lambda r: r['claims'][0].update(check=[]),
            'repair-count': lambda r: r.update(repair_rounds_used=True),
            'revision': lambda r: r.update(revision=False),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                data = json.loads(original)
                mutate(data)
                path.write_text(json.dumps(data))
                code, result = self.validate()
                self.assertEqual(2, code)
                self.assertEqual('BLOCKED', result['status'])

    def start_loop(self):
        self.write('source.json', self.source)
        self.write('view.json', self.view)
        self.write('acceptance.json', {'required': 'exact projection'})
        self.write('plan.json', dict(adapter='synthetic-projection-v1', check='source-view-drift',
                                    source='source.json', view='view.json', acceptance_test='acceptance.json',
                                    allow_repair=True))
        loop = SyntheticLoop(self.root, 'review', 'plan.json')
        self.addCleanup(loop.close)
        return loop

    def test_orchestration_repairs_boolean_projection(self):
        self.view.update(record_count=True, ids=[True])
        loop = self.start_loop()
        self.assertEqual('completed_advisory', loop.advance()['state'])
        view = core.load_json(self.root / 'view.json')
        self.assertIs(type(view['record_count']), int)
        self.assertIs(type(view['ids'][0]), int)
        self.assertTrue(core.load_json(loop.directory / 'repair.json')['changed'])

    def test_cancelled_stage_is_blocked_and_cli_returns_nonzero(self):
        loop = self.start_loop()
        loop.event('inspect', 'cancel')
        self.assertEqual('blocked', loop.advance()['state'])
        self.assertEqual(2, self.call('orchestrate', '--root', self.root, '--run-id', 'review',
                                      '--plan', 'plan.json')[0])

    def test_failed_stage_is_blocked_and_cli_returns_nonzero(self):
        loop = self.start_loop()
        loop.event('inspect', 'claim')
        loop.event('inspect', 'dispatch', dispatch_ref='synthetic:failed-process')
        loop.event('inspect', 'recover')
        loop.event('inspect', 'reconcile', outcome='failed', evidence_ref='synthetic:process-exit')
        self.assertEqual('blocked', loop.advance()['state'])
        self.assertEqual(2, self.call('orchestrate', '--root', self.root, '--run-id', 'review',
                                      '--plan', 'plan.json')[0])

    def test_cancelled_repair_after_successful_checkpoint_is_blocked(self):
        loop = self.start_loop()
        self.assertEqual('checkpoint', loop.advance(1)['state'])
        loop.event('repair', 'cancel')
        self.assertEqual('blocked', loop.advance()['state'])
        self.assertEqual('succeeded', loop.row('inspect')['state'])
        self.assertEqual(2, self.call('orchestrate', '--root', self.root, '--run-id', 'review',
                                      '--plan', 'plan.json')[0])
