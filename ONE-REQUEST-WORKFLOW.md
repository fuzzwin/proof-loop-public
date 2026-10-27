# Describe a fix once

Configure a project's permitted files and checks once, then give each fix in one request:

```sh
python3 proofloop fix --root /path/to/project \
  --request "Fix the empty-search crash while preserving existing search results."
```

Proof Loop runs the checks, sends failures or verifier findings back through bounded repairs,
starts a fresh verifier for the actual candidate, and reruns checks before returning a patch.
You do not relay messages between workers. A missing dependency, uncertain process, changed
source, unmet requirement, or exhausted budget produces a blocker with a reason and next action.

The candidate stays in `.proof/agent-runs/RUN/candidate/`. `fix` leaves the project's source
files unchanged. Review the returned patch and apply it with your normal Git workflow.
`verified_diff` means local checks passed and a fresh model session reviewed the candidate;
formal portable acceptance still reads `not_accepted`.

## Try a disposable example

The optional agent workflow needs Python 3.10+, a POSIX host, and an installed Codex CLI
already signed in. The tested host and limits are recorded in [test status](TEST-STATUS.md).
Native calls use that account's allowance. No API key or Python package is needed.

Run these commands from this toolkit's folder:

```sh
proofloop_demo="$(mktemp -d)"
cp -R examples/order-total/. "$proofloop_demo/"
python3 proofloop profile-init --root "$proofloop_demo" --from-plan plan.json
python3 proofloop fix --root "$proofloop_demo" --run-id order-fix \
  --request "Repair the order total to meet requirements.txt and all protected tests; preserve the public functions."
```

The example deliberately contains defects in two files. Its protected checks cover rounding,
discounts, quantities, multiple order lines and empty orders. A successful run prints a patch
path under the temporary project. Inspect it, then check and apply it if desired:

```sh
# Replace PATCH_PATH with the path printed by fix.
git -C "$proofloop_demo" apply --check PATCH_PATH
git -C "$proofloop_demo" apply PATCH_PATH
```

## Configure your project once

An existing reviewed plan can supply the scope and budgets through `--from-plan`. For a
simple project, specify editable files, protected checks and an approved command directly:

```sh
python3 proofloop profile-init --root /path/to/project \
  --editable app.py helpers.py --protected tests/test_app.py \
  --check-json '["{python}", "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]'
```

This creates `proofloop-project.json` without overwriting an existing file. Review it once.
List every source and command dependency; include read-only supporting files with `--context`.
The request supplies the goal. It cannot expand file permissions, select new commands, or
raise budgets. The profile cannot authorize edits to itself.

New profiles default to two repair rounds, six agent calls, 180 seconds per agent,
30 seconds per check and 600 seconds total. Profiles imported from a plan keep its limits
and optional exact Python test IDs. Skips, missing IDs and unchecked requirements cannot pass
the optional test/requirement gates. Limits stop the run rather than extending themselves.

## Inspect or repeat a run

Use `--run-id` for a stable identity and add `--json` for a structured `verified_diff` or
`blocked` result. `--request-file` accepts a project-relative UTF-8 file instead of inline text.

```sh
python3 proofloop agent-status --root "$proofloop_demo" --run-id order-fix \
  --plan .proof/tasks/order-fix/plan.json
```

Status reads both the original run format and the new isolated-candidate format without
starting Codex, opening the coordinator database, acquiring a writer lock, or writing files.
It reports stale or uncertain evidence explicitly; matching hashes are consistency evidence.

Repeat `fix` with the exact request, run ID and profile to retrieve a completed result without
new agent calls or file changes. Changed requests, source, profiles or evidence invalidate
reuse. An omitted run ID starts a fresh task. Partial intake and unfinished stages are retained
for inspection. Never clear locks or active state merely to force a retry.

The older `agent-run` entry point still supports applying a verified candidate, with explicit
journal recovery through `agent-recover --finish-promotion`. See [the bridge guide](AGENT-BRIDGE.md).

To route ordinary chat requests through `fix`, review and merge the optional
[Codex project instructions](templates/codex/AGENTS.md), setting the toolkit path and profile
for that project. Setup does not automatically activate other chats or install a background
service. Local evidence can contain task data and is never uploaded automatically.
