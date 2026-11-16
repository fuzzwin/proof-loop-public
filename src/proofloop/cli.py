"""Command line entry points for the checkout and installed package."""
import argparse
import json
import sys

from .core import (
    RUN_ID_RE,
    VERSION,
    canonical_json,
    cmd_close,
    cmd_drift,
    cmd_init,
    cmd_observe,
    cmd_recheck,
    cmd_repair,
    cmd_resume,
    cmd_start,
    cmd_status,
    cmd_validate,
    cmd_validate_registry,
)


def parser():
    p = argparse.ArgumentParser(prog="proofloop", description="Portable, project-scoped evidence workflow")
    p.add_argument("--version", action="version", version=f"Proof Loop {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create project-scoped .proof starter files")
    init.add_argument("root"); init.add_argument("--force", action="store_true", help="fill missing starter files without overwriting existing files")
    init.set_defaults(func=cmd_init)
    registry = sub.add_parser("validate-registry", help="check capability registry lifecycle and policy-first fields")
    registry.add_argument("--root", required=True); registry.set_defaults(func=cmd_validate_registry)
    start = sub.add_parser("start", help="create an intake Run Card and run state")
    start.add_argument("--root", required=True); start.add_argument("--goal", required=True); start.add_argument("--scope", required=True)
    start.add_argument("--playbook", default="build"); start.add_argument("--risk", choices=["low", "medium", "high", "critical"], default="low")
    start.add_argument("--use-capability", action="append", default=[]); start.add_argument("--exclude-capability", action="append", default=[])
    start.add_argument("--assumption", action="append", default=[]); start.add_argument("--done", action="append", required=True)
    start.add_argument("--check", action="append", required=True, help="required check name; repeat for each gate check")
    start.add_argument("--input", action="append", default=[]); start.add_argument("--acceptance-test", action="append", required=True); mode = start.add_mutually_exclusive_group(); mode.add_argument("--baseline-pin", help="externally governed acceptance hash pin outside the project"); mode.add_argument("--advisory", action="store_true", help="allow low-risk work without acceptance pin; the run can never be accepted as passed_for_scope"); start.add_argument("--run-id")
    start.set_defaults(func=cmd_start)
    recheck = sub.add_parser("recheck", help="freeze repaired inputs in a new run with unchanged acceptance criteria")
    recheck.add_argument("--root", required=True); recheck.add_argument("--run-id", required=True)
    recheck.add_argument("--new-run-id", required=True); recheck.add_argument("--baseline-pin")
    recheck.set_defaults(func=cmd_recheck)
    status = sub.add_parser("status", help="show persisted run status")
    status.add_argument("--root", required=True); status.add_argument("--run-id", required=True); status.set_defaults(func=cmd_status)
    resume = sub.add_parser("resume", help="summarize state and next action")
    resume.add_argument("--root", required=True); resume.add_argument("--run-id", required=True); resume.set_defaults(func=cmd_resume)
    repair = sub.add_parser("repair", help="record a bounded repair round")
    repair.add_argument("--root", required=True); repair.add_argument("--run-id", required=True); repair.add_argument("--reason", required=True); repair.set_defaults(func=cmd_repair)
    close = sub.add_parser("close", help="record a terminal non-pass state")
    close.add_argument("--root", required=True); close.add_argument("--run-id", required=True); close.add_argument("--state", choices=["failed", "blocked", "cancelled", "superseded"], required=True); close.add_argument("--reason", required=True); close.set_defaults(func=cmd_close)
    observe = sub.add_parser("observe", help="record observed invocation/spend metrics without claiming enforcement")
    observe.add_argument("--root", required=True); observe.add_argument("--run-id", required=True); observe.add_argument("--invocations", type=int, required=True); observe.add_argument("--spend", help="observed amount/currency or 'not available'"); observe.add_argument("--source", required=True, help="evidence source for the observation"); observe.set_defaults(func=cmd_observe)
    validate = sub.add_parser("validate", help="validate receipt shape and input freshness")
    validate.add_argument("--root", required=True); validate.add_argument("--run-id", required=True); validate.add_argument("--baseline-pin", help="same externally governed acceptance pin used at start"); validate.set_defaults(func=cmd_validate)
    drift = sub.add_parser("drift", help="read-only source-versus-generated-view JSON check; writes a new evidence log")
    drift.add_argument("--root", required=True); drift.add_argument("--run-id", required=True); drift.add_argument("--source", required=True); drift.add_argument("--view", required=True); drift.add_argument("--log", required=True); drift.set_defaults(func=cmd_drift)
    orchestration = sub.add_parser("orchestrate", help="run or resume a bounded synthetic projection plan; advisory only")
    orchestration.add_argument("--root", required=True)
    orchestration.add_argument("--run-id", required=True)
    orchestration.add_argument("--plan", required=True)
    orchestration.add_argument("--max-steps", type=int, default=3)
    def orchestrate(args):
        from .orchestration import command
        return command(args)
    orchestration.set_defaults(func=orchestrate)
    agent_run = sub.add_parser("agent-run", help="run/resume bounded native Codex agents with quality and verifier gates")
    agent_run.add_argument("--root", required=True); agent_run.add_argument("--run-id", required=True)
    agent_run.add_argument("--plan", required=True); agent_run.add_argument("--max-steps", type=int, default=100)
    def run_agents(args):
        from .bridge import command
        return command(args)
    agent_run.set_defaults(func=run_agents)
    agent_cancel = sub.add_parser("agent-cancel", help="request cancellation without redispatching work")
    agent_cancel.add_argument("--root", required=True); agent_cancel.add_argument("--run-id", required=True)
    def cancel_agents(args):
        from .bridge import cancel_command
        return cancel_command(args)
    agent_cancel.set_defaults(func=cancel_agents)
    for name, help_text in [('agent-preflight', 'inspect plan/runtime dependencies without dispatch'),
                            ('agent-status', 'inspect progress, evidence and recovery without writes'),
                            ('agent-recover', 'finish a journaled verified promotion; never redispatch agents')]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument('--root', required=True)
        command.add_argument('--plan', required=True)
        if name != 'agent-preflight':
            command.add_argument('--run-id', required=True)
        if name == 'agent-recover':
            command.add_argument('--finish-promotion', action='store_true', required=True)
        def bridge_command(args, name=name):
            from .bridge import preflight_command, status_command, recover_command
            return {'agent-preflight': preflight_command, 'agent-status': status_command,
                    'agent-recover': recover_command}[name](args)
        command.set_defaults(func=bridge_command)
    profile_init = sub.add_parser('profile-init', help='configure reusable project scope and checks once')
    profile_init.add_argument('--root', required=True)
    profile_init.add_argument('--profile', default='proofloop-project.json')
    profile_init.add_argument('--from-plan')
    profile_init.add_argument('--editable', nargs='+')
    profile_init.add_argument('--protected', nargs='+')
    profile_init.add_argument('--context', nargs='+')
    profile_init.add_argument('--check-json', action='append', help='trusted command as a JSON argv array')
    def initialize_profile(args):
        from .task_workflow import initialize_profile
        return initialize_profile(args)
    profile_init.set_defaults(func=initialize_profile)
    fix = sub.add_parser('fix', help='describe a fix once; return a verified diff or precise blocker')
    fix.add_argument('--root', required=True)
    fix.add_argument('--profile', default='proofloop-project.json')
    fix.add_argument('--run-id', help='reuse the exact request/profile to resume; defaults to a unique ID')
    request = fix.add_mutually_exclusive_group(required=True)
    request.add_argument('--request')
    request.add_argument('--request-file', help='project-relative UTF-8 request file')
    fix.add_argument('--json', action='store_true', help='emit the structured outcome/evidence report')
    def fix_task(args):
        from .task_workflow import command
        return command(args)
    fix.set_defaults(func=fix_task)
    for name, only in [('harness-run', False), ('harness-check', True)]:
        task = sub.add_parser(name, help='run a typed task contract' if not only else 'run checks without dispatching agents')
        task.add_argument('--root', required=True); task.add_argument('--run-id', required=True)
        task.add_argument('--task', required=True)
        task.add_argument('--max-steps', type=int, default=100)
        def run_harness(args):
            from .harness import command
            return command(args)
        task.set_defaults(func=run_harness, checks_only=only)
    observer = sub.add_parser('harness-status', help='inspect typed task evidence without execution')
    observer.add_argument('--root', required=True); observer.add_argument('--run-id', required=True)
    observer.add_argument('--task', required=True)
    def inspect_harness(args):
        from .harness import status_command
        return status_command(args)
    observer.set_defaults(func=inspect_harness)
    stop = sub.add_parser('harness-cancel', help='request cancellation of a typed task run')
    stop.add_argument('--root', required=True); stop.add_argument('--run-id', required=True)
    stop.set_defaults(func=cancel_agents)
    recovery = sub.add_parser('harness-recover', help='finish a journaled typed-task promotion without redispatch')
    recovery.add_argument('--root', required=True); recovery.add_argument('--run-id', required=True)
    recovery.add_argument('--task', required=True); recovery.add_argument('--finish-promotion', action='store_true', required=True)
    def recover_harness(args):
        from .harness import recover_command
        return recover_command(args)
    recovery.set_defaults(func=recover_harness)
    adapters = sub.add_parser('harness-adapters', help='list version-1 built-in check adapters')
    def show_adapters(args):
        from .checks import ADAPTERS
        print(canonical_json({'schema_version': 1, 'checks': ADAPTERS, 'hosts': ['codex', 'command']}), end='')
        return 0
    adapters.set_defaults(func=show_adapters)
    return p


def module_main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if hasattr(args, "run_id") and args.run_id is not None and (not isinstance(args.run_id, str) or not RUN_ID_RE.fullmatch(args.run_id)):
            raise ValueError("run id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
        return args.func(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def main(argv=None):
    """Run the normal proofloop command, including JSON status errors."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ('agent-status', 'harness-status'):
        return module_main(argv)

    from .bridge import status_command, status_report
    if argv[0] == 'harness-status':
        from .harness import status_command

    class StatusParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(message)

    parser = StatusParser(prog='proofloop ' + argv[0], description='Inspect persisted agent evidence without executing or writing anything')
    parser.add_argument('--root', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--task' if argv[0] == 'harness-status' else '--plan', required=True)
    try:
        args = parser.parse_args(argv[1:])
    except ValueError as exc:
        report = status_report(None)
        report['freshness']['issues'].append(str(exc))
        print(json.dumps(report, allow_nan=False))
        return 2
    return status_command(args)
