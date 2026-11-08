"""Release metadata must name the version the CLI enforces.

Runner logs and receipts are rejected unless their version equals
proofloop.VERSION, so a schema, template or guide naming another version
misleads anyone producing or checking evidence by hand.
"""
import json
from pathlib import Path
import re
import unittest

import proofloop as core

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = r'[0-9]+\.[0-9]+\.[0-9]+'

# Each pattern marks a statement about the current CLI, not a historical note.
CURRENT_VERSION_STATEMENTS = {
    'HOW-TO.md': [rf'^# How to use Proof Loop ({VERSION_RE})$',
                  rf'^\*\*Version ({VERSION_RE}) source:\*\*',
                  rf'^Version ({VERSION_RE}) can currently validate'],
    'HOW-TO-USE.md': [rf'uses the ({VERSION_RE}) CLI source'],
    'RECEIPT-SCHEMA.md': [rf'`runner_version: ({VERSION_RE})` (?:are|is) required'],
    'templates/codex/AGENTS.md': [rf'In Proof Loop ({VERSION_RE}),'],
    'templates/gpt/PROOF-LOOP-GPT-HANDOFF.md': [rf'^Proof Loop ({VERSION_RE}) accepts Verified'],
}


class ReleaseConsistencyTests(unittest.TestCase):
    def test_runner_log_schema_requires_the_enforced_runner_version(self):
        schema = json.loads((ROOT / 'schemas/drift-runner-log.schema.json').read_text())
        self.assertEqual(core.VERSION, schema['properties']['runner_version']['const'])

    def test_current_version_statements_match_the_cli(self):
        for relative, patterns in CURRENT_VERSION_STATEMENTS.items():
            text = (ROOT / relative).read_text()
            for pattern in patterns:
                with self.subTest(file=relative, pattern=pattern):
                    found = re.findall(pattern, text, re.MULTILINE)
                    self.assertTrue(found, f'{relative} no longer states the current version')
                    self.assertEqual({core.VERSION}, set(found))

    def test_latest_changelog_release_is_the_cli_version(self):
        text = (ROOT / 'CHANGELOG.md').read_text()
        releases = re.findall(rf'^## ({VERSION_RE})\b', text, re.MULTILINE)
        self.assertTrue(releases, 'CHANGELOG.md has no versioned release heading')
        self.assertEqual(core.VERSION, releases[0])


if __name__ == '__main__':
    unittest.main()
