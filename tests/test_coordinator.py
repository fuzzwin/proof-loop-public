import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from coordinator import Coordinator, digest


def task(name, deps=None, resources=None, reviewer=None):
    return dict(id=name, description='Synthetic ' + name, dependencies=deps or [], resources=resources or [], reviewer=reviewer)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'state.db')
        self.c = Coordinator(self.path)
        self.key = 0
        self.artifact = Path(self.tmp.name) / 'artifact.txt'
        self.artifact.write_text('synthetic evidence, not a physical test')

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def apply(self, op, task_id='a', **kw):
        self.key += 1
        row = next(x for x in self.c.snapshot('R') if x['id'] == task_id)
        return self.c.apply(str(self.key), op, run='R', task=task_id, version=row['version'], **kw)

    def submit(self, tasks=None):
        return self.c.apply('submit', 'submit', run='R', tasks=tasks or [task('a')])

    def running(self):
        self.apply('claim')
        return self.apply('dispatch', dispatch_ref='synthetic-host-dispatch')

    def result(self):
        return self.apply('result', artifact=str(self.artifact), sha256=digest(self.artifact), evidence_ref='synthetic:evidence', outcome_ref='synthetic:outcome')

    def test_idempotency_and_frozen_submission(self):
        original = self.submit()
        self.assertEqual(original, self.submit())
        self.c.apply('new-key', 'submit', run='R', tasks=[task('a')])
        with self.assertRaisesRegex(ValueError, 'frozen'):
            self.c.apply('changed', 'submit', run='R', tasks=[task('b')])
        with self.assertRaisesRegex(ValueError, 'idempotency'):
            self.c.apply('submit', 'submit', run='R', tasks=[task('b')])
        first = self.c.apply('claim', 'claim', run='R', task='a', version=0)
        self.assertEqual(first, self.c.apply('claim', 'claim', run='R', task='a', version=0))
        self.assertEqual(1, self.c.db.execute('SELECT count(*) FROM attempts').fetchone()[0])

    def test_bad_dags(self):
        for tasks in ([task('a', ['b'])], [task('a', ['b']), task('b', ['a'])], [task('a'), task('a')]):
            with self.assertRaises(ValueError):
                self.c.apply('bad', 'submit', run='R', tasks=tasks)
        self.assertEqual([], self.c.snapshot('R'))

    def test_dependency_blocking(self):
        self.submit([task('a'), task('b', ['a'])])
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            self.apply('claim', 'b')
        self.apply('block', reason='missing verified evidence')
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            self.apply('claim', 'b')
        self.apply('cancel')
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            self.apply('claim', 'b')

    def test_concurrent_claim(self):
        self.submit()
        def claim(n):
            c = Coordinator(self.path)
            try:
                c.apply('parallel-' + str(n), 'claim', run='R', task='a', version=0)
                return True
            except ValueError:
                return False
            finally:
                c.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual([False, True], sorted(pool.map(claim, range(2))))

    def test_three_worker_limit(self):
        self.submit([task(x) for x in 'abcd'])
        for x in 'abc':
            self.apply('claim', x)
        with self.assertRaisesRegex(ValueError, 'worker limit'):
            self.apply('claim', 'd')
        self.apply('recover')
        with self.assertRaisesRegex(ValueError, 'worker limit'):
            self.apply('claim', 'd')

    def test_resource_collision(self):
        self.submit([task('a', resources=['writer']), task('b', resources=['writer'])])
        self.apply('claim')
        with self.assertRaisesRegex(ValueError, 'resource collision'):
            self.apply('claim', 'b')
        self.apply('cancel')
        with self.assertRaisesRegex(ValueError, 'resource collision'):
            self.apply('claim', 'b')
        self.apply('reconcile', outcome='not_executed', evidence_ref='synthetic:confirmed-not-dispatched')
        self.apply('claim', 'b')

    def test_version_conflict(self):
        self.submit()
        self.apply('claim')
        with self.assertRaisesRegex(ValueError, 'version conflict'):
            self.c.apply('stale', 'dispatch', run='R', task='a', version=0, dispatch_ref='fake')

    def test_hash_and_reviewer_gates(self):
        self.submit([task('a', reviewer='independent-reviewer'), task('b', ['a'])])
        self.running()
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.apply('result', artifact=str(self.artifact), sha256=digest(self.artifact), outcome_ref='done')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.apply('result', artifact=str(self.artifact), sha256='wrong', evidence_ref='e', outcome_ref='o')
        self.assertEqual('awaiting_review', self.result()['state'])
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            self.apply('claim', 'b')
        with self.assertRaisesRegex(ValueError, 'reviewer'):
            self.apply('accept', sha256=digest(self.artifact))
        old_hash = digest(self.artifact)
        self.artifact.write_text('changed output')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.apply('accept', reviewer='independent-reviewer', review_ref='synthetic:review', sha256=old_hash)

    def test_acceptance_and_persistence(self):
        self.submit([task('a', reviewer='reviewer'), task('b', ['a'])])
        self.running()
        self.result()
        self.apply('accept', reviewer='reviewer', review_ref='synthetic:review', sha256=digest(self.artifact))
        self.apply('claim', 'b')
        c = Coordinator(self.path)
        self.assertEqual('succeeded', c.snapshot('R')[0]['state'])
        self.assertGreater(c.db.execute('SELECT count(*) FROM events').fetchone()[0], 4)
        c.close()

    def test_uncertain_dispatch_recovery(self):
        self.submit()
        self.apply('claim')
        self.c.close()
        self.c = Coordinator(self.path)
        with self.assertRaises(ValueError):
            self.apply('claim')
        self.apply('recover')
        with self.assertRaises(ValueError):
            self.apply('dispatch', dispatch_ref='blind retry')
        with self.assertRaises(ValueError):
            self.apply('reconcile', outcome='not_executed')
        self.apply('reconcile', outcome='not_executed', evidence_ref='synthetic:verified-no-dispatch')
        self.running()
        self.assertEqual(2, self.c.db.execute('SELECT count(*) FROM attempts').fetchone()[0])
        self.apply('recover')
        self.apply('reconcile', outcome='running', dispatch_ref='synthetic:existing-worker', evidence_ref='synthetic:worker-running')
        self.assertEqual('succeeded', self.result()['state'])

    def test_cancellation_does_not_release_running_work(self):
        self.submit()
        self.running()
        self.assertEqual('uncertain', self.apply('cancel')['state'])
        with self.assertRaises(ValueError):
            self.result()
        self.apply('reconcile', outcome='running', dispatch_ref='synthetic:existing', evidence_ref='synthetic:still-running')
        with self.assertRaises(ValueError):
            self.result()
        self.apply('recover')
        self.assertEqual('cancelled', self.apply('reconcile', outcome='cancelled', evidence_ref='synthetic:terminated')['state'])
        with self.assertRaises(ValueError):
            self.apply('claim')

    def test_failed_dependency(self):
        self.submit([task('a'), task('b', ['a'])])
        self.running()
        self.apply('recover')
        self.apply('reconcile', outcome='failed', evidence_ref='synthetic:failure')
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            self.apply('claim', 'b')


if __name__ == '__main__':
    unittest.main(verbosity=2)
