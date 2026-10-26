#!/usr/bin/env python3
"""Disposable stdlib smoke: fresh state, matching and divergent synthetic evidence."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='proof-loop-smoke-') as directory:
        project = Path(directory) / 'project'
        shutil.copytree(SOURCE / 'examples/synthetic-project', project,
                        ignore=shutil.ignore_patterns('.proof', '__pycache__'))
        assert not (project / '.proof').exists(), 'fixture must start without runtime state'

        def run(*args, expected=0):
            result = subprocess.run([sys.executable, '-B', str(SOURCE / 'proofloop'),
                                     *map(str, args)], cwd=directory, capture_output=True, text=True)
            if result.returncode != expected:
                raise RuntimeError(f'{args[0]}: expected exit {expected}, got {result.returncode}\n'
                                   f'{result.stdout}\n{result.stderr}')
            return result

        run('init', project)
        run('validate-registry', '--root', project)
        run('start', '--root', project, '--run-id', 'fresh-start', '--advisory',
            '--risk', 'low', '--playbook', 'build', '--goal', 'Compare synthetic JSON',
            '--scope', 'Synthetic files and local evidence only',
            '--use-capability', 'python-stdlib', '--check', 'source-view-drift',
            '--acceptance-test', 'fixtures/acceptance_contract.json',
            '--input', 'fixtures/records.json', '--input', 'reports/result.json',
            '--done', 'Count and ordered IDs match')
        run('drift', '--root', project, '--run-id', 'fresh-start',
            '--source', 'fixtures/records.json', '--view', 'reports/result.json',
            '--log', '.proof/runs/fresh-start/logs/matching.json')
        run('status', '--root', project, '--run-id', 'fresh-start')
        run('resume', '--root', project, '--run-id', 'fresh-start')
        # A real mismatch must fail; a passing advisory check must not become acceptance.
        view = project / 'reports/result.json'
        value = json.loads(view.read_text())
        value['record_count'] = 999
        view.write_text(json.dumps(value))
        run('drift', '--root', project, '--run-id', 'fresh-start',
            '--source', 'fixtures/records.json', '--view', 'reports/result.json',
            '--log', '.proof/runs/fresh-start/logs/divergent.json', expected=1)
        run('validate', '--root', project, '--run-id', 'fresh-start', expected=2)
    print('PASS: fresh initialization, registry, matching drift, status/resume, '
          'divergence failure, and blocked advisory acceptance; temporary data removed.')


if __name__ == '__main__':
    main()
