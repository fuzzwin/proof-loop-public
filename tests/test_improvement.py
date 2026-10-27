import json
import tempfile
import unittest
from pathlib import Path
from improvement import ImprovementGate, stable_hash


def evaluator(base, candidate, dataset):
    # Actual deterministic comparison; no model/network execution.
    cases = json.loads(dataset.read_text())
    return {'base_passed': all(base.read_text() == c['expected'] for c in cases),
            'candidate_passed': all(candidate.read_text() == c['expected'] for c in cases)}


def broken_evaluator(base, candidate, dataset):
    raise RuntimeError('interruption')


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name, text in [('base', 'bad'), ('candidate', 'good'), ('control', 'frozen')]:
            (self.root / name).write_text(text)
        for name in ['regression', 'holdout']:
            (self.root / name).write_text(json.dumps([{'case_id': name, 'expected': 'good'}]))
        self.gate = ImprovementGate(self.root / 'gate.db')

    def tearDown(self):
        self.gate.close()
        self.tmp.cleanup()

    def propose(self, identity='one', **kwargs):
        defaults = dict(proposer='builder', hypothesis='Correct comparison target', base=self.root / 'base',
                        candidate=self.root / 'candidate', evaluator=evaluator, regression=self.root / 'regression',
                        holdout=self.root / 'holdout', protected_files=[self.root / 'control'], change_kind='adapter', unknowns=[])
        defaults.update(kwargs)
        return self.gate.propose(identity, **defaults)

    def test_evaluate_then_review_never_promotes(self):
        self.propose()
        row = self.gate.evaluate('one', evaluator)
        self.assertEqual('awaiting_review', row['state'])
        result = self.gate.review('one', reviewer='independent-host', evidence_sha256=stable_hash(row['evidence']))
        self.assertFalse(result['promoted'])
        self.assertFalse(result['identity_authenticated'])

    def test_same_reviewer_rejected(self):
        self.propose()
        row = self.gate.evaluate('one', evaluator)
        with self.assertRaisesRegex(ValueError, 'distinct reviewer'):
            self.gate.review('one', reviewer='builder', evidence_sha256=stable_hash(row['evidence']))

    def test_changed_artifacts_invalidate_review(self):
        for name in ['base', 'candidate', 'regression', 'holdout', 'control']:
            original = (self.root / name).read_bytes()
            self.propose(name)
            # Use a new holdout case identity for each independent evaluation.
            if name == 'base':
                row = self.gate.evaluate(name, evaluator)
            else:
                # This tests freshness at evaluation without reusing consumed holdout.
                row = None
            (self.root / name).write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'changed'):
                if row:
                    self.gate.review(name, reviewer='reviewer', evidence_sha256=stable_hash(row['evidence']))
                else:
                    self.gate.evaluate(name, evaluator)
            (self.root / name).write_bytes(original)

    def test_regression_failure_rejects(self):
        (self.root / 'candidate').write_text('bad')
        self.propose()
        row = self.gate.evaluate('one', evaluator)
        self.assertEqual('rejected', row['state'])
        self.assertNotIn('holdout', row['evidence']['reports'])

    def test_no_improvement_rejects(self):
        (self.root / 'base').write_text('good')
        self.propose()
        self.assertEqual('rejected', self.gate.evaluate('one', evaluator)['state'])

    def test_holdout_reuse_and_exposure_block(self):
        self.propose()
        self.propose('two')
        self.gate.evaluate('one', evaluator)
        with self.assertRaisesRegex(ValueError, 'holdout'):
            self.gate.evaluate('two', evaluator)

    def test_exposed_holdout_blocked(self):
        self.propose()
        self.gate.expose_holdout(self.root / 'holdout', 'inspected before trial')
        with self.assertRaisesRegex(ValueError, 'exposed'):
            self.gate.evaluate('one', evaluator)

    def test_crash_burns_budget(self):
        self.propose(evaluator=broken_evaluator)
        with self.assertRaises(RuntimeError):
            self.gate.evaluate('one', broken_evaluator)
        self.assertEqual('blocked', self.gate.row('one')['state'])
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.gate.evaluate('one', broken_evaluator)

    def test_unknowns_block(self):
        self.propose(unknowns=['missing security coverage'])
        with self.assertRaisesRegex(ValueError, 'unknowns'):
            self.gate.evaluate('one', evaluator)

    def test_policy_changes_rejected(self):
        with self.assertRaisesRegex(ValueError, 'prohibited'):
            self.propose(change_kind='permissions')

    def test_overlapping_data_rejected(self):
        (self.root / 'holdout').write_bytes((self.root / 'regression').read_bytes())
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.propose()

    def test_evaluator_substitution_rejected(self):
        self.propose()
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.gate.evaluate('one', broken_evaluator)

    def test_reencoded_holdout_case_reuse_rejected(self):
        self.propose()
        self.gate.evaluate('one', evaluator)
        (self.root / 'holdout').write_text('[ {"expected": "good", "case_id": "holdout"} ]')
        self.propose('two')
        with self.assertRaisesRegex(ValueError, 'case already'):
            self.gate.evaluate('two', evaluator)

    def test_prior_regression_cannot_become_later_holdout(self):
        self.propose()
        self.gate.evaluate('one', evaluator)
        prior = (self.root / 'regression').read_text()
        (self.root / 'holdout').write_text(prior)
        (self.root / 'regression').write_text('[{"case_id":"new-training", "expected":"good"}]')
        self.propose('two')
        with self.assertRaisesRegex(ValueError, 'holdout'):
            self.gate.evaluate('two', evaluator)

    def test_proposed_regression_is_already_exposed(self):
        self.propose()
        (self.root / 'holdout').write_text('[ {"expected":"good", "case_id":"regression"} ]')
        (self.root / 'regression').write_text('[{"case_id":"new-training", "expected":"good"}]')
        self.propose('two')
        with self.assertRaisesRegex(ValueError, 'case already'):
            self.gate.evaluate('two', evaluator)

    def test_later_proposal_exposes_earlier_pending_holdout(self):
        self.propose()
        (self.root / 'other-regression').write_text('[ {"expected":"good", "case_id":"holdout"} ]')
        (self.root / 'other-holdout').write_text('[{"case_id":"new-holdout", "expected":"good"}]')
        self.propose('two', regression=self.root / 'other-regression', holdout=self.root / 'other-holdout')
        with self.assertRaisesRegex(ValueError, 'case already'):
            self.gate.evaluate('one', evaluator)
