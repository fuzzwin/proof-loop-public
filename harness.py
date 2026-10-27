"""General task contracts, typed checks and interchangeable local agent hosts."""
import json
import uuid

import agent_bridge as bridge
import harness_checks as checks
from coordinator import require

TASK_FIELDS = {'schema_version', 'goal', 'files', 'editable', 'protected', 'requirements',
               'checks', 'specialists', 'limits', 'host'}
LIMITS = {'max_repairs', 'max_agent_calls', 'agent_timeout_seconds',
          'check_timeout_seconds', 'max_run_seconds'}


def compile_task(task, path, *, allow_legacy=False):
    """Validate without reading files, creating state or resolving a host."""
    require(isinstance(task, dict) and set(task) == TASK_FIELDS, 'invalid task contract fields')
    require(type(task['schema_version']) is int and task['schema_version'] == 1,
            'unsupported task schema')
    require(isinstance(task['limits'], dict) and set(task['limits']) == LIMITS,
            'explicit repair, call and time limits required')
    plan = {k: task[k] for k in ('goal', 'files', 'editable', 'protected', 'specialists')}
    plan.update(task['limits'], quality=[['typed-checks']])
    bridge.validate_plan(plan, path)
    requirements = task['requirements']
    checks.strings(requirements, 'requirements')
    require(len(set(requirements)) == len(requirements) and set(requirements) <= set(plan['protected']),
            'requirements must name unique protected files')
    checks.validate_checks(task['checks'], plan['files'], plan['protected'], allow_legacy=allow_legacy)
    host = task['host']
    require(isinstance(host, dict), 'host must be an explicit object')
    if host.get('kind') == 'codex':
        require(set(host) == {'kind'}, 'invalid Codex host fields')
    else:
        require(set(host) == {'kind', 'argv', 'controls'} and host['kind'] == 'command', 'unknown agent host')
        checks.argv(host['argv'])
        checks.strings(host['controls'], 'agent host controls')
        require(len(set(host['controls'])) == len(host['controls']) and
                set(host['controls']) <= set(plan['protected']), 'agent host controls must be protected files')
    return {**plan, 'checks': task['checks'], 'requirements': requirements, 'host': host}


class CommandHost:
    """One fresh local process per role; session identities remain attributed reports."""
    def __init__(self, argv, runner):
        self.argv, self.runner = argv, runner

    def run(self, role, prompt, candidate, *, workspace, directory, timeout, cancelled, launched):
        request = {'protocol_version': 1, 'request_id': str(uuid.uuid4()), 'role': role,
                   'candidate_sha256': candidate, 'response_schema': bridge.schema_for(role), 'prompt': prompt}
        bridge.core.write_json(directory / 'request.json', request, exclusive=True)
        result = self.runner.run(checks.expand(self.argv, workspace), cwd=workspace, directory=directory,
                                 timeout=timeout, cancelled=cancelled, launched=launched,
                                 stdin=json.dumps(request))
        require(result['exit_code'] == 0, 'agent host process failed')
        value = bridge._status_json((directory / 'stdout.log').read_bytes())
        require(isinstance(value, dict) and set(value) ==
                {'protocol_version', 'request_id', 'role', 'candidate_sha256', 'session_id', 'response'},
                'invalid command-host response fields')
        require(type(value['protocol_version']) is int and value['protocol_version'] == 1 and
                all(value[k] == request[k] for k in ('request_id', 'role', 'candidate_sha256')),
                'agent host response does not match this request')
        require(isinstance(value['session_id'], str) and 0 < len(value['session_id'].strip()) <= 200,
                'agent host must report a session identity')
        bridge.validate_response(role, value['response'], candidate)
        return {**result, 'thread_id': value['session_id'], 'response': value['response'],
                'classification': 'local_host_report_not_authenticated_identity'}


def snapshot_state(root):
    return bridge.tree_state(root)


