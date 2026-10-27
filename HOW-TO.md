# How to use Proof Loop 0.2.2

**Version 0.2.2 source:** older 0.2.0 and 0.2.1 checks have known false-pass cases. Rerun existing checks and produce fresh receipts with 0.2.2; do not edit old version/status fields.

Proof Loop is a project-local workflow. Start each request with a Run Card, freeze declared inputs and acceptance files, perform the scoped work, collect reproducible evidence, request a fresh independent review when the risk tier requires it, and run the receipt validator before treating the result as accepted.

## Quick start

From this repository directory, confirm the CLI and initialize synthetic fixtures:

```sh
python3 proofloop --version
python3 proofloop init examples/synthetic-project
python3 proofloop validate-registry --root examples/synthetic-project
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

This repository contains synthetic source/view fixtures, not a preaccepted runtime receipt. Tests create fresh isolated fixtures and synthetic pins. A synthetic or same-user pin is a consistency demonstration only. For a real project, follow Sections 1–5 and have a reviewer/operator retain the acceptance pin outside the author's writable project.

**Codex with a local runtime** can execute the documented commands when the active runtime grants access to the project; its actual permissions and OS sandbox govern execution. **GPT without a connected code or file capability** can only advise on user-provided Run Cards, files, and outputs. It cannot inspect local files, run the CLI, invoke local hooks, or attest to the hashes. Treat GPT-produced receipts as drafts and validate them locally with the CLI.

## 1. Initialize a project

From the bundle directory:

```sh
python3 proofloop --version
python3 proofloop init /path/to/project
```

This creates `.proof/BRIEF.md`, `.proof/capabilities.json`, `.proof/manifest.json`, a build playbook, and run directories. It does not alter global configuration or overwrite existing starter files. If `.proof/` already exists, inspect it first; `--force` only fills missing starter files.

Review the capability registry. Each capability needs an explicit `policy` (`allow`, `deny`, or `review`) and a lifecycle status. `start --use-capability X` fails for unknown, denied, review-needed, or insufficiently tested capabilities; exclusions and decisions are persisted on the Run Card. The CLI applies policy then lifecycle only. Fit, `use_when`, `not_for`, health-check, and ranking fields are descriptive; they are not evaluated or executed.

## 2. Create a Run Card and frozen baseline

For author/reviewer separation, have a reviewer or operator who controls a location outside the project create and retain a pin for the acceptance files. The pin format is JSON with `schema_version: 1` and an `acceptance_tests` object keyed by project-relative path. Each value contains the file's SHA-256 and byte length, for example:

```json
{"schema_version":1,"acceptance_tests":{"fixtures/acceptance_contract.json":{"sha256":"<acceptance-file SHA-256>","bytes":123}}}
```

Keep the pin under separate reviewer control; if the author can rewrite the pin, this author-separation property does not hold. `start` checks acceptance files against the external pin and creates the Run Card. Before an acceptance-required receipt can pass, a separate reviewer must inspect that card and add `approved_run_card_sha256` with the SHA-256 of the exact `run-card.json` bytes to the externally controlled pin. Compute the value with `python3 -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path(".proof/runs/RUN_ID/run-card.json").read_bytes()).hexdigest())'` from the project, then have the reviewer record it in the pin. Validation checks that approval on every run, so changing risk, scope, required checks, or other card fields invalidates approval. The CLI does not sign the pin or enforce operating-system ownership. For a single-user synthetic pilot, a locally generated pin and card approval are consistency demonstrations only.

For low-risk exploratory work without a separately controlled pin, use `--advisory` in place of `--baseline-pin`. Advisory runs can record inputs, evidence, and resumable progress, but the validator will never accept them as `passed_for_scope`. The acceptance-required mode remains the only mode that can pass validation.

```sh
python3 proofloop start --root /path/to/project \
  --run-id acceptance-example --goal "Compare a generated view with its source fixture" \
  --scope "Read declared local JSON files only" \
  --playbook build --risk low \
  --use-capability python-stdlib \
  --check source-view-drift \
  --acceptance-test fixtures/acceptance_contract.json \
  --baseline-pin /separate/reviewer-controlled/acceptance-pin.json \
  --input fixtures/records.json --input reports/result.json \
  --done "The projected IDs and count match"
```

`start` validates declared inputs, acceptance files and capability policy decisions before writing. A required check name records a gate; naming a check does not install or validate an adapter. It records a Run Card, input manifest, receipt starter, separate acceptance-baseline record, and ledger event. Failed validation does not reserve the run ID. Run IDs accept only letters, digits, hyphen, and underscore; all run paths are checked for containment and symlinks. The run remains unapproved until the external pin carries the reviewer-approved Run Card hash.

Advisory start example (low-risk exploration only; cannot produce an accepted receipt):

```sh
python3 proofloop start --root /path/to/project --run-id advisory-example --goal "Explore a synthetic fixture" \
  --scope "Read declared local fixture files" --risk low --advisory \
  --check source-view-drift --acceptance-test fixtures/acceptance_contract.json \
  --input fixtures/records.json --input reports/result.json --done "Record evidence"
```

The local baseline and hash chain detect ordinary inconsistent edits. The independent pin is the trust anchor for frozen acceptance tests; it must stay outside the author's writable project and be controlled by a reviewer/operator. Without separate custody, the CLI cannot prevent a same-user from rewriting every trust file.

## 3. Run the built-in evidence check

