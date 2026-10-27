# Agent status: quick guide

Use `agent-status` to inspect a saved Proof Loop agent run without starting or continuing it. It is useful after a run, after a checkpoint, or when a run stopped, was cancelled, or may have left uncertain work. It helps answer **what did the run record, and does the evidence still match?**

It is an inspection command. It does not resume a run, dispatch an agent, run quality checks, edit project files, cancel a process, or decide production acceptance.

## Before you start

- Use Python 3.10 or newer on a POSIX host (macOS or Linux).
- Run the command from a Proof Loop checkout that includes the `proofloop`, `proofloop.py`, and `agent_bridge.py` files.
- You do **not** need Codex installed, logged in, or running to inspect evidence.
- Know the target project directory, the run ID, and that run's plan path. The plan path must be normalized and relative to the project root.
- Do not delete locks or restart uncertain work just because a deadline passed. Read the report and reconcile the recorded stage first.

## Run it

Replace the example paths and ID with your own:

```sh
PROJECT=/path/to/your/project
RUN_ID=my-run
PLAN=plan.json

python3 proofloop agent-status \
  --root "$PROJECT" \
  --run-id "$RUN_ID" \
  --plan "$PLAN"
```

The run record is read from `$PROJECT/.proof/agent-runs/$RUN_ID/`. `--plan` names the existing plan under `$PROJECT`; for example, `plan.json` means `$PROJECT/plan.json`. The command prints one JSON object to standard output.

For a one-request `fix`, use `--plan .proof/tasks/RUN_ID/plan.json`. The observer supports
legacy schema-1, draft schema-2 and new schema-3 state. Schema 2 is indeterminate
because it lacks permission evidence. For schema 3, status checks source and candidate content,
file modes and declared parent-directory modes, uses the final recheck as the latest quality
evidence, and binds
the returned patch to its delivery record. Status does not inspect the coordinator database;
replay checks it separately. The report format remains schema 1 and adds
`state_schema_version`, `delivery`, `candidate_sha256`, and a validated `diff` when applicable.

## Read the result

Start with these fields:

- `state`, `phase`, `active`, and `reason`: what the run recorded and whether a stage was still active.
- `budgets`: repairs and agent calls used, their limits, and what remains.
- `deadline` and `time_remaining_seconds`: the recorded deadline and a clamped countdown. This is informational; inspection never changes run state.
- `quality` and `verifier`: latest completed stage summaries found in the evidence records.
- `freshness.state` and `freshness.issues`: whether checked file hashes still match, or what prevented a stable inspection.
- `historical`: true whenever freshness is not `current`.
- `acceptance`: always `not_accepted`. Matching hashes are consistency checks, not authenticated proof or production acceptance.

### Stable blocked run — exit 0

A blocked run can still be inspected successfully if it has no active unfinished stage and its evidence is current. For example:

```json
{
  "state": "blocked",
  "phase": "builder",
  "active": null,
  "reason": "repair limit reached",
  "budgets": {
    "repairs": {"used": 2, "limit": 2, "remaining": 0},
    "agent_calls": {"used": 4, "limit": 6, "remaining": 2}
  },
  "freshness": {"state": "current", "issues": []},
  "historical": false,
  "acceptance": "not_accepted",
  "classification": "hash_consistency_only"
}
```

This says the saved record is internally consistent; it does not say the task passed. The example is abbreviated to show the fields people usually inspect.

### Changed evidence — exit 2

If a declared file changes after the run, the report keeps the recorded outcome but marks it historical:

```json
{
  "state": "completed_advisory",
  "freshness": {
    "state": "stale",
    "issues": ["hash changed or file missing: app.py"]
  },
  "historical": true,
  "acceptance": "not_accepted"
}
```

A completed outcome remains visible as what was recorded at the time. Do not interpret stale evidence as a current pass.

### Active, changing, or unreadable record — exit 2

A recorded active stage or state changing during inspection is `indeterminate`. Unsafe paths, symlinks, malformed JSON, missing required run state/plan/result files, or invalid evidence are `unreadable`. A missing previously hashed project file is `stale`; an absent declared editable file can be current when the plan records it as prospective. Read `freshness.issues` for the specific explanation. Exit 2 means **the inspection is not a stable current result**; it does not itself cancel or repair the run.

## Exit codes

| Exit | Meaning | Next step |
|---|---|---|
| `0` | Evidence was read stably and its checked hashes are current. A blocked or cancelled run can return 0 when no work is active. | Use the recorded state and summaries to decide what a person should inspect or do next. |
| `2` | Evidence is stale, indeterminate, unreadable, or the command arguments are invalid. JSON includes the freshness explanation. | Inspect `freshness.issues`; reconcile active or uncertain work before any resume attempt. |

An elapsed deadline alone does not rewrite an already completed run or make matching evidence stale. The report clamps remaining time to zero and preserves the recorded outcome.

## Run the checks

From the Proof Loop checkout:

```sh
python3 -m unittest discover -s tests -v
python3 pilot/acceptance.py
```

Historical project records include regression and protected acceptance checks for this implementation. Before relying on a specific build, run the checks on that exact source snapshot. These checks do not turn an advisory agent result into production acceptance.

## Important boundary

The status reader checks consistency of the saved plan, declared files, completed records, and nested evidence. It does not authenticate who or what produced those files, prove general correctness, or accept a production change. Every report preserves `acceptance: not_accepted`. For the full protocol and evidence details, see [Native agent execution bridge](AGENT-BRIDGE.md#inspect-without-resuming).
