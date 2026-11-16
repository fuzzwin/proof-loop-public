"""Durable local ledger. Host dispatches workers; this module never executes them."""
import argparse
import hashlib
import json
import sqlite3
from enum import Enum
from pathlib import Path


class State(str, Enum):
    QUEUED = 'queued'
    RESERVED = 'reserved'
    RUNNING = 'running'
    UNCERTAIN = 'uncertain'
    REVIEW = 'awaiting_review'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'


WORKERS = {'reserved', 'running', 'uncertain'}
CLAIMS = WORKERS | {'awaiting_review'}
TERMINAL = {'succeeded', 'failed', 'cancelled'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Coordinator:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, contract TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(run TEXT, id TEXT, contract TEXT NOT NULL,
          state TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(run,id));
        CREATE TABLE IF NOT EXISTS operations(key TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          run TEXT, task TEXT, operation TEXT, payload TEXT, recorded_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, run TEXT, task TEXT,
          ordinal INTEGER, dispatch TEXT, state TEXT, evidence TEXT);
        ''')

    def close(self):
        self.db.close()

    def snapshot(self, run):
        rows = self.db.execute('SELECT * FROM tasks WHERE run=? ORDER BY id', (run,)).fetchall()
        return [self.decode(row) for row in rows]

    @staticmethod
    def decode(row):
        d = dict(row)
        d['contract'] = json.loads(d['contract'])
        d['data'] = json.loads(d['data'])
        return d

    def apply(self, key, operation, **args):
        require(isinstance(key, str) and bool(key.strip()), 'idempotency key required')
        request = canonical({'operation': operation, 'args': args})
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT * FROM operations WHERE key=?', (key,)).fetchone()
            if old:
                require(old['request'] == request, 'idempotency key reused with different request')
                result = json.loads(old['response'])
            else:
                result = self._apply(operation, args)
                self.db.execute('INSERT INTO operations VALUES(?,?,?)', (key, request, canonical(result)))
                self.db.execute('INSERT INTO events(run,task,operation,payload) VALUES(?,?,?,?)',
                                (args.get('run'), args.get('task'), operation,
                                 canonical({'request': args, 'result': result})))
            self.db.execute('COMMIT')
            return result
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _apply(self, op, a):
        if op == 'submit':
            return self.submit(a['run'], a['tasks'])
        row = self.db.execute('SELECT * FROM tasks WHERE run=? AND id=?', (a['run'], a['task'])).fetchone()
        require(row is not None, 'unknown task')
        t = self.decode(row)
        require(type(a.get('version')) is int and a['version'] == t['version'], 'version conflict')
        s, c, d = t['state'], t['contract'], t['data']
        target = s
        if op == 'claim':
            require(s == 'queued', 'only queued work can be claimed')
            self.verify_dependencies(a['run'], c['dependencies'])
            rows = self.db.execute('SELECT state,contract FROM tasks').fetchall()
            require(sum(r['state'] in WORKERS for r in rows) < 3, 'worker limit')
            held = {res for r in rows if r['state'] in CLAIMS for res in json.loads(r['contract'])['resources']}
            require(not held.intersection(c['resources']), 'resource collision')
            ordinal = self.db.execute('SELECT COUNT(*) FROM attempts WHERE run=? AND task=?', (a['run'], a['task'])).fetchone()[0] + 1
            attempt = hashlib.sha256(canonical([a['run'], a['task'], ordinal]).encode()).hexdigest()
            self.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)', (attempt, a['run'], a['task'], ordinal, None, 'reserved', None))
            d = {'attempt': attempt, 'cancel_requested': False}
            target = 'reserved'
        elif op == 'dispatch':
            require(s == 'reserved', 'dispatch requires reservation')
            require(bool(a.get('dispatch_ref', '').strip()), 'actual dispatch reference required')
            d['dispatch_ref'] = a['dispatch_ref']
            self.db.execute('UPDATE attempts SET dispatch=? WHERE id=?', (a['dispatch_ref'], d['attempt']))
            target = 'running'
        elif op == 'recover':
            require(s in WORKERS, 'recovery requires potentially active work')
            target = 'uncertain'
        elif op == 'reconcile':
            require(s == 'uncertain', 'reconciliation requires uncertain state')
            require(bool(a.get('evidence_ref', '').strip()), 'reconciliation evidence required')
            outcome = a.get('outcome')
            require(outcome in {'running', 'not_executed', 'failed', 'cancelled'}, 'invalid reconciliation outcome')
            if outcome == 'running':
                require(bool(a.get('dispatch_ref', '').strip()), 'actual dispatch reference required')
                d['dispatch_ref'] = a['dispatch_ref']
                self.db.execute('UPDATE attempts SET dispatch=? WHERE id=?', (a['dispatch_ref'], d['attempt']))
                target = 'running'
            elif outcome == 'not_executed':
                target = 'cancelled' if d.get('cancel_requested') else 'queued'
            else:
                target = outcome
            self.db.execute('UPDATE attempts SET evidence=? WHERE id=?', (a['evidence_ref'], d['attempt']))
        elif op == 'result':
            require(s == 'running' and not d.get('cancel_requested'), 'result requires uncancelled running work')
            self.verify_dependencies(a['run'], c['dependencies'])
            require(bool(a.get('evidence_ref', '').strip()), 'result evidence required')
            require(bool(a.get('outcome_ref', '').strip()), 'actual outcome reference required')
            path = str(Path(a['artifact']).resolve())
            require(digest(path) == a['sha256'], 'artifact hash mismatch')
            d['result'] = {'artifact': path, 'sha256': a['sha256'], 'evidence_ref': a['evidence_ref'], 'outcome_ref': a['outcome_ref']}
            target = 'awaiting_review' if c['reviewer'] else 'succeeded'
        elif op == 'accept':
            require(s == 'awaiting_review', 'accept requires pending review')
            require(a.get('reviewer') == c['reviewer'] and bool(a.get('review_ref', '').strip()), 'reviewer acceptance required')
            require(a.get('sha256') == d['result']['sha256'] == digest(d['result']['artifact']), 'artifact changed or wrong review hash')
            self.verify_dependencies(a['run'], c['dependencies'])
            d['acceptance'] = {'reviewer': a['reviewer'], 'review_ref': a['review_ref'], 'sha256': a['sha256']}
            target = 'succeeded'
        elif op == 'cancel':
            require(s not in TERMINAL, 'terminal task cannot be cancelled')
            target = 'uncertain' if s in WORKERS else 'cancelled'
            d['cancel_requested'] = True
        elif op == 'block':
            require(s == 'queued', 'only queued tasks can be blocked')
            require(bool(a.get('reason', '').strip()), 'block reason required')
            d['block_reason'] = a['reason']
            target = 'blocked'
        else:
            raise ValueError('unknown operation')
        State(target)
        self.db.execute('UPDATE tasks SET state=?,version=version+1,data=? WHERE run=? AND id=?',
                        (target, canonical(d), a['run'], a['task']))
        if d.get('attempt'):
            self.db.execute('UPDATE attempts SET state=? WHERE id=?', (target, d['attempt']))
        return self.decode(self.db.execute('SELECT * FROM tasks WHERE run=? AND id=?', (a['run'], a['task'])).fetchone())

    def verify_dependencies(self, run, dependencies):
        """Recheck all ancestors, not merely cached successful states.

        A success records historical acceptance; it is not immutable file storage.
        This guard deliberately fails closed on absent/unreadable/changed bytes.
        """
        checked = set()
        def verify(task_id):
            if task_id in checked:
                return
            row = self.db.execute('SELECT * FROM tasks WHERE run=? AND id=?', (run, task_id)).fetchone()
            require(row is not None and row['state'] == 'succeeded', 'dependencies not succeeded')
            parent = self.decode(row)
            result = parent['data'].get('result')
            require(result is not None, 'dependency artifact result missing: ' + task_id)
            try:
                current_hash = digest(result['artifact'])
            except OSError as exc:
                raise ValueError('dependency artifact unreadable: ' + task_id) from exc
            require(current_hash == result['sha256'], 'dependency artifact changed: ' + task_id)
            for ancestor in parent['contract']['dependencies']:
                verify(ancestor)
            checked.add(task_id)
        for dependency in dependencies:
            verify(dependency)

    def submit(self, run, tasks):
        require(isinstance(run, str) and bool(run.strip()), 'run ID required')
        require(isinstance(tasks, list) and bool(tasks), 'tasks required')
        for t in tasks:
            require(set(t) == {'id', 'description', 'dependencies', 'resources', 'reviewer'}, 'contract fields invalid')
            require(all(isinstance(t[k], str) and t[k].strip() for k in ('id', 'description')), 'task identity and description required')
            require(t['reviewer'] is None or isinstance(t['reviewer'], str) and bool(t['reviewer'].strip()), 'invalid reviewer')
            for field in ('dependencies', 'resources'):
                require(isinstance(t[field], list) and all(isinstance(x, str) and x.strip() for x in t[field]) and len(t[field]) == len(set(t[field])), 'invalid dependency/resource list')
        by_id = {t['id']: t for t in tasks}
        require(len(by_id) == len(tasks), 'duplicate task ID')
        visited, visiting = set(), set()
        def visit(tid):
            require(tid in by_id, 'missing dependency')
            require(tid not in visiting, 'dependency cycle')
            if tid in visited:
                return
            visiting.add(tid)
            for dep in by_id[tid]['dependencies']:
                visit(dep)
            visiting.remove(tid)
            visited.add(tid)
        for tid in by_id:
            visit(tid)
        contract = canonical(sorted(tasks, key=lambda t: t['id']))
        old = self.db.execute('SELECT contract FROM runs WHERE id=?', (run,)).fetchone()
        if old:
            require(old['contract'] == contract, 'frozen run contract mismatch')
        else:
            self.db.execute('INSERT INTO runs VALUES(?,?)', (run, contract))
            for t in tasks:
                self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)', (run, t['id'], canonical(t), 'queued', 0, '{}'))
        return self.snapshot(run)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', required=True)
    sub = p.add_subparsers(dest='command', required=True)
    apply = sub.add_parser('apply', help='JSON object: key, operation, args')
    apply.add_argument('request', help='JSON file path')
    show = sub.add_parser('show')
    show.add_argument('run')
    ns = p.parse_args()
    db = Coordinator(ns.db)
    try:
        if ns.command == 'show':
            result = db.snapshot(ns.run)
        else:
            r = json.loads(Path(ns.request).read_text())
            result = db.apply(r['key'], r['operation'], **r['args'])
        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == '__main__':
    main()
