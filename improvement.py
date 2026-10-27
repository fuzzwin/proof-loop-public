"""Proposal-only learning gate. No installation, promotion or permission mutation.

A host supplies a registered evaluator callable. Results are attributed to that
callable and byte-pinned inputs, not accepted as arbitrary truth by Proof Loop.
A holdout is consumed before evaluation, including when the evaluator crashes.
"""
import hashlib
import inspect
import json
import sqlite3
from pathlib import Path

from coordinator import canonical, digest, require


def stable_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ImprovementGate:
    def __init__(self, database):
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, contract TEXT NOT NULL,
          state TEXT NOT NULL, evidence TEXT);
        CREATE TABLE IF NOT EXISTS holdouts(hash TEXT PRIMARY KEY, proposal TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS holdout_cases(id TEXT PRIMARY KEY, proposal TEXT);
        ''')

    def close(self):
        self.db.close()

    @staticmethod
    def cases(path):
        cases = json.loads(Path(path).read_text())
        require(isinstance(cases, list) and bool(cases), 'nonempty dataset required')
        ids = [x.get('case_id') for x in cases if isinstance(x, dict)]
        require(len(ids) == len(cases) and all(isinstance(x, str) and x for x in ids) and len(set(ids)) == len(ids), 'unique case IDs required')
        return set(ids)

    def propose(self, identity, *, proposer, hypothesis, base, candidate, evaluator,
                regression, holdout, protected_files, change_kind, unknowns):
        require(all(isinstance(x, str) and x.strip() for x in (identity, proposer, hypothesis)), 'identity/proposer/hypothesis required')
        require(change_kind in ('prompt', 'strategy', 'adapter'), 'permission, policy, acceptance and gate changes are prohibited')
        require(isinstance(unknowns, list) and all(isinstance(x, str) and x for x in unknowns), 'unknowns must be explicit')
        require(bool(protected_files), 'protected acceptance/control files required')
        paths = dict(base=str(Path(base).resolve()), candidate=str(Path(candidate).resolve()),
                     evaluator=str(Path(inspect.getsourcefile(evaluator)).resolve()),
                     regression=str(Path(regression).resolve()), holdout=str(Path(holdout).resolve()))
        require(len(set(paths.values())) == 5, 'candidate/base/evaluator/datasets must be distinct files')
        require(not self.cases(regression) & self.cases(holdout), 'regression and holdout cases overlap')
        hashes = {k: digest(v) for k, v in paths.items()}
        require(hashes['regression'] != hashes['holdout'], 'holdout must be distinct')
        controls = {str(Path(p).resolve()): digest(p) for p in protected_files}
        require(paths['candidate'] not in controls, 'candidate cannot be a protected gate file')
        contract = dict(proposer=proposer, hypothesis=hypothesis, paths=paths, hashes=hashes,
                        evaluator_name=evaluator.__qualname__, protected=controls,
                        change_kind=change_kind, unknowns=unknowns, evaluation_budget=1)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO proposals VALUES(?,?,?,NULL)', (identity, canonical(contract), 'queued'))
            # Regression is inspected selection data, never a future untouched holdout.
            self.db.execute('INSERT OR IGNORE INTO holdouts VALUES(?,?,?)',
                            (hashes['regression'], identity, 'exposed:regression'))
            for case in self.cases(regression):
                self.db.execute('INSERT OR IGNORE INTO holdout_cases VALUES(?,?)', (case, identity + ':regression'))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return stable_hash(contract)

    def row(self, identity):
        row = self.db.execute('SELECT * FROM proposals WHERE id=?', (identity,)).fetchone()
        require(row is not None, 'unknown proposal')
        result = dict(row)
        result['contract'] = json.loads(result['contract'])
        result['evidence'] = json.loads(result['evidence']) if result['evidence'] else None
        return result

    @staticmethod
    def fresh(contract):
        for name, path in contract['paths'].items():
            require(digest(path) == contract['hashes'][name], name + ' changed')
        for path, expected in contract['protected'].items():
            require(digest(path) == expected, 'protected control changed')

    def expose_holdout(self, path, reason):
        require(isinstance(reason, str) and reason.strip(), 'exposure reason required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO holdouts VALUES(?,?,?)', (digest(path), 'external', 'exposed:' + reason))
            for case in self.cases(path):
                self.db.execute('INSERT OR IGNORE INTO holdout_cases VALUES(?,?)', (case, 'external'))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def evaluate(self, identity, evaluator):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.row(identity)
            require(row['state'] == 'queued', 'evaluation already attempted; no retry/tuning on this holdout')
            c = row['contract']
            self.fresh(c)
            require(not c['unknowns'], 'unresolved unknowns prevent evaluation')
            require(str(Path(inspect.getsourcefile(evaluator)).resolve()) == c['paths']['evaluator'] and evaluator.__qualname__ == c['evaluator_name'], 'evaluator identity mismatch')
            require(self.db.execute('SELECT 1 FROM holdouts WHERE hash=?', (c['hashes']['holdout'],)).fetchone() is None, 'holdout already exposed or consumed')
            for case in self.cases(c['paths']['holdout']):
                require(self.db.execute('SELECT 1 FROM holdout_cases WHERE id=?', (case,)).fetchone() is None, 'holdout case already exposed or consumed')
            # Reserve before any evaluator call; failure/interruption burns the holdout.
            for case in self.cases(c['paths']['holdout']):
                self.db.execute('INSERT INTO holdout_cases VALUES(?,?)', (case, identity))
            self.db.execute('INSERT INTO holdouts VALUES(?,?,?)', (c['hashes']['holdout'], identity, 'consumed'))
            self.db.execute('UPDATE proposals SET state=? WHERE id=?', ('evaluating', identity))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        try:
            reports = {}
            for dataset in ('regression', 'holdout'):
                self.fresh(c)
                report = evaluator(Path(c['paths']['base']), Path(c['paths']['candidate']), Path(c['paths'][dataset]))
                require(isinstance(report, dict) and set(report) == {'base_passed', 'candidate_passed'} and
                        all(type(v) is bool for v in report.values()), 'evaluator must return exact boolean outcomes')
                reports[dataset] = report
                self.fresh(c)
                if not report['candidate_passed']:
                    break
            improved = ('holdout' in reports and reports['holdout']['candidate_passed'] and
                        all(r['candidate_passed'] for r in reports.values()) and
                        any(not r['base_passed'] for r in reports.values()))
            evidence = {'contract_sha256': stable_hash(c), 'reports': reports,
                        'classification': 'evaluator_reported_not_portable_verified', 'promoted': False}
            self.db.execute('UPDATE proposals SET state=?,evidence=? WHERE id=?',
                            ('awaiting_review' if improved else 'rejected', canonical(evidence), identity))
        except BaseException as exc:
            self.db.execute('UPDATE proposals SET state=?,evidence=? WHERE id=?',
                            ('blocked', canonical({'error': type(exc).__name__, 'promoted': False}), identity))
            raise
        return self.row(identity)

    def review(self, identity, *, reviewer, evidence_sha256):
        row = self.row(identity)
        require(row['state'] == 'awaiting_review', 'no passing proposal awaiting review')
        require(isinstance(reviewer, str) and reviewer.strip() and reviewer != row['contract']['proposer'], 'distinct reviewer attribution required')
        self.fresh(row['contract'])
        require(evidence_sha256 == stable_hash(row['evidence']), 'review evidence changed')
        # Pure decision, deliberately no promotion/activation API. Names are not authentication.
        return {'proposal': identity, 'state': 'eligible_for_human_decision', 'promoted': False,
                'reviewer': reviewer, 'identity_authenticated': False,
                'holdout_custody': 'trusted_host_attestation_only_not_OS_sealed',
                'contract_sha256': stable_hash(row['contract']), 'evidence_sha256': evidence_sha256}
