"""Local unittest evidence adapter; no portable acceptance or external custody claim.

Executed in a frozen project snapshot. Exact files and test IDs come from the plan.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


def main():
    report = Path(sys.argv[1])
    sys.path.insert(0, str(Path.cwd()))
    suite = unittest.TestSuite()
    for name in sys.argv[2:]:
        path = Path(name)
        # Stable IDs, independent of temporary snapshot directory names.
        module_name = '.'.join(path.with_suffix('').parts)
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))

    class Result(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.records = []
            self.statuses = {}

        def startTest(self, test):
            super().startTest(test)
            self.statuses[id(test)] = 'incomplete'

        def addSuccess(self, test):
            super().addSuccess(test)
            if self.statuses[id(test)] == 'incomplete':
                self.statuses[id(test)] = 'pass'

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.statuses[id(test)] = 'fail'

        def addError(self, test, err):
            super().addError(test, err)
            self.statuses[id(test)] = 'error'

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            parent = getattr(test, 'test_case', test)
            self.statuses[id(parent)] = 'skipped'

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self.statuses[id(test)] = 'expected_failure'

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self.statuses[id(test)] = 'unexpected_success'

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                self.statuses[id(test)] = 'fail'

        def stopTest(self, test):
            self.records.append({'id': test.id(), 'status': self.statuses[id(test)]})
            super().stopTest(test)

    result = unittest.TextTestRunner(resultclass=Result, verbosity=2).run(suite)
    passed = bool(result.records) and result.wasSuccessful() and all(r['status'] == 'pass' for r in result.records)
    data = {'schema_version': 1, 'runtime': {'executable': sys.executable, 'version': sys.version},
            'tests': result.records, 'tests_run': result.testsRun, 'passed': passed,
            'test_files': {n: hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in sys.argv[2:]}}
    with report.open('x') as stream:
        json.dump(data, stream, sort_keys=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
