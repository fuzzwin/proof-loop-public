"""Regression coverage for fresh candidates without resetting acceptance or repairs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / 'proofloop'

class RecheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        self.runcli('init', self.root)
        (self.root / 'source.json').write_text('{"items":[{"id":"A"}]}')
        (self.root / 'view.json').write_text('{}')
        (self.root / 'criteria.json').write_text('{"count":1}')
        self.pin = Path(self.tmp.name) / 'pin.json'
    def runcli(self, *argv, expected=0):
        p = subprocess.run([sys.executable, '-B', str(CLI), *map(str, argv)], text=True, capture_output=True)
        self.assertEqual(p.returncode, expected, p.stdout + p.stderr)
        return p
    def start(self, accepted=False):
        mode = ['--advisory']
        if accepted:
            data = (self.root / 'criteria.json').read_bytes()
            self.pin.write_text(json.dumps({'schema_version':1,'acceptance_tests':{'criteria.json':{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}}}))
            mode = ['--baseline-pin', self.pin]
        self.runcli('start', '--root', self.root, '--run-id', 'old', '--goal', 'correct view', '--scope', 'view only', '--check', 'source-view-drift', '--input', 'source.json', '--input', 'view.json', '--acceptance-test', 'criteria.json', '--done', 'projection matches', *mode)
    def recheck(self, **kwargs):
        return self.runcli('recheck', '--root', self.root, '--run-id', 'old', '--new-run-id', 'new', **kwargs)
    def path(self, run, name):
        return self.root / '.proof/runs' / run / name
    def test_repair_new_candidate_preserves_old_evidence_and_budget(self):
        self.start()
        self.runcli('drift','--root',self.root,'--run-id','old','--source','source.json','--view','view.json','--log','.proof/runs/old/fail.json', expected=1)
        self.runcli('repair','--root',self.root,'--run-id','old','--reason','fix view')
        before={p.name:p.read_bytes() for p in self.path('old','').iterdir() if p.is_file()}
        (self.root/'view.json').write_text('{"record_count":1,"ids":["A"],"fixture_classification":"synthetic"}')
        self.recheck()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.path('old','').iterdir() if p.is_file()})
        receipt=json.loads(self.path('new','receipt.json').read_text())
        self.assertEqual(receipt['repair_rounds_used'],1)
        self.assertEqual(receipt['checks'],{})
        self.assertEqual(json.loads(self.runcli('status','--root',self.root,'--run-id','new').stdout)['input_fresh'],True)
        self.runcli('drift','--root',self.root,'--run-id','new','--source','source.json','--view','view.json','--log','.proof/runs/new/pass.json')
        self.runcli('repair','--root',self.root,'--run-id','new','--reason','second')
        self.runcli('repair','--root',self.root,'--run-id','new','--reason','third',expected=3)
        self.recheck(expected=2)
        self.runcli('repair','--root',self.root,'--run-id','old','--reason','branch',expected=2)
    def test_changed_criteria_rejected_without_child(self):
        self.start(); (self.root/'criteria.json').write_text('{}')
        self.recheck(expected=2)
        self.assertFalse(self.path('new','').exists())
    def test_changed_card_and_manifest_rejected(self):
        self.start()
        p=self.path('old','run-card.json'); old=p.read_bytes()
        card=json.loads(old);card['scope']='everything';p.write_text(json.dumps(card))
        self.recheck(expected=2);p.write_bytes(old)
        p=self.path('old','input-manifest.json');p.write_text('{}')
        self.recheck(expected=2)
    def test_unsafe_duplicate_and_cancelled_rejected(self):
        self.start()
        self.runcli('recheck','--root',self.root,'--run-id','old','--new-run-id','../escape',expected=2)
        self.runcli('recheck','--root',self.root,'--run-id','old','--new-run-id','old',expected=2)
        self.runcli('close','--root',self.root,'--run-id','old','--state','cancelled','--reason','stop')
        self.recheck(expected=2)
    def test_reduced_budget_rejected(self):
        self.start();self.runcli('repair','--root',self.root,'--run-id','old','--reason','fix')
        p=self.path('old','receipt.json');r=json.loads(p.read_text());r['repair_rounds_used']=0;p.write_text(json.dumps(r))
        self.recheck(expected=2)
    def test_acceptance_pin_requires_parent_approval_and_new_approval(self):
        self.start(accepted=True)
        self.recheck(expected=2)
        self.runcli('recheck','--root',self.root,'--run-id','old','--new-run-id','new','--baseline-pin',self.pin,expected=2)
        pin=json.loads(self.pin.read_text());parent_hash=hashlib.sha256(self.path('old','run-card.json').read_bytes()).hexdigest()
        pin['approved_run_card_sha256']=parent_hash;self.pin.write_text(json.dumps(pin))
        self.runcli('recheck','--root',self.root,'--run-id','old','--new-run-id','new','--baseline-pin',self.pin)
        self.assertEqual(json.loads(self.pin.read_text())['approved_run_card_sha256'],parent_hash)
        result=self.runcli('validate','--root',self.root,'--run-id','new','--baseline-pin',self.pin,expected=2)
        self.assertIn('Run Card changed after external reviewer approval',result.stdout)
        new=json.loads(self.path('new','run-card.json').read_text());old=json.loads(self.path('old','run-card.json').read_text())
        for key in ['goal','scope','risk','required_checks','done_when','capabilities_selected','assumptions','acceptance_mode']:
            self.assertEqual(new[key],old[key])
    def test_malformed_repair_ledger_fails_cleanly(self):
        self.start()
        self.runcli('repair','--root',self.root,'--run-id','old','--reason','fix')
        p=self.root/'.proof/ledger.jsonl'
        rows=[json.loads(line) for line in p.read_text().splitlines()]
        for row in rows:
            if row.get('event') == 'repair_started':
                row['round']='invalid'
        p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        result=self.recheck(expected=2)
        self.assertIn('repair ledger is malformed',result.stderr)
        self.assertNotIn('Traceback',result.stderr)

    def test_symlink_criteria_rejected(self):
        self.start(); p=self.root/'criteria.json';content=p.read_bytes();p.unlink();other=Path(self.tmp.name)/'elsewhere';other.write_bytes(content);p.symlink_to(other)
        self.recheck(expected=2)

if __name__ == '__main__':
    unittest.main()