Version 0.2.2 can currently validate only the built-in `source-view-drift` check as Verified evidence. It reads JSON source and generated-view files and writes a new, non-overwriting runner log:

```sh
python3 proofloop drift --root /path/to/project --run-id RUN_ID \
  --source fixtures/records.json --view reports/result.json \
  --log .proof/runs/RUN_ID/logs/source-view-drift.json
```

The check must have been declared on the Run Card. The validator later recomputes the expected projection from the current source and view, verifies the recorded hashes and run/check identity, and compares the log fields. A log with a made-up runner label cannot pass. External tool output may be recorded as Reported, but no external runner adapter is implemented yet; do not label it Verified or treat its self-declared PASS as accepted.

## 4. Complete the receipt

Edit `.proof/runs/RUN_ID/receipt.json` only after recording the evidence. Every required check needs a state and nonempty reason. To record the built-in check as Verified, use the exact structured form in `RECEIPT-SCHEMA.md`, pointing to the matching runner log. The allowed Verified wording is exactly `Check source-view-drift passed.`; broader claims must be Reported, Inferred, or Unknown. Set `coverage.checked` to the fields in the runner log (`record_count`, `ordered_ids`, `fixture_classification`) and list uncovered scope in `coverage.not_checked`.

Add hashes for at least one declared output under `outputs`. The validator recomputes declared input and output hashes, receipt/manifest/baseline hashes, all required checks, checker version, environment, and expiry. Receipts are local editable JSON; the hashes are consistency checks, not signatures.

Medium/high/critical risk needs a fresh independent review; high/critical also needs `human_decision: "approved"`. Reviewer name is attribution only; it does not prove fresh context or independence. Any fail, blocked, or not-run check prevents acceptance. Not-applicable checks need a reason, and at least one recomputable built-in check must pass.

## 5. Validate, resume, or stop

```sh
python3 proofloop validate --root /path/to/project --run-id RUN_ID \
  --baseline-pin /separate/reviewer-controlled/acceptance-pin.json
python3 proofloop status --root /path/to/project --run-id RUN_ID
python3 proofloop resume --root /path/to/project --run-id RUN_ID
```

Validation exits 0 only for a passing acceptance-required receipt with the external acceptance pin and its reviewer-approved Run Card hash. Advisory runs remain non-acceptance records and validation refuses any attempt to mark them `passed_for_scope`. It also refuses mismatched or unknown evidence, undeclared drift source/view files, stale inputs/outputs/baselines, mismatched external pin/card approval, invalid metadata, expiry, unsafe paths, and unsupported Verified text. The CLI serializes local writers with `.proof.write.lock`; updates use atomic writes and a compare-and-swap check of the receipt bytes. A stale concurrent writer is rejected. If a process crashes leaving the lock, confirm that no writer is running before removing it.

Record repair rounds:

```sh
python3 proofloop repair --root /path/to/project --run-id RUN_ID --reason "Correct the output schema"
```

The first two calls record rounds 1 and 2. A third call records `blocked` and exits 3; no third repair begins. `close` with `--root`, `--run-id`, `--state` (cancelled, superseded, failed or blocked), and `--reason` records a terminal non-pass state. `proofloop observe` stores user-reported invocation/spend observations; the CLI does not pretend to enforce budgets.

## Codex setup (project scope)

Copy `templates/codex/AGENTS.md` to the project root and `templates/codex/.agents/skills/proof-loop-intake/` into the project. Review and adapt these opt-in instructions. They ask Codex to consult `.proof/`, produce a Run Card, and finish with explicit local receipt validation. No hooks or agents are installed by this bundle.

Optional Codex hooks or custom-agent TOML must be checked against the installed version's official docs before project-level adaptation. Hooks are best-effort helpers: SessionStart loads context; PreToolUse coverage is incomplete; PostToolUse cannot undo side effects; Stop is a continuation prompt, not a hard receipt gate; invalid hook responses can fail open. Hooks do not create an OS security boundary. Use `proofloop validate` as the receipt gate and runtime permissions/OS sandbox for actual execution isolation.

See `HOST-COMPATIBILITY.md` for host and hosted-context boundaries.

## GPT setup (portable handoff)

Use `templates/gpt/PROOF-LOOP-GPT-HANDOFF.md` as an instruction block in a GPT that supports user-provided instructions, or paste it at task start. Provide only the Run Card, relevant files, check output, and necessary context. Ask GPT for a draft receipt, coverage list, and blockers.

GPT cannot independently inspect files on your Mac, run local commands, invoke local hooks, or verify local hashes unless a connected capability explicitly provides that action and returns evidence. Do not claim GPT ran local tests. Copy its receipt into the project and run `proofloop validate` locally. Do not assume local Codex instructions, files, agents, hooks, or skills are transferred to ChatGPT Work. There is no shared backend implied; a subscription does not cover API spend, and a sleeping computer runs no local work.

## Pilot and safety boundaries

The included project is synthetic. The two-week target of at least 50% lower median handling time is prospective and not achieved. Measure actual baseline and Proof Loop effort prospectively; do not claim a time reduction until it has been demonstrated.

The OS sandbox track is separate. A read-only pilot can proceed without it, but arbitrary code execution is not contained until the exact runtime/OS passes `SANDBOX-ACCEPTANCE.md`. Windows remains unverified. No external tools, credentials, network access, installs, global settings, hooks, or automations are included.
