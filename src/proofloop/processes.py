"""Bounded subprocess execution, output limits, and process group cleanup."""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import core
from .coordinator import require
from .filesystem import MAX_LOG_BYTES


class BridgeStop(ValueError):
    pass


def expand(arguments, workspace):
    return [sys.executable if a == '{python}' else a.replace('{workspace}', str(workspace))
            for a in arguments]


class ProcessRunner:
    """No shell; recorded PID; bounded output/time; terminate process group on stop."""
    def run(self, argv, *, cwd, directory, timeout, cancelled, launched, stdin=None, umask=None, isolate_git=False):
        require(os.name == 'posix', 'bounded process-group execution currently requires a POSIX host')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        # This adapter uses saved native auth, never an implicitly inherited API key.
        env.pop('OPENAI_API_KEY', None)
        env.pop('CODEX_API_KEY', None)
        if isolate_git:
            for name in list(env):
                if name.startswith('GIT_'):
                    env.pop(name)
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CEILING_DIRECTORIES=str(Path(cwd).parent))
        stdout, stderr = directory / 'stdout.log', directory / 'stderr.log'
        start = time.monotonic()
        with contextlib.ExitStack() as handles:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            out = handles.enter_context(os.fdopen(os.open(stdout, flags, 0o600), 'wb'))
            err = handles.enter_context(os.fdopen(os.open(stderr, flags, 0o600), 'wb'))
            options = {'umask': umask} if umask is not None else {}
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                    stdout=out, stderr=err, start_new_session=(os.name == 'posix'), **options)
            try:
                launched(proc.pid)
                pending = stdin.encode() if stdin is not None else None
                while True:
                    if cancelled():
                        raise BridgeStop('cancelled')
                    if time.monotonic() - start >= timeout:
                        raise BridgeStop('process timeout; inspect evidence before any new run')
                    if stdout.stat().st_size + stderr.stat().st_size > MAX_LOG_BYTES:
                        raise BridgeStop('process output limit')
                    try:
                        proc.communicate(input=pending, timeout=min(.1, timeout))
                        break
                    except subprocess.TimeoutExpired:
                        pending = None
                require(stdout.stat().st_size + stderr.stat().st_size <= MAX_LOG_BYTES, 'process output limit')
                return {'pid': proc.pid, 'exit_code': proc.returncode, 'elapsed_seconds': time.monotonic() - start,
                        'argv': argv}
            finally:
                self.cleanup(proc, directory, sys.exc_info()[1])

    @staticmethod
    def cleanup(proc, directory, original_error):
        # A denied group signal must not skip reaping our direct child. It also
        # must not become success merely because the leader has exited: live
        # descendants may remain. Only ESRCH resolves a failed group signal.
        deadline = time.monotonic() + 5
        report = {'pid': proc.pid, 'group': None, 'group_error': None,
                  'leader_kill_error': None, 'reap_error': None,
                  'original_error': str(original_error) if original_error else None}
        group_error = reap_error = None
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            report['group'] = 'signal_sent'
        except ProcessLookupError:
            report['group'] = 'absent'
        except OSError as exc:
            group_error = exc
            report['group'] = 'unresolved'
            report['group_error'] = str(exc)
        try:
            if group_error is not None and proc.poll() is None:
                try:
                    proc.kill()
                except OSError as exc:
                    report['leader_kill_error'] = str(exc)
            proc.wait(timeout=max(0, deadline - time.monotonic()))
            report['leader_reaped'] = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            reap_error = exc
            report['reap_error'] = str(exc)
            report['leader_reaped'] = False
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
        if group_error is not None:
            try:
                os.killpg(proc.pid, 0)
                report['group_probe'] = 'exists'
            except ProcessLookupError:
                report['group_probe'] = 'absent'
                report['group'] = 'absent'
            except OSError as exc:
                report['group_probe'] = str(exc)
        report['status'] = ('complete' if report['leader_reaped'] and
                            report['group'] != 'unresolved' else 'uncertain')
        core.write_json(directory / 'cleanup.json', report, exclusive=True)
        if report['status'] == 'uncertain':
            trigger = '; original failure: ' + str(original_error) if original_error else ''
            raise BridgeStop('process cleanup uncertain; inspect cleanup.json' + trigger) from (reap_error or group_error)
