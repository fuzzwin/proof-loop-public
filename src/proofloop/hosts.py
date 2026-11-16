"""Codex and command hosts for the bridge protocol."""

import json
import os
import shutil
import uuid
from pathlib import Path

from . import core
from .coordinator import require
from .filesystem import MAX_BYTES
from .processes import ProcessRunner, expand
from .validation import parse_json, schema_for, validate_response


class CodexHost:
    def __init__(self, executable='codex', runner=None):
        self.executable = str(Path(shutil.which(executable) or executable).resolve())
        require(Path(self.executable).is_file() and os.access(self.executable, os.X_OK), 'Codex executable unavailable')
        self.runner = runner or ProcessRunner()

    def run(self, role, prompt, candidate, *, workspace, directory, timeout, cancelled, launched, requirements=None):
        schema = directory / 'schema.json'
        final = directory / 'final.json'
        core.write_json(schema, schema_for(role, requirements), exclusive=True)
        core.atomic_write(directory / 'prompt.txt', prompt.encode(), exclusive=True)
        argv = [self.executable, 'exec', '--ignore-user-config', '--ephemeral', '--sandbox', 'read-only',
                '-c', 'approval_policy="never"', '--skip-git-repo-check', '--json', '--color', 'never',
                '--output-schema', str(schema), '--output-last-message', str(final), '-C', str(workspace), '-']
        # The bridge owns delegation and its budget; children cannot fan out or
        # inherit memory, app connectors, plugins or hooks from this host.
        for feature in ('multi_agent', 'multi_agent_v2', 'memories', 'apps', 'plugins',
                        'hooks', 'computer_use', 'browser_use'):
            argv[2:2] = ['--disable', feature]
        result = self.runner.run(argv, cwd=workspace, directory=directory, timeout=timeout,
                                 cancelled=cancelled, launched=launched, stdin=prompt)
        require(result['exit_code'] == 0, 'Codex process failed; inspect stderr, no automatic redispatch')
        events = [json.loads(line) for line in (directory / 'stdout.log').read_text().splitlines() if line.strip()]
        require(all(isinstance(e, dict) for e in events), 'invalid native events')
        started = [e.get('thread_id') for e in events if e.get('type') == 'thread.started']
        completed = [e for e in events if e.get('type') == 'turn.completed']
        require(len(started) == 1 and isinstance(started[0], str) and bool(started[0]), 'exact native thread identity required')
        require(len(completed) == 1 and not any(e.get('type') in ('error', 'turn.failed') for e in events), 'native turn not completed cleanly')
        messages = [e['item'].get('text') for e in events if e.get('type') == 'item.completed' and
                    isinstance(e.get('item'), dict) and e['item'].get('type') == 'agent_message']
        require(messages and final.is_file() and not final.is_symlink() and final.stat().st_size <= MAX_BYTES, 'native final response missing/oversized')
        value = core.load_json(final)
        require(core.json_equal(json.loads(messages[-1]), value), 'final file differs from native final message')
        validate_response(role, value, candidate, requirements)
        return {**result, 'thread_id': started[0], 'usage': completed[0].get('usage'), 'response': value,
                'requested_model': 'host default', 'observed_model': None,
                'classification': 'native_agent_report_not_portable_acceptance'}


class CommandHost:
    """One fresh local process per role; session identities remain attributed reports."""
    def __init__(self, argv, runner):
        self.argv, self.runner = argv, runner

    def run(self, role, prompt, candidate, *, workspace, directory, timeout, cancelled, launched):
        request = {'protocol_version': 1, 'request_id': str(uuid.uuid4()), 'role': role,
                   'candidate_sha256': candidate, 'response_schema': schema_for(role), 'prompt': prompt}
        core.write_json(directory / 'request.json', request, exclusive=True)
        result = self.runner.run(expand(self.argv, workspace), cwd=workspace, directory=directory,
                                 timeout=timeout, cancelled=cancelled, launched=launched,
                                 stdin=json.dumps(request))
        require(result['exit_code'] == 0, 'agent host process failed')
        value = parse_json((directory / 'stdout.log').read_bytes())
        require(isinstance(value, dict) and set(value) ==
                {'protocol_version', 'request_id', 'role', 'candidate_sha256', 'session_id', 'response'},
                'invalid command-host response fields')
        require(type(value['protocol_version']) is int and value['protocol_version'] == 1 and
                all(value[k] == request[k] for k in ('request_id', 'role', 'candidate_sha256')),
                'agent host response does not match this request')
        require(isinstance(value['session_id'], str) and 0 < len(value['session_id'].strip()) <= 200,
                'agent host must report a session identity')
        validate_response(role, value['response'], candidate)
        return {**result, 'thread_id': value['session_id'], 'response': value['response'],
                'classification': 'local_host_report_not_authenticated_identity'}
