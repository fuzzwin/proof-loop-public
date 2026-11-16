"""Verified diff delivery, journaled promotion, and deterministic recovery."""

import copy
import difflib
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from . import core
from .coordinator import digest, require
from .filesystem import (
    directory_modes,
    file_map,
    file_modes,
    file_path,
    fingerprint,
    prepare_directories,
    set_directory_modes,
    tree_map,
)


class DeliveryMixin:
    """Deliver the checked candidate or recover its recorded promotion."""

    def deliver_diff(self, directory, launched):
        self.guard()
        self.fresh()
        require(self.state['quality']['passed'] and self.state.get('final_verifier_thread') and
                self.state['final_candidate_sha256'] == fingerprint(self.state['expected'], self.state['candidate_modes'], self.state['directory_modes']),
                'diff requires checks and verifier for exact candidate')
        chunks = []
        changed = sorted(n for n in self.plan['files'] if self.state['initial'][n] != self.state['expected'][n])
        for name in changed:
            before = file_path(self.root, name).read_bytes() if self.state['initial'][name] is not None else b''
            after = file_path(self.candidate_root, name).read_bytes()
            # Text proposals only. Reject binary/ambiguous patches rather than hiding bytes.
            require(b'\x00' not in before + after, 'binary diff is unsupported')
            # Git patch lines are delimited only by LF, not by Unicode line separators or bare CR.
            def lf_lines(data):
                parts = data.decode('utf-8').split('\n')
                return [line + '\n' for line in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
            old = lf_lines(before)
            new = lf_lines(after)
            lines = difflib.unified_diff(old, new, fromfile=json.dumps('a/' + name, ensure_ascii=False) if self.state['initial'][name] else '/dev/null',
                                         tofile=json.dumps('b/' + name, ensure_ascii=False))
            chunks.append('diff --git ' + json.dumps('a/' + name, ensure_ascii=False) + ' ' + json.dumps('b/' + name, ensure_ascii=False) + '\n')
            if self.state['initial'][name] is None:
                chunks.append('new file mode 100644\n')
            for line in lines:
                chunks.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
        path = directory / 'verified.patch'
        core.atomic_write(path, ''.join(chunks).encode('utf-8'), exclusive=True)
        apply_umask = None
        if changed:
            git = shutil.which('git')
            if not git:
                return {'blocked_reason': 'Git is required to verify an applicable patch; install Git before starting a new run.'}
            modes = self.state['candidate_modes']
            def matches(mask):
                return all(((0o777 if modes[name] & 0o100 else 0o666) & ~mask) == modes[name] for name in changed)
            mask = next((n for n in [0o022, 0o077, *range(0o1000)] if matches(n)), None)
            if mask is None:
                return {'blocked_reason': 'No single Git apply umask reproduces the verified file permissions; split the change or use the reviewed apply workflow.'}
            apply_umask = f'{mask:03o}'
            check = directory / 'patch-check'
            check.mkdir()
            workspace = check / 'workspace'
            workspace.mkdir()
            prepare_directories(workspace, self.state['directory_modes'])
            for name, value in self.state['initial'].items():
                if value is not None:
                    dest = workspace / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(file_path(self.root, name), dest)
            set_directory_modes(workspace, self.state['directory_modes'])
            run = self.runner.run([git, 'apply', '--', str(path)], cwd=workspace, directory=check,
                                  timeout=self.remaining(self.plan['check_timeout_seconds']),
                                  cancelled=self.cancelled, launched=launched, umask=mask, isolate_git=True)
            self.guard()
            if run['exit_code'] != 0:
                return {'blocked_reason': 'Generated patch did not apply in a disposable copy; inspect patch-check stderr.'}
            expected_tree = {n: value for n, value in self.state['expected'].items() if value is not None}
            if (tree_map(workspace) != expected_tree or file_modes(workspace, self.plan['files']) != modes or
                    directory_modes(workspace, self.plan['files']) != self.state['directory_modes']):
                return {'blocked_reason': 'Applied patch differs from verified candidate content, paths or permissions.'}
        self.fresh()
        return {'path': str(path.relative_to(self.root)), 'sha256': digest(path),
                'candidate_sha256': self.state['final_candidate_sha256'], 'files': changed,
                'apply_umask': apply_umask}

    def promote(self, directory, launched):
        self.guard()
        self.fresh()
        require(self.state['quality']['passed'] and self.state.get('final_verifier_thread') and
                self.state['final_candidate_sha256'] == fingerprint(self.state['expected'], self.state['candidate_modes'], self.state['directory_modes']),
                'promotion requires checks and verifier for exact candidate')
        before = self.state['source_expected'].copy()
        after = self.state['expected'].copy()
        before_modes = self.state['source_modes'].copy()
        after_modes = self.state['candidate_modes'].copy()
        changes = sorted(n for n in before if before[n] != after[n])
        require(set(changes) <= set(self.plan['editable']), 'promotion outside editable scope')
        for name in changes:
            if before[name] is not None:
                target = directory / 'before' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                core.atomic_write(target, file_path(self.root, name).read_bytes(), exclusive=True)
                require(digest(target) == before[name], 'source changed during promotion preparation')
        journal = directory / 'promotion.json'
        core.write_json(journal, {'before': before, 'after': after, 'before_modes': before_modes,
                                 'after_modes': after_modes, 'changes': changes,
                                 'candidate_sha256': fingerprint(after, after_modes, self.state['directory_modes'])}, exclusive=True)
        self.state['promotion'] = {'path': str(journal.relative_to(self.root)), 'sha256': digest(journal)}
        self.save()  # Durable intent before the first project write.
        return self.finish_promotion()

    def finish_promotion(self):
        self.guard()
        self.fresh(source=False)
        require(directory_modes(self.root, self.plan['files']) == self.state['directory_modes'],
                'declared source directory permissions changed before promotion')
        ref = self.state['promotion']
        journal = core.safe_project_file(self.root, ref['path'])
        require(digest(journal) == ref['sha256'], 'promotion journal changed')
        data = core.load_json(journal)
        require(data['after'] == self.state['expected'] and data['before'] == self.state['initial'] and
                data['before_modes'] == self.state['initial_modes'] and data['after_modes'] == self.state['candidate_modes'] and
                data['candidate_sha256'] == self.state['final_candidate_sha256'] == fingerprint(data['after'], data['after_modes'], self.state['directory_modes']),
                'promotion does not match verified candidate')
        require(data['changes'] == sorted(n for n in data['before'] if data['before'][n] != data['after'][n]) and
                set(data['changes']) <= set(self.plan['editable']), 'invalid promotion scope')
        current = file_map(self.root, self.plan['files'])
        current_modes = file_modes(self.root, self.plan['files'])
        for name in current:
            require((current[name], current_modes[name]) in ((data['before'][name], data['before_modes'][name]),
                                                             (data['after'][name], data['after_modes'][name])),
                    'promotion conflict; preserve external edit: ' + name)
        for name in data['changes']:
            if data['before'][name] is not None:
                require(digest(journal.parent / 'before' / name) == data['before'][name], 'promotion backup changed')
        for name in data['changes']:
            self.guard()
            target = file_path(self.root, name, optional=True)
            now = digest(target) if target.exists() else None
            mode = target.stat().st_mode & 0o777 if target.exists() else None
            if (now, mode) == (data['after'][name], data['after_modes'][name]):
                continue
            require((now, mode) == (data['before'][name], data['before_modes'][name]), 'promotion conflict: ' + name)
            if data['before'][name] is not None:
                require(digest(journal.parent / 'before' / name) == data['before'][name], 'promotion backup changed')
            content = file_path(self.candidate_root, name).read_bytes()
            require(hashlib.sha256(content).hexdigest() == data['after'][name], 'candidate changed during promotion')
            core.atomic_write(target, content, expected_sha256=data['before'][name],
                              exclusive=data['before'][name] is None, mode=data['after_modes'][name])
        require(file_map(self.root, self.plan['files']) == data['after'], 'promotion readback mismatch')
        require(file_modes(self.root, self.plan['files']) == data['after_modes'], 'promotion permission readback mismatch')
        require(directory_modes(self.root, self.plan['files']) == self.state['directory_modes'],
                'declared source directory permissions changed during promotion')
        self.state['source_expected'] = data['after'].copy()
        self.state['source_modes'] = data['after_modes'].copy()
        return {'before': data['before'], 'after': data['after'], 'promoted': data['changes']}

    def recover_promotion(self):
        # A new explicit recovery gets a bounded local deadline; it never dispatches agents.
        self.recovery_deadline = time.monotonic() + 60
        try:
            return self._recover_promotion()
        finally:
            self.recovery_deadline = None

    def _recover_promotion(self):
        # Only a deterministic, already verified promotion can be finished. Never redispatch an agent.
        with core.project_lock(self.root):
            self.load()
            if self.state['status'] == 'completed_advisory':
                self.fresh()
                return self.status()
            require((self.state.get('active') or {}).get('stage') == 'promote' and self.state.get('promotion'),
                    'only journaled promotion can be recovered; inspect other unfinished stages')
            uncertain_state = copy.deepcopy(self.state)
            self.guard()
            result = self.finish_promotion()
            run = self.state['active']['coordinator_run']
            journal = self.root / self.state['promotion']['path']
            directory = journal.parent
            task = self.coordinator.snapshot(run)[0]
            if task['state'] == 'succeeded':
                path = Path(task['data']['result']['artifact'])
                require(digest(path) == task['data']['result']['sha256'], 'completed promotion result changed')
                saved = core.load_json(path)
                require(saved['result'] == result, 'completed promotion result mismatch')
                for name, expected in saved['evidence'].items():
                    require(digest(core.safe_project_file(self.root, name)) == expected, 'promotion evidence changed')
            else:
                if task['state'] in ('reserved', 'running'):
                    self.transition(run, 'recover')
                self.transition(run, 'reconcile', outcome='running', evidence_ref=str(journal),
                                dispatch_ref=f'local-recovery:{os.getpid()}')
                path = directory / 'recovered-result.json'
                if path.exists():
                    saved = core.load_json(path)
                    require(saved['result'] == result, 'recovery result mismatch')
                    for name, expected in saved['evidence'].items():
                        require(digest(core.safe_project_file(self.root, name)) == expected, 'recovery evidence changed')
                else:
                    evidence = {str((directory / n).relative_to(self.root)): h for n,h in tree_map(directory).items()}
                    core.write_json(path, {'stage': 'promote', 'result': result, 'evidence': evidence}, exclusive=True)
                self.transition(run, 'result', artifact=str(path), sha256=digest(path),
                                evidence_ref=str(journal), outcome_ref='reconciled-promotion')
            self.state['records'].append({'path': str(path.relative_to(self.root)), 'sha256': digest(path), 'coordinator_run': run})
            try:
                self.fresh()  # Do not persist completion if the promoted source or evidence changed.
                self.state.update(active=None, status='completed_advisory', reason=None)
                return self._completed_status()
            except Exception:
                # Retain the journal and active uncertain state if completion
                # validation or its state write fails.
                self.state = uncertain_state
                self.save()
                raise
