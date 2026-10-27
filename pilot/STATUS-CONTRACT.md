# Frozen real-task pilot contract — 26 October 2026

Implement `python3 proofloop agent-status --root PROJECT --run-id RUN --plan PLAN`.
This is a read-only observer, not a resume or acceptance operation. Use existing
schema-version-1 run state, completed stage results and nested hashes as evidence.
Do not migrate or rewrite existing state. Existing execution behavior stays intact.

JSON interface (schema_version 1): run_id, state, phase, active, reason,
cancellation_requested; budgets.repairs and budgets.agent_calls each with used,
limit, remaining; deadline (existing epoch seconds), time_remaining_seconds;
quality (latest completed quality summary with passed, or null);
verifier (latest completed verifier summary with verdict, thread_id,
candidate_sha256, findings, or null); freshness {state, issues}; historical;
acceptance exactly not_accepted; classification hash_consistency_only.
Unknown unavailable values are null, never fabricated defaults. time_remaining
and budget remaining clamp at zero. JSON numeric fields must be finite. Fields
from malformed evidence cannot be treated as valid success. Freshness states are
current, stale, indeterminate, unreadable. Issues are nonempty explanations for
any noncurrent result. historical is true when freshness is not current.

Exit 0 only for stable/current inspection, including a recorded blocked or
cancelled run with no unfinished stage. Exit 2 for stale, indeterminate, unreadable
or invalid input, and print a JSON explanation, not a traceback. An expired
historical completed run stays completed_advisory/current if its evidence matches;
status does not advance deadlines or synthesize new execution state.

Reader requirements:
- Never instantiate AgentBridge, Coordinator, CodexHost or ProcessRunner; open a
  database; acquire/create a project lock; spawn a process; call model tools;
  signal processes; change files, directories, runtime state, logs or caches.
- Must work without a Codex executable or credentials. CLI entrypoint must avoid
  writing local Python bytecode even when PYTHONDONTWRITEBYTECODE is not supplied.
- Reject unsafe run IDs, absolute/traversing/non-normalized plan/evidence paths,
  symlinks, malformed top-level shapes/types, unsupported schema/run identities,
  inconsistent counters, nonfinite deadlines, and malformed result/evidence maps.
- Validate plan hash, exact declared source file map (including declared missing
  editable files), completed record hashes and nested evidence hashes. Hash
  consistency is not authenticated process identity or general acceptance.
- Confine completed records to this run's steps; confine their nested evidence to
  the corresponding stage directory. No reading arbitrary external paths.
- Read exact state bytes before and after inspection. If they change, report
  indeterminate. Do not retry, write a lock, or infer liveness from a PID.
- A non-null active stage means unfinished work: report indeterminate even if the
  stored state says blocked_uncertain or cancelled. Do not claim current while an
  active candidate may be changing. Read cancellation marker before/after too;
  a change during observation is indeterminate.
- Preserve interpretable recorded state and quality/verifier results when stale,
  clearly marked historical. Do not trust state-only quality fields as verified
  completion evidence; summaries come from completed stage records.

Allowed edits: agent_bridge.py (status reader only), proofloop.py and proofloop
(CLI integration/bytecode suppression), tests/test_agent_status.py (new tests),
AGENT-BRIDGE.md (status usage). No changes to other implementation, old tests,
this contract, the protected acceptance script, runtime limits or frozen runner.
The existing bridge limit remains one repair, two configured native agent calls,
1800 total seconds, 600 seconds per agent, 120 seconds per quality command.
No specialist dispatch in this follow-up: the independent review already supplies the diagnosis.

Quality gates: full unittest suite plus protected pilot/acceptance.py.
Final delivery: separate fresh independent review of final diff/raw run evidence,
then a reviewed draft PR only if checks pass. Main remains unchanged.

User authorized a NEW run with a ten-minute per-agent limit on 26 October 2026. Preserve the stopped original run; other caps unchanged.

## Authorized targeted follow-up

The user explicitly approved one targeted repair, two native calls, 600 seconds
per agent and 1800 seconds total. Fix the independently reproduced symlink-swap
race. Bind traversal/opening to trusted directory descriptors, use atomic
no-symlink opens for leaf AND intermediate components, and validate type/size on
the opened descriptor before reading. Unsupported platforms must fail closed
with structured output. Never read an external target before detecting a swap.
Preserve before/after state and cancellation comparisons and read-only behavior.

The independent acceptance author may correct the sibling-stage fixture to use
an unlisted sibling file and adapt race instrumentation to both path-based and
descriptor-based reads before this new run is frozen. Add deterministic leaf and
intermediate-directory swap tests. Requirements are not relaxed, old frozen
checks/runs are preserved, and the new checks must fail the pre-fix candidate.
