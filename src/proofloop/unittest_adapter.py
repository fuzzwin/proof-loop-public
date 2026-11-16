"""Local unittest evidence adapter; no portable acceptance or external custody claim.

Executed in a frozen project snapshot. Exact files and test IDs come from the plan.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback
import unittest


def main():
    report = Path(sys.argv[1])
    expected_ids = json.loads(Path(sys.argv[2]).read_text())
    test_files = sys.argv[3:]
    sys.path.insert(0, str(Path.cwd()))
    suite = unittest.TestSuite()
    collection_errors = []
    for name in test_files:
        path = Path(name)
        # Stable IDs, independent of temporary snapshot directory names.
        module_name = '.'.join(path.with_suffix('').parts)
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        except (Exception, SystemExit):
            collection_errors.append({'file': name, 'error': traceback.format_exc(limit=12)[-12000:]})

    class Result(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.records = []
            self.statuses = {}
            self.fixture_errors = []

        def startTest(self, test):
            super().startTest(test)
            self.statuses[test] = 'incomplete'

        def addSuccess(self, test):
            super().addSuccess(test)
            if self.statuses[test] == 'incomplete':
                self.statuses[test] = 'pass'

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.statuses[test] = 'fail'

        def addError(self, test, err):
            super().addError(test, err)
            if test not in self.statuses:
                self.fixture_errors.append({'id': test.id(), 'status': 'error',
                                            'detail': ''.join(traceback.format_exception(*err))[-12000:]})
            self.statuses[test] = 'error'

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            parent = getattr(test, 'test_case', test)
            if parent not in self.statuses:
                self.fixture_errors.append({'id': parent.id(), 'status': 'skipped', 'detail': str(reason) or 'skipped'})
            self.statuses[parent] = 'skipped'

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self.statuses[test] = 'expected_failure'

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self.statuses[test] = 'unexpected_success'

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                self.statuses[test] = 'fail'

        def stopTest(self, test):
            self.records.append({'id': test.id(), 'status': self.statuses[test]})
            super().stopTest(test)

    result = unittest.TextTestRunner(resultclass=Result, verbosity=2).run(suite)
    ids = [row['id'] for row in result.records]
    coverage = {'missing': sorted(set(expected_ids) - set(ids)),
                'unexpected': sorted(set(ids) - set(expected_ids)),
                'duplicate': sorted({name for name in ids if ids.count(name) > 1})}
    passed = (bool(result.records) and result.wasSuccessful() and
              all(r['status'] == 'pass' for r in result.records) and
              not collection_errors and not result.fixture_errors and not any(coverage.values()))
    data = {'schema_version': 2, 'runtime': {'executable': sys.executable, 'version': sys.version},
            'tests': result.records, 'tests_run': result.testsRun, 'passed': passed,
            'test_files': {n: hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in test_files},
            'collection_errors': collection_errors, 'fixture_errors': result.fixture_errors, 'coverage': coverage}
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(report, flags, 0o600), 'w') as stream:
        json.dump(data, stream, sort_keys=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
