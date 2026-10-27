"""Round-one regression: accepted ancestor bytes must remain verifiable."""
import unittest
from pathlib import Path
import test_coordinator as base
from test_coordinator import task
from coordinator import digest


class DependencyFreshness(unittest.TestCase):
    setUp = base.Tests.setUp
    tearDown = base.Tests.tearDown
    apply = base.Tests.apply
    submit = base.Tests.submit
    running = base.Tests.running
    result = base.Tests.result

    def chain(self, review_child=False):
        self.submit([task('a'), task('b', ['a'], reviewer='reviewer' if review_child else None), task('c', ['b'])])
        self.running()
        self.result()
        self.upstream = self.artifact
        self.artifact = Path(self.tmp.name) / 'child.txt'
        self.artifact.write_text('child artifact')

    def child_running(self):
        self.apply('claim', 'b')
        self.apply('dispatch', 'b', dispatch_ref='synthetic:child')

    def child_result(self):
        return self.apply('result', 'b', artifact=str(self.artifact), sha256=digest(self.artifact), evidence_ref='synthetic:child-evidence', outcome_ref='synthetic:child-result')

    def test_changed_ancestor_before_claim(self):
        self.chain()
        self.upstream.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'dependency artifact'):
            self.apply('claim', 'b')
        self.assertEqual('queued', self.c.snapshot('R')[1]['state'])

    def test_missing_ancestor_before_claim(self):
        self.chain()
        self.upstream.unlink()
        with self.assertRaisesRegex(ValueError, 'dependency artifact'):
            self.apply('claim', 'b')

    def test_unreadable_ancestor_before_claim(self):
        from unittest.mock import patch
        self.chain()
        with patch('coordinator.digest', side_effect=PermissionError('synthetic unreadable')):
            with self.assertRaisesRegex(ValueError, 'dependency artifact'):
                self.apply('claim', 'b')

    def test_changed_ancestor_before_result(self):
        self.chain()
        self.child_running()
        self.upstream.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'dependency artifact'):
            self.child_result()
        self.assertEqual('running', self.c.snapshot('R')[1]['state'])

    def test_changed_ancestor_before_acceptance(self):
        self.chain(review_child=True)
        self.child_running()
        self.child_result()
        self.upstream.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'dependency artifact'):
            self.apply('accept', 'b', reviewer='reviewer', review_ref='synthetic:review', sha256=digest(self.artifact))
        self.assertEqual('awaiting_review', self.c.snapshot('R')[1]['state'])

    def test_changed_transitive_ancestor(self):
        self.chain()
        self.child_running()
        self.child_result()
        self.upstream.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'dependency artifact'):
            self.apply('claim', 'c')

    def test_valid_chain(self):
        self.chain(review_child=True)
        self.child_running()
        self.child_result()
        self.apply('accept', 'b', reviewer='reviewer', review_ref='synthetic:review', sha256=digest(self.artifact))
        self.assertEqual('reserved', self.apply('claim', 'c')['state'])