class HarnessBridge(bridge.AgentBridge):
    def __init__(self, root, run_id, task_path, host=None, runner=None):
        super().__init__(root, run_id, task_path, host=host, runner=runner,
                         plan_validator=compile_task, plan_loader=bridge._status_json)
        # Match the read-only observer's source-map contract before creating a run.
        bridge._status_source_map(bridge.file_map(self.root, self.plan['files']), self.plan)
        if self.host is None and self.plan['host']['kind'] == 'command':
            self.host = CommandHost(self.plan['host']['argv'], self.runner)

    def load(self):
        super().load()
        bridge._status_source_map(self.state.get('initial'), self.plan)
        bridge._status_source_map(self.state.get('expected'), self.plan)

    def verifier_requirements(self):
        # Harness requirements are protected filenames, not assessment rows.
        return None

    def fresh(self, source=True):
        super().fresh(source=source)
        latest = None
        for record in self.state['records']:
            value = bridge.core.load_json(bridge.core.safe_project_file(self.root, record['path']))
            require(isinstance(value.get('stage'), str), 'invalid recorded stage')
            if value['stage'].startswith(('quality-', 'recheck-')):
                latest = quality_summary(value['result'], self.plan)
        if latest is not None:
            require(checks.exact(latest, self.state.get('quality')), 'saved check summary differs from stage evidence')
        else:
            require(self.state.get('quality') is None, 'saved check summary has no stage evidence')

    def quality(self, directory, launched):
        result = checks.run_checks(self.plan['checks'], self.plan['files'], directory, self.runner,
                                  lambda: self.remaining(self.plan['check_timeout_seconds']),
                                  self.cancelled, launched, snapshot=self.snapshot,
                                  snapshot_state=snapshot_state, fresh=self.fresh)
        self.guard()
        result['candidate'] = self.state['expected'].copy()
        result['candidate_modes'] = self.state['candidate_modes'].copy()
        result['directory_modes'] = self.state['directory_modes'].copy()
        result['candidate_sha256'] = bridge.fingerprint(result['candidate'], result['candidate_modes'], result['directory_modes'])
        return result

    def check(self):
        """Advance only a quality stage. This path cannot call an agent host."""
        with bridge.core.project_lock(self.root):
            self.load()
            require(not self.state['active'], 'unfinished stage; inspect uncertain work before continuing')
            self.fresh()
            if self.state['status'] in ('completed_advisory', 'blocked', 'blocked_uncertain', 'cancelled') or \
                    self.state['phase'] != 'quality':
                return self.status()
            try:
                self.guard()
                result = self.stage('quality-' + str(self.state['round']), self.quality)
                self.state['quality'] = result
                if result['passed']:
                    self.state['phase'] = 'verifier'
                else:
                    self.failed_candidate('quality_failure', [result])
                if self.state['status'] == 'running':
                    self.state['status'] = 'checkpoint'
                self.save()
                return self.status()
            except Exception as exc:
                if self.state['status'] != 'blocked_uncertain':
                    self.state.update(status='cancelled' if self.cancelled() else 'blocked', reason=str(exc))
                self.save()
                raise

    def status(self):
        result = super().status()
        quality = self.state.get('quality')
        require(quality is None or isinstance(quality, dict), 'invalid saved check summary')
        quality = quality or {}
        current = bool(quality) and quality.get('candidate_sha256') == result['candidate_sha256']
        return {**result, 'verification': quality.get('verification') if current else None,
                'validation': quality.get('validation') if current else None,
                'checks': quality.get('checks', []) if current else [],
                'quality_historical': bool(quality) and not current,
                'historical_quality': quality if quality and not current else None,
                'requirements': self.plan['requirements'],
                'classification': 'scoped_checks_and_agent_reports_not_production_acceptance'}


