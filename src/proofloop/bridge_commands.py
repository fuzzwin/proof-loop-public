"""CLI commands that start, cancel, recover, or prepare bridge runs."""

import os
import shutil
import sys
from pathlib import Path

from . import core
from .coordinator import require
from .filesystem import MAX_BYTES, checked_project_root, directory_modes, file_map, file_path
from .validation import validate_plan
from .bridge import AgentBridge


def command(args):
    bridge = AgentBridge(args.root, args.run_id, args.plan)
    try:
        result = bridge.advance(args.max_steps)
        print(core.canonical_json(result), end='')
        return 0 if result['state'] in ('checkpoint', 'completed_advisory') else 2
    finally:
        bridge.close()


def cancel_command(args):
    root = checked_project_root(args.root)
    require(core.RUN_ID_RE.fullmatch(args.run_id) is not None, 'invalid bridge run ID')
    relative = f'.proof/agent-runs/{args.run_id}'
    core.safe_project_file(root, relative + '/state.json')
    path = root / relative / 'cancel.json'
    if not path.exists():
        core.write_json(core.safe_write_path(root, relative + '/cancel.json'), {'requested_at': core.now()}, exclusive=True)
    print('Cancellation recorded; an active bridge stops its process group at the next poll.')
    return 0


def preflight(root, plan_path):
    """Read-only preparation: no project state, quality execution, or model calls."""
    root = checked_project_root(root)
    plan = validate_plan(core.load_json(file_path(root, plan_path)), plan_path)
    expected = file_map(root, plan['files'])
    directory_modes(root, plan['files'])
    require(all(expected[n] is not None for n in set(plan['files']) - set(plan['editable'])),
            'declared noneditable files must exist')
    require(sum(file_path(root, n).stat().st_size for n,v in expected.items() if v is not None) <= MAX_BYTES,
            'declared workspace too large')
    errors, warnings = [], []
    if os.name != 'posix':
        errors.append('POSIX process control is required.')
    if sys.version_info < (3, 10):
        errors.append('Python 3.10+ is required.')
    codex = shutil.which('codex')
    if not codex:
        errors.append('Codex CLI is not on PATH; install/sign in before native dispatch.')
    for argv in plan['quality']:
        executable = sys.executable if argv[0] == '{python}' else argv[0]
        available = (root / executable).is_file() if '/' in executable and not Path(executable).is_absolute() else shutil.which(executable)
        if not available:
            errors.append('Quality executable unavailable: ' + executable)
    # Static import hints only. Never import project code during preflight.
    import ast
    import importlib.machinery
    modules = {Path(n).parts[0].split('.')[0] for n in plan['files']}
    stdlib = getattr(sys, 'stdlib_module_names', set()) | set(sys.builtin_module_names)
    for name in plan['files']:
        if expected[name] is None or not name.endswith('.py'):
            continue
        try:
            tree = ast.parse(file_path(root, name).read_text())
        except (SyntaxError, UnicodeError) as exc:
            warnings.append(f'{name}: cannot inspect imports ({type(exc).__name__}).')
            continue
        imports = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imports |= {node.module.split('.')[0] for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
        for module in sorted(imports - modules - stdlib):
            spec = importlib.machinery.PathFinder.find_spec(module)
            if spec is None:
                warnings.append(f'{name}: dependency {module} is absent from declared files and current Python path.')
    repair_calls = 2 + max((sum(trigger in r['when'] for r in plan['specialists'])
                            for trigger in ('quality_failure', 'verification_failure')), default=0)
    if plan['max_repairs'] and plan['max_agent_calls'] < repair_calls:
        warnings.append(f'A repair plus matching specialists and fresh verifier can require {repair_calls} calls; budget is {plan["max_agent_calls"]}.')
    return {'ready': not errors, 'errors': errors, 'warnings': warnings,
            'runtime': {'python': sys.executable, 'python_version': sys.version, 'codex_path': codex,
                        'codex_authentication': 'not_checked', 'native_protocol': 'validated_at_dispatch'},
            'files': len(expected), 'editable': plan['editable'], 'protected': plan['protected'],
            'requirements': len(plan.get('requirements', [])), 'test_adapter': plan.get('test_adapter'),
            'repair_call_reserve': repair_calls, 'agent_calls_used': 0,
            'note': 'Static preflight only. Quality commands and native authentication have not been exercised.'}


def preflight_command(args):
    result = preflight(args.root, args.plan)
    print(core.canonical_json(result), end='')
    return 0 if result['ready'] else 2


def recover_command(args):
    require(args.finish_promotion, 'explicit --finish-promotion is required')
    bridge = AgentBridge(args.root, args.run_id, args.plan)
    try:
        print(core.canonical_json(bridge.recover_promotion()), end='')
        return 0
    finally:
        bridge.close()
