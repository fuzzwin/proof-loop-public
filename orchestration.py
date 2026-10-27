"""Bounded deterministic orchestration; no model calls or live-app adapters.

Coordinator success means a stage produced an artifact, never portable acceptance.
The synthetic worker actively repairs its declared JSON projection, then the core
recomputes its check. Host identity and OS isolation remain external concerns.
"""
import contextlib
import io
import json
from pathlib import Path

import proofloop as core
from coordinator import Coordinator, digest, require

PLAN_FIELDS = {'adapter', 'check', 'source', 'view', 'acceptance_test', 'allow_repair'}


def core_call(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        result = core.main(argv)
    require(result == 0, 'core command failed: ' + argv[0])


def projection(root, plan):
    source = core.load_json(core.safe_project_file(root, plan['source']))
    require(source.get('fixture_classification') == 'synthetic', 'worker permits synthetic data only')
    items = source.get('items')
    require(isinstance(items, list) and all(isinstance(x, dict) and 'id' in x for x in items), 'invalid synthetic items')
    return {'record_count': len(items), 'ids': [x['id'] for x in items], 'fixture_classification': 'synthetic'}


class SyntheticLoop:
    """One trusted host owns an active claim. Resume only at completed checkpoints.

    If a host dies during a stage, its claim stays active/uncertain and this runner
    stops. An operator must inspect actual outcomes with Coordinator recovery APIs;
    it never assumes elapsed time proves a side effect did not happen.
    """
    def __init__(self, root, run, plan_path):
        self.root = core.project_root(str(root))
        require(core.RUN_ID_RE.fullmatch(run) is not None and len(run) <= 54, 'invalid orchestration run ID (maximum 54 characters)')
        self.run = run
        self.plan_path = plan_path
        self.plan = core.load_json(core.safe_project_file(self.root, plan_path))
        require(isinstance(self.plan, dict) and set(self.plan) == PLAN_FIELDS, 'invalid plan fields')
        require(type(self.plan['allow_repair']) is bool, 'repair permission must be boolean')
        require(all(isinstance(self.plan[k], str) and self.plan[k] for k in PLAN_FIELDS - {'allow_repair'}), 'plan strings required')
        require(len({plan_path, self.plan['source'], self.plan['view'], self.plan['acceptance_test']}) == 4, 'plan paths must be distinct')
        checked_paths = []
        for name in (plan_path, self.plan['source'], self.plan['view'], self.plan['acceptance_test']):
            require(not Path(name).parts[0].startswith('.proof'), 'operational inputs cannot be evidence files')
            checked_paths.append(core.safe_project_file(self.root, name))
        require(len(set(checked_paths)) == 4, 'resolved plan paths must be distinct')
        # Freeze the plan and evidence paths under the core's exclusive project lock.
        if not (self.root / '.proof').exists():
            core_call(['init', str(self.root)])
        with core.project_lock(self.root):
            relative_contract = f'.proof/orchestration/{run}/contract.json'
            contract_candidate = self.root / relative_contract
            contract_checked = core.safe_project_file(self.root, relative_contract) if contract_candidate.exists() else core.safe_write_path(self.root, relative_contract)
            self.directory = contract_checked.parent
            contract_path = self.directory / 'contract.json'
            bound = {'plan': self.plan, 'plan_path': plan_path, 'plan_sha256': digest(self.root / plan_path),
                     'source_sha256': digest(self.root / self.plan['source']),
                     'acceptance_sha256': digest(self.root / self.plan['acceptance_test'])}
            if contract_path.exists():
                self.contract = core.load_json(contract_path)
                require(all(self.contract[k] == v for k, v in bound.items()), 'frozen plan/source/acceptance changed')
            else:
                self.contract = {**bound, 'initial_view_sha256': digest(self.root / self.plan['view'])}
                core.write_json(contract_path, self.contract, exclusive=True)
            db_relative = '.proof/coordinator.sqlite'
            db_path = core.safe_project_file(self.root, db_relative) if (self.root / db_relative).exists() else core.safe_write_path(self.root, db_relative)
            for suffix in ('', '-wal', '-shm'):
                require(not Path(str(db_path) + suffix).is_symlink(), 'database cannot be symlinked')
            self.coordinator = Coordinator(db_path)
            resource = 'project-writer:' + str(self.root)
            tasks = [dict(id=name, description=description, dependencies=deps, resources=[resource], reviewer=None)
                     for name, description, deps in [
                         ('inspect', 'Run built-in drift inspection', []),
                         ('repair', 'Repair only the authorized synthetic projection', ['inspect']),
                         ('verify', 'Recompute drift after repair under a new advisory Run Card', ['repair'])]]
            self.coordinator.apply(run + ':submit', 'submit', run=run, tasks=tasks)

    def close(self):
        self.coordinator.close()

    def row(self, name):
        return next(x for x in self.coordinator.snapshot(self.run) if x['id'] == name)

    def event(self, name, operation, **kwargs):
        row = self.row(name)
        return self.coordinator.apply(f'{self.run}:{name}:{row["version"]}:{operation}', operation,
                                      run=self.run, task=name, version=row['version'], **kwargs)

    def freshness(self):
        require(digest(self.root / self.plan_path) == self.contract['plan_sha256'], 'plan changed')
        require(digest(core.safe_project_file(self.root, self.plan['source'])) == self.contract['source_sha256'], 'source changed')
        require(digest(core.safe_project_file(self.root, self.plan['acceptance_test'])) == self.contract['acceptance_sha256'], 'acceptance changed')
        repaired = self.row('repair')
        expected = self.contract['initial_view_sha256']
        if repaired['state'] == 'succeeded':
            repair_result = repaired['data']['result']
            require(digest(repair_result['artifact']) == repair_result['sha256'], 'repair evidence changed')
            expected = core.load_json(Path(repair_result['artifact']))['view_sha256']
        require(digest(core.safe_project_file(self.root, self.plan['view'])) == expected, 'view changed outside completed worker stage')

    def drift(self, stage):
        run_id = self.run + '-' + stage
        argv = ['start', '--root', str(self.root), '--run-id', run_id, '--advisory',
                '--goal', 'Inspect synthetic JSON projection', '--scope', 'Read declared JSON; write evidence only',
                '--use-capability', 'python-stdlib', '--check', self.plan['check'],
                '--input', self.plan['source'], '--input', self.plan['view'],
                '--acceptance-test', self.plan['acceptance_test'], '--done', 'Declared source projection matches view']
        core_call(argv)
        log = f'.proof/runs/{run_id}/logs/source-view-drift.json'
        with contextlib.redirect_stdout(io.StringIO()):
            code = core.main(['drift', '--root', str(self.root), '--run-id', run_id,
                              '--source', self.plan['source'], '--view', self.plan['view'], '--log', log])
        require(code in (0, 1), 'drift runner did not produce a check')
        result = {'stage': stage, 'core_run': run_id, 'log': log,
                  'log_sha256': digest(self.root / log), 'exit_code': code,
                  'state': 'match' if code == 0 else 'drift', 'acceptance': 'advisory_not_accepted',
                  'core_controls': {relative: digest(self.root / relative) for relative in (
                      f'.proof/runs/{run_id}/run-card.json',
                      f'.proof/runs/{run_id}/input-manifest.json',
                      f'.proof/baselines/{run_id}.json')}}
        if stage == 'verify':
            require(code == 0, 'recheck still fails')
            core.verify_drift_log(self.root, run_id, self.plan['check'], self.root / log, core.load_json(self.root / log))
        return result

    def execute(self, name):
        if name in ('inspect', 'verify'):
            return self.drift(name)
        initial = core.load_json(self.directory / 'inspect.json')
        require(digest(self.root / initial['log']) == initial['log_sha256'], 'initial check evidence changed')
        expected = projection(self.root, self.plan)
        view = core.safe_project_file(self.root, self.plan['view'])
        changed = not core.json_equal(core.load_json(view), expected)
        require(not changed or self.plan['allow_repair'], 'repair not authorized; approve a new plan/run')
        if changed:
            core_call(['repair', '--root', str(self.root), '--run-id', self.run + '-inspect', '--reason', 'Declared synthetic projection drift'])
            with core.project_lock(self.root):
                self.freshness()
                core.write_json(view, expected, expected_sha256=self.contract['initial_view_sha256'])
            core_call(['close', '--root', str(self.root), '--run-id', self.run + '-inspect',
                       '--state', 'superseded', '--reason', 'Authorized projection repair changed input; preserved original evidence; verify under new Run Card'])
        return {'stage': name, 'changed': changed, 'view_sha256': digest(view), 'repair_rounds_used': int(changed)}

    def verify_completed(self, name):
        self.coordinator.verify_dependencies(self.run, [name])
        if name in ('inspect', 'verify'):
            value = core.load_json(Path(self.row(name)['data']['result']['artifact']))
            log_path = core.safe_project_file(self.root, value['log'])
            require(digest(log_path) == value['log_sha256'], 'core log evidence changed')
            for relative, expected in value['core_controls'].items():
                require(digest(core.safe_project_file(self.root, relative)) == expected, 'core control evidence changed')
            if name == 'verify':
                core.verify_drift_log(self.root, value['core_run'], self.plan['check'], log_path, core.load_json(log_path))

    def advance(self, max_steps=3):
        require(type(max_steps) is int and 1 <= max_steps <= 3, 'max_steps must be 1..3')
        if self.plan['adapter'] != 'synthetic-projection-v1' or self.plan['check'] != 'source-view-drift':
            if self.row('inspect')['state'] == 'queued':
                self.event('inspect', 'block', reason='Unsupported adapter/check; no Verified adapter available')
            return self.status()
        self.freshness()
        # Check synthetic applicability before acquiring any mutation claim.
        projection(self.root, self.plan)
        steps = 0
        for name in ('inspect', 'repair', 'verify'):
            row = self.row(name)
            if row['state'] == 'succeeded':
                self.verify_completed(name)
                continue
            if row['state'] != 'queued' or steps >= max_steps:
                break
            self.freshness()
            if name == 'repair' and not self.plan['allow_repair'] and core.load_json(self.directory / 'inspect.json')['exit_code']:
                self.event(name, 'block', reason='repair not authorized; approve a new plan/run')
                break
            self.event(name, 'claim')
            self.event(name, 'dispatch', dispatch_ref=f'deterministic-local:{self.run}:{name}')
            try:
                value = self.execute(name)
                artifact = core.safe_write_path(self.root, f'.proof/orchestration/{self.run}/{name}.json')
                core.write_json(artifact, value, exclusive=True)
                self.event(name, 'result', artifact=str(artifact), sha256=digest(artifact),
                           evidence_ref=str(artifact), outcome_ref=f'deterministic-local:{self.run}:{name}')
            except Exception:
                # Do not retry an uncertain write or unlock its resource automatically.
                self.event(name, 'recover')
                raise
            steps += 1
        return self.status()

    def status(self):
        tasks = self.coordinator.snapshot(self.run)
        if all(t['state'] == 'succeeded' for t in tasks):
            state = 'completed_advisory'
        elif any(t['state'] not in ('queued', 'succeeded') for t in tasks):
            # Failed/cancelled tasks cannot be resumed. They must not look like
            # a successful checkpoint to callers that rely on the CLI exit code.
            state = 'blocked'
        else:
            state = 'checkpoint'
        return {'run_id': self.run, 'state': state, 'acceptance': 'not_accepted',
                'tasks': {t['id']: t['state'] for t in tasks},
                'next_action': 'Independent review and externally governed acceptance remain separate' if state == 'completed_advisory' else 'Inspect blockers/uncertain outcomes; resume only completed checkpoints'}


def command(args):
    loop = SyntheticLoop(args.root, args.run_id, args.plan)
    try:
        result = loop.advance(args.max_steps)
        print(json.dumps(result, indent=2))
        return 2 if result['state'] == 'blocked' else 0
    finally:
        loop.close()
