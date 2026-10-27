#!/usr/bin/env python3
"""Exercise typed software/data/document checks without models or credentials."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
FIXES = {
    'software': {'app.py': 'def add(left, right):\n    return left + right\n'},
    'data': {'report.json': '{"schema_version":1,"count":2,"ids":["a","b"]}\n'},
    'document': {'report.md': '# Release checklist\n\nScope: local files\nEvidence: recorded checks\n'},
}


def main():
    with tempfile.TemporaryDirectory(prefix='proof-loop-harness-') as folder:
        for name, files in FIXES.items():
            root = Path(folder) / name
            shutil.copytree(SOURCE / 'examples/harness' / name, root,
                            ignore=shutil.ignore_patterns('.proof', '__pycache__'))
            def run(command, run_id, expected):
                result = subprocess.run([sys.executable, '-B', str(SOURCE / 'proofloop'), command,
                                         '--root', str(root), '--run-id', run_id, '--task', 'task.json'],
                                        capture_output=True, text=True)
                if result.returncode != expected:
                    raise RuntimeError(result.stdout + result.stderr)
                return json.loads(result.stdout)
            failed = run('harness-check', 'failing', 2)
            assert failed['agent_calls'] == 0
            for path, content in files.items():
                (root / path).write_text(content)
            passed = run('harness-check', 'fixed', 0)
            assert passed['verification']['state'] == passed['validation']['state'] == 'pass'
            assert passed['agent_calls'] == 0 and passed['acceptance'] == 'not_accepted'
            status = run('harness-status', 'fixed', 0)
            assert status['freshness']['state'] == 'current'
            print('PASS:', name, 'failure control, corrected criteria and read-only status')
    print('Synthetic direct edits exercised checks only; no agent or live model was dispatched.')


if __name__ == '__main__':
    main()
