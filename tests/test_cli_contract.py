"""Observe the public CLI against fixtures captured before the refactor."""
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / 'proofloop'
INSTALLED_CLI = os.environ.get('PROOFLOOP_TEST_INSTALLED_CLI')
FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'phase0'
CLI_ENV = {
    **os.environ,
    'LC_ALL': 'C',
    'COLUMNS': '80',
    'PYTHONUTF8': '1',
    'PYTHON_COLORS': '0',
    'NO_COLOR': '1',
}


def invoke(*args):
    if INSTALLED_CLI:
        executable = Path(INSTALLED_CLI).resolve()
        command = [str(executable)]
        cwd = executable.parent
    else:
        command = [sys.executable, '-B', str(CLI)]
        cwd = None
    return subprocess.run(
        [*command, *map(str, args)], cwd=cwd,
        capture_output=True, env=CLI_ENV, timeout=30,
    )


def restore_fixture(root, fixture):
    root.mkdir()
    for name in fixture['directories']:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name, entry in fixture['files'].items():
        path = root / name
        path.write_bytes(entry['text'].encode('utf-8'))
        path.chmod(entry['mode'])
    for name, mode in fixture['directories'].items():
        (root / name).chmod(mode)


class CliContractTests(unittest.TestCase):
    maxDiff = None

    def help_text(self, *args):
        result = invoke(*args, '--help')
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        usage, body = result.stdout.decode('utf-8').split('\n\n', 1)
        # Argparse wraps the usage paragraph differently across Python versions.
        return ' '.join(usage.split()) + '\n\n' + body

    def test_help_for_every_subcommand(self):
        snapshot = (FIXTURES / 'cli-help.txt').read_text(encoding='utf-8')
        headings = re.findall(r'^\$ proofloop (.*)--help$', snapshot, flags=re.MULTILINE)
        root_help = self.help_text()
        advertised = re.search(r'\{([\w,-]+)\}', root_help).group(1).split(',')
        self.assertEqual([heading.strip() for heading in headings], ['', *advertised])
        rendered = []
        for heading in headings:
            rendered.append(f'$ proofloop {heading}--help\n' + self.help_text(*heading.split()))
        self.assertEqual(snapshot, '\n'.join(rendered))

    def assert_observations(self, name):
        data = json.loads((FIXTURES / f'{name}.json').read_bytes())
        for command, observation in data['observations'].items():
            with self.subTest(command=command), tempfile.TemporaryDirectory() as folder:
                root = Path(folder) / 'project'
                restore_fixture(root, data)
                pin = root.parent / 'baseline-pin.json'
                if 'external_pin' in data:
                    pin.write_bytes(data['external_pin'].encode('utf-8'))
                args = [pin if arg == '{external_pin}' else arg for arg in observation['args']]
                result = invoke(command, '--root', root, *args)
                expected = observation
                if 'other_environment' in observation:
                    receipt = json.loads(data['files']['.proof/runs/demo/receipt.json']['text'])
                    current = {'python': platform.python_version(), 'platform': platform.platform()}
                    if receipt['environment'] != current:
                        expected = observation['other_environment']
                self.assertEqual(result.stderr, b'')
                self.assertEqual(result.stdout, expected['stdout'].encode('utf-8'))
                self.assertEqual(result.returncode, expected['exit'])

    def test_completed_agent_schema3_status(self):
        self.assert_observations('agent-schema3')

    def test_bound_harness_status(self):
        self.assert_observations('harness-bound')

    def test_legacy_unbound_harness_status(self):
        self.assert_observations('harness-legacy-unbound')

    def test_schema3_receipt_status_resume_and_validate(self):
        self.assert_observations('receipt-schema3')

    def test_missing_run_observer_json_keys(self):
        # Missing-run reports omit the optional fields for a saved state.
        common = {
            'acceptance', 'active', 'budgets', 'cancellation_requested',
            'classification', 'deadline', 'freshness', 'historical', 'phase',
            'quality', 'reason', 'run_id', 'schema_version', 'state',
            'time_remaining_seconds', 'verifier',
        }
        observers = {
            'agent-status': ('--plan', 'plan.json', common),
            'harness-status': ('--task', 'task.json', common | {
                'quality_historical', 'validation', 'verification',
            }),
        }
        with tempfile.TemporaryDirectory() as folder:
            for command, (arg, path, keys) in observers.items():
                with self.subTest(command=command):
                    result = invoke(command, '--root', folder, '--run-id', 'missing', arg, path)
                    self.assertEqual((result.returncode, result.stderr), (2, b''))
                    report = json.loads(result.stdout)
                    self.assertEqual(set(report), keys)
                    self.assertEqual(report['freshness']['state'], 'unreadable')
                    self.assertEqual(set(report['freshness']), {'state', 'issues'})
                    self.assertEqual(set(report['budgets']), {'agent_calls', 'repairs'})
