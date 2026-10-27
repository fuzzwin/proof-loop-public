# Describe a fix once

Configure a project's permitted files and checks once, then give each fix in one request:

```sh
python3 proofloop fix --root /path/to/project \
  --request "Fix the empty-search crash while preserving existing search results."
```

Proof Loop runs the checks, sends failures or verifier findings back through bounded repairs,
starts a fresh verifier for the candidate, reruns checks, and applies the patch in a disposable
copy before returning it. The copy must match the candidate's declared content and modes.
You do not relay messages between workers. A missing dependency, uncertain process, changed
source, unmet requirement, or exhausted budget produces a blocker with a reason and next action.

The candidate stays in `.proof/agent-runs/RUN/candidate/`. `fix` leaves the project's source
files unchanged. Review the returned patch and apply it with your normal Git workflow.
`verified_diff` means local checks passed, a fresh model session reviewed the candidate,
and the patch matched it in a disposable application. This binds declared file contents,
file modes, and declared parent-directory modes. It does not bind timestamps, ownership,
ACLs, inode identity, external services or undeclared filesystem state. Checks must not
depend on those unbound properties. Formal portable acceptance still reads `not_accepted`.

## Try a disposable example

The optional agent workflow needs Python 3.10+, Git, a POSIX host, and an installed Codex CLI
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
# Replace PATCH_PATH and APPLY_UMASK with the values printed by fix.
# Use a subshell so the permission setting does not affect your terminal.
(umask APPLY_UMASK; git -C "$proofloop_demo" apply --check PATCH_PATH)
(umask APPLY_UMASK; git -C "$proofloop_demo" apply PATCH_PATH)
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
Every declared noneditable file and every declared file's parent directories must already
exist. Prospective files are allowed only in editable scope. Paths containing control
characters, reserved control-directory aliases or special permission bits are unsupported.
Use a canonical project root rather than a symlink.

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

The result prints `apply_umask` in JSON and states the same setting in text output. Use it
when applying the patch and verify the resulting modes in your target environment. If Git
cannot reproduce all changed file modes with a single umask, delivery stops with a blocker.

Repeat `fix` with the exact request, run ID and profile to retrieve a completed result without
new agent calls or file changes. Changed requests, source, profiles or evidence invalidate
reuse. An omitted run ID starts a fresh task. Partial intake and unfinished stages are retained
for inspection. If saved run state or coordinator evidence is missing, replay blocks instead
of dispatching again; inspect the preserved intake and processes before starting new work.
Never clear locks or active state merely to force a retry.

The older `agent-run` entry point still supports applying a verified candidate, with explicit
journal recovery through `agent-recover --finish-promotion`. See [the bridge guide](AGENT-BRIDGE.md).

To route ordinary chat requests through `fix`, review and merge the optional
[Codex project instructions](templates/codex/AGENTS.md), setting the toolkit path and profile
for that project. Setup does not automatically activate other chats or install a background
service. Local evidence can contain task data and is never uploaded automatically. New task
and agent-run directories are created owner-only. On macOS, inherited extended ACLs are
removed from new run directories; on Linux the bridge checks POSIX ACL attributes. Existing
run directories with group/other access or ACLs are blocked instead of silently changed.
Other ACL models and network filesystems need separate host verification.
