"""Read-only coordinator evidence checks for an existing bridge run."""

import contextlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from .coordinator import canonical as coordinator_canonical, digest, require
from .filesystem import clear_new_directory_acl, directory_has_acl


def validate_existing_coordinator(path, state, root, private_directory):
    """Inspect an isolated snapshot, never opening the original database with SQLite."""
    try:
        with tempfile.TemporaryDirectory(prefix='.coordinator-check-', dir=private_directory) as temporary:
            fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                clear_new_directory_acl(fd)
                os.fchmod(fd, 0o700)
                require(not directory_has_acl(fd), 'snapshot directory has an ACL')
            finally:
                os.close(fd)
            snapshot = Path(temporary) / 'coordinator.sqlite'
            wal = Path(str(path) + '-wal')
            before = (digest(path), digest(wal) if wal.exists() else None)
            def private_copy(source, destination):
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
                with source.open('rb') as source_file, os.fdopen(os.open(destination, flags, 0o600), 'wb') as target:
                    shutil.copyfileobj(source_file, target)
            private_copy(path, snapshot)
            if before[1] is not None:
                private_copy(wal, Path(str(snapshot) + '-wal'))
            require(before == (digest(path), digest(wal) if wal.exists() else None),
                    'coordinator changed during inspection')
            uri = 'file:' + quote(str(snapshot), safe='/') + '?mode=ro'
            with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
                _validate_coordinator_snapshot(db, state, root)
            return before
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError('coordinator evidence missing or invalid; inspect the prior run before continuing') from exc


def _validate_coordinator_snapshot(db, state, root):
    require(db.execute('PRAGMA quick_check').fetchone() == ('ok',), 'database integrity check failed')
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    require({'runs', 'tasks', 'operations', 'events', 'attempts'} <= tables, 'coordinator schema missing')
    records = state.get('records')
    require(isinstance(records, list), 'invalid recorded stages')
    def stage_row(run, *, completed):
        require(db.execute('SELECT 1 FROM runs WHERE id=?', (run,)).fetchone() is not None,
                'coordinator run missing')
        row = db.execute('SELECT state,data,contract FROM tasks WHERE run=? AND id=?', (run, 'work')).fetchone()
        allowed = ('succeeded',) if completed else ('reserved', 'running', 'uncertain', 'succeeded')
        require(row is not None and row[0] in allowed, 'coordinator stage missing')
        data = json.loads(row[1])
        contract = json.loads(row[2])
        require(isinstance(contract, dict) and contract.get('dependencies') == [],
                'coordinator stage dependencies changed')
        attempt = db.execute('SELECT state FROM attempts WHERE id=? AND run=? AND task=?',
                             (data['attempt'], run, 'work')).fetchone()
        require(attempt is not None and attempt[0] == row[0], 'coordinator attempt missing or inconsistent')
        events = set()
        event_keys = set()
        for operation, payload in db.execute('SELECT operation,payload FROM events WHERE run=?', (run,)):
            event = json.loads(payload)
            args = event['request']
            require(isinstance(args, dict) and args.get('run') == run, 'invalid coordinator event')
            if operation == 'submit':
                key = run + ':submit'
            else:
                require(args.get('task') == 'work' and type(args.get('version')) is int, 'invalid coordinator event version')
                key = f"{run}:{args['version']}:{operation}"
            require(key not in event_keys, 'duplicate coordinator event')
            event_keys.add(key)
            saved = db.execute('SELECT request,response FROM operations WHERE key=?', (key,)).fetchone()
            require(saved == (coordinator_canonical({'operation': operation, 'args': args}),
                              coordinator_canonical(event['result'])), 'coordinator operation differs from event')
            events.add(operation)
        required = {'submit', 'claim', 'result'} if completed else {'submit', 'claim'}
        prefix = run + ':'
        operation_keys = {r[0] for r in db.execute('SELECT key FROM operations WHERE substr(key,1,?)=?',
                                                   (len(prefix), prefix))}
        require(required <= events and (not completed or bool(events & {'dispatch', 'reconcile'})) and
                operation_keys == event_keys,
                'coordinator operation history missing')
        return data
    for record in records:
        require(isinstance(record, dict), 'invalid recorded stage')
        data = stage_row(record['coordinator_run'], completed=True)
        result = data['result']
        require(result['sha256'] == record['sha256'] and
                Path(result['artifact']).resolve() == (root / record['path']).resolve(),
                'coordinator stage evidence differs')
    active = state.get('active')
    if active:
        require(isinstance(active, dict), 'invalid active stage')
        stage_row(active['coordinator_run'], completed=False)
