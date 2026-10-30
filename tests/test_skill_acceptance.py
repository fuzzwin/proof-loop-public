"""Case-packet integrity tests; synthetic approval is not separate custody."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.skill_acceptance import digest, evaluate


class SkillAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        for name in ('SKILL.md', 'requirements.md', 'trace.txt'):
            (self.root / name).write_text('synthetic ' + name)
        def bound(name):
            return {'path': name, 'sha256': digest((self.root / name).read_bytes())}
        self.contract = {'schema_version': 1, 'skill': bound('SKILL.md'),
                         'requirements': [bound('requirements.md')],
                         'cases': [{'id': 'readonly', 'prompt': 'Read without changes',
                                    'criteria': ['Project is unchanged']}]}
        self.write('contract.json', self.contract)
        self.contract_hash = digest((self.root / 'contract.json').read_bytes())
        self.observations = {'schema_version': 1, 'contract_sha256': self.contract_hash,
                             'candidate_sha256': self.contract['skill']['sha256'],
                             'cases': [{'id': 'readonly', 'outcome': 'pass',
                                        'artifacts': [bound('trace.txt')],
                                        'notes': 'Synthetic observation fixture'}]}
        self.write('observations.json', self.observations)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def evaluate(self, approval=None):
        return evaluate(self.root, 'contract.json', 'observations.json',
                        self.contract_hash, approval)

    def approval(self, packet):
        value = {'schema_version': 1, 'packet_sha256': packet,
                 'reviewer': 'synthetic reviewer', 'decision': 'approve',
                 'scope': 'declared_cases'}
        path = self.base / 'approval.json'
        path.write_text(json.dumps(value))
        return path

    def test_ready_is_not_accepted_and_no_changes(self):
        before = {str(p): p.read_bytes() for p in self.root.iterdir()}
        result = self.evaluate()
        self.assertEqual('ready_for_review', result['state'])
        self.assertEqual('not_accepted', result['portable_acceptance'])
        self.assertEqual('unverified', result['custody'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.iterdir()})

    def test_end_to_end_cli_approval_and_tampering(self):
        command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] /
                   'tools/skill_acceptance.py'), '--root', str(self.root),
                   '--contract', 'contract.json', '--observations', 'observations.json',
                   '--contract-sha256', self.contract_hash]
        ready = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, ready.returncode, ready.stderr)
        path = self.approval(json.loads(ready.stdout)['packet_sha256'])
        approved = subprocess.run(command + ['--reviewer-approval', str(path)],
                                  capture_output=True, text=True)
        self.assertEqual(0, approved.returncode, approved.stderr)
        self.assertEqual('reviewer_approved_for_cases', json.loads(approved.stdout)['state'])
        self.assertEqual('not_accepted', json.loads(approved.stdout)['portable_acceptance'])
        # A semantically equivalent but byte-different observation still invalidates approval.
        with (self.root / 'observations.json').open('a') as stream:
            stream.write('\n')
        self.assertEqual('needs_evidence', self.evaluate(path)['state'])

    def test_changed_candidate_requirement_artifact_or_contract(self):
        for name in ('SKILL.md', 'requirements.md', 'trace.txt', 'contract.json'):
            with self.subTest(name=name):
                path = self.root / name
                before = path.read_bytes()
                path.write_bytes(before + b'changed')
                self.assertEqual('needs_evidence', self.evaluate()['state'])
                path.write_bytes(before)

    def test_incomplete_failed_duplicate_unknown_or_no_artifacts(self):
        for cases in ([], [dict(self.observations['cases'][0], outcome='fail')],
                      self.observations['cases'] * 2,
                      [dict(self.observations['cases'][0], id='different')],
                      [dict(self.observations['cases'][0], artifacts=[])]):
            with self.subTest(cases=cases):
                changed = dict(self.observations, cases=cases)
                self.write('observations.json', changed)
                self.assertEqual('needs_evidence', self.evaluate()['state'])

    def test_malformed_json_duplicate_keys_and_boolean_version(self):
        for raw in ('{', '{"schema_version":1,"schema_version":1}', 'NaN', '[]'):
            (self.root / 'observations.json').write_text(raw)
            self.assertEqual('needs_evidence', self.evaluate()['state'])
        self.write('observations.json', dict(self.observations, schema_version=True))
        self.assertEqual('needs_evidence', self.evaluate()['state'])

    def test_unsafe_and_symlink_artifact_paths(self):
        outside = self.base / 'outside'
        outside.write_text('outside')
        (self.root / 'link').symlink_to(outside)
        for name in ('../outside', str(outside), 'link'):
            changed = copy.deepcopy(self.observations)
            changed['cases'][0]['artifacts'][0]['path'] = name
            self.write('observations.json', changed)
            self.assertEqual('needs_evidence', self.evaluate()['state'])

    def test_approval_must_be_external_regular_and_candidate_bound(self):
        packet = self.evaluate()['packet_sha256']
        path = self.approval(packet)
        inside = self.root / 'approval.json'
        inside.write_bytes(path.read_bytes())
        self.assertEqual('needs_evidence', self.evaluate(inside)['state'])
        link = self.base / 'link'
        link.symlink_to(path)
        self.assertEqual('needs_evidence', self.evaluate(link)['state'])
        value = json.loads(path.read_text())
        for key, wrong in [('decision', 'reject'), ('packet_sha256', '0' * 64),
                           ('reviewer', ''), ('scope', 'all_future_tasks')]:
            path.write_text(json.dumps(dict(value, **{key: wrong})))
            self.assertEqual('needs_evidence', self.evaluate(path)['state'])


if __name__ == '__main__':
    unittest.main()