def quality_summary(result, plan, *, allow_unbound=False):
    base = {'passed', 'checks', 'verification', 'validation'}
    require(isinstance(result, dict), 'invalid typed-check summary')
    unbound = set(result) == base
    legacy = set(result) == base | {'candidate', 'candidate_sha256'}
    require(set(result) == base | {'candidate', 'candidate_modes', 'directory_modes', 'candidate_sha256'} or
            (allow_unbound and (unbound or legacy)),
            'invalid typed-check summary')
    if not unbound:
        bridge._status_source_map(result['candidate'], plan)
        if not legacy:
            bridge._status_modes(result['candidate_modes'], result['candidate'], plan)
            bridge._status_directories(result['directory_modes'], plan)
        require(bridge._status_hash(result['candidate_sha256']) and
                result['candidate_sha256'] == bridge.fingerprint(result['candidate'], result.get('candidate_modes'), result.get('directory_modes')),
                'check candidate does not match its recorded source map')
    outcomes = result['checks']
    require(isinstance(outcomes, list) and len(outcomes) == len(plan['checks']), 'check coverage mismatch')
    for expected, actual in zip(plan['checks'], outcomes):
        require(isinstance(actual, dict) and
                all(actual.get(k) == expected[k] for k in ('id', 'purpose', 'adapter', 'criterion')),
                'check identity/criterion mismatch')
        require(checks.exact(actual.get('contract'), expected), 'recorded check contract mismatch')
        kind = expected['adapter']
        process = actual.get('process')
        require(actual.get('evidence_kind') == ('trusted_process_report' if process is not None
                                              else 'deterministic_file_check'), 'invalid evidence kind')
        require((process is not None) == (kind in ('python-unittest', 'command-json')),
                'check process evidence mismatch')
        observed = actual.get('observations')
        require(isinstance(observed, dict), 'check observations required')
        if process is not None:
            bridge._status_process(process)
        if kind == 'command-json':
            checks.validate_external(observed, expected['id'], expected['coverage'])
            require(process['exit_code'] >= 0, 'external check terminated by signal')
            passed = observed['state'] == 'pass'
            require((process['exit_code'] == 0) == passed, 'external result/exit disagree')
        elif kind == 'python-unittest':
            if unbound and set(observed) == {'counts', 'output'}:
                counts = observed['counts']
                require(isinstance(observed['output'], str) and isinstance(counts, dict) and set(counts) ==
                        {'tests', 'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'} and
                        all(type(v) is int and v >= 0 for v in counts.values()), 'invalid historical unittest counts')
                passed = counts['tests'] > 0 and all(v == 0 for k, v in counts.items() if k != 'tests')
                require(process['exit_code'] == (0 if passed else 1), 'historical test counts/exit disagree')
            else:
                passed = checks.validate_unittest(observed, expected, plan['files'], process['exit_code'])
        elif set(observed) == {'error'}:
            require(isinstance(observed['error'], str), 'invalid recorded check error')
            passed = False
        elif kind == 'text':
            require(set(observed) == {'missing', 'forbidden'}, 'invalid recorded text checks')
            for field, config in (('missing', 'contains'), ('forbidden', 'excludes')):
                checks.strings(observed[field], field, nonempty=False)
                require(set(observed[field]) <= set(expected[config]), 'unknown recorded text assertion')
            passed = not observed['missing'] and not observed['forbidden']
        else:
            assertions = observed.get('assertions')
            require(isinstance(assertions, list) and len(assertions) == len(expected['assertions']),
                    'recorded JSON assertions mismatch')
            passed = True
            for wanted, found in zip(expected['assertions'], assertions):
                require(isinstance(found, dict) and found.get('pointer') == wanted['pointer'],
                        'recorded JSON Pointer mismatch')
                require(set(found) in ({'pointer', 'actual_sha256', 'matches'}, {'pointer', 'missing', 'matches'}),
                        'invalid recorded JSON assertion fields')
                if found.get('missing') is True:
                    matches = False
                else:
                    require(bridge._status_hash(found.get('actual_sha256')), 'recorded JSON value hash missing')
                    matches = found['actual_sha256'] == checks.value_hash(wanted['equals'])
                require(type(found.get('matches')) is bool and found['matches'] == matches,
                        'recorded JSON comparison disagrees')
                passed = passed and matches
        require(actual.get('state') == ('pass' if passed else 'fail'), 'recorded check verdict disagrees')
    require(type(result['passed']) is bool and result['passed'] == all(c['state'] == 'pass' for c in outcomes),
            'overall check verdict disagrees')
    summary = checks.summarize(outcomes)
    require(all(checks.exact(result[p], summary[p]) for p in summary), 'purpose summary disagrees')
    return {**result, 'candidate': None, 'candidate_sha256': None, 'unbound': True} if unbound else result


def read_status(root, run_id, task_path):
    plan = {}
    def validator(value, path):
        plan.update(compile_task(value, path, allow_legacy=True))
        return plan
    def summary(stage, value):
        if stage.startswith(('quality-', 'recheck-')):
            return 'quality', quality_summary(value, plan, allow_unbound=True)
        return bridge._status_summary(stage, value)
    result = bridge.read_status(root, run_id, task_path, plan_validator=validator,
                                summary_reader=summary, include_candidate=True)
    quality = result.get('quality') or {}
    old_quality = bool(quality) and (result['historical'] or
                                     quality.get('candidate_sha256') != result.get('candidate_sha256'))
    if old_quality and result['state'] == 'completed_advisory' and result['freshness']['state'] == 'current':
        result['freshness']['state'] = 'stale'
        result['freshness']['issues'].append('completed run has checks for a different candidate')
        result['historical'] = True
    return {**result, 'verification': quality.get('verification') if quality and not old_quality else None,
            'validation': quality.get('validation') if quality and not old_quality else None,
            'quality_historical': old_quality,
            'classification': 'hash_consistency_only_not_authenticated_evidence'}


def command(args):
    loop = None
    try:
        loop = HarnessBridge(args.root, args.run_id, args.task)
        report = loop.check() if args.checks_only else loop.advance(args.max_steps)
        passed = report['state'] in ('checkpoint', 'completed_advisory')
        if args.checks_only:
            passed = passed and all((report.get(p) or {}).get('state') == 'pass'
                                    for p in ('verification', 'validation'))
        code = 0 if passed else 2
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        report = {'run_id': args.run_id, 'state': 'blocked', 'reason': str(exc), 'acceptance': 'not_accepted'}
        if loop is not None and hasattr(loop, 'state'):
            try:
                recorded = loop.status()
                report.update(recorded, recorded_state=recorded['state'],
                              state='blocked_uncertain' if recorded['state'] == 'blocked_uncertain' else 'blocked',
                              reason=str(exc))
            except (ValueError, TypeError, KeyError):
                pass
        code = 2
    finally:
        if loop:
            loop.close()
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return code


def status_command(args):
    report = read_status(args.root, args.run_id, args.task)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report['freshness']['state'] == 'current' else 2
