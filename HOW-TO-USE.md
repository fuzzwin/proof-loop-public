# How and when to activate the workflow

This entry guide uses the 0.2.2 CLI source. [HOW-TO.md](HOW-TO.md) retains the detailed receipt/pin procedure. No command from a separate Windows implementation is assumed here.

## 1. Agree the scope before execution

Name the project/source commit, exact input and test files, runtime, permitted writes, done criteria and what is out of scope. Prefer an isolated copy when testing an existing app. Preserve dirty work and live deployment. Freeze acceptance tests before the builder starts; revised scope needs a new approved card, not edits to old evidence.

Keep this toolkit and your project separate. Create or choose a project directory, provide only the files you intend to check, and write a task description with the desired result, source/evidence paths, acceptance criteria, constraints, and allowed writes. Do not include credentials. Use project-relative paths for declared inputs; `.proof/` is created in that project when you initialize it.

Run `python3 proofloop init your-project` from the toolkit folder (replace `your-project` with your chosen directory). Then use `--root your-project` on subsequent commands. For a task beyond JSON drift, supply your actual checker output as Reported evidence; it does not gain a supported adapter simply by being named. Use the detailed guide for receipt fields.

For Codex, preserve existing instructions and review and merge [project instructions](templates/codex/AGENTS.md) and the [intake skill](templates/codex/.agents/skills/proof-loop-intake/SKILL.md) into the target project. Ask it to create the card, perform bounded work, retain logs, obtain the required fresh verification and run receipt validation. Installation of these instructions alone does not activate a trusted runner or prove they were loaded.

For GPT, supply the [portable handoff](templates/gpt/PROOF-LOOP-GPT-HANDOFF.md), Run Card and relevant material. Ask for an evidence-labelled draft and uncovered scope. A local operator or connected execution capability must run the actual checks and validator. No shared local backend, hooks, subscription/API coverage or unseen local file access is implied.

## 2. Try an advisory synthetic check

For an automatically cleaned first try, run `python3 tools/clean_start_smoke.py`. The longer manual example below leaves local `.proof/` state in the synthetic project for inspection.

These commands are supported by this version. Run them from the core checkout; use a new run ID if the example already exists.

```sh
python3 proofloop init examples/synthetic-project
python3 proofloop validate-registry --root examples/synthetic-project
python3 proofloop start --root examples/synthetic-project \
  --run-id guide-advisory --advisory --risk low --playbook build \
  --goal "Compare the declared synthetic source and view" \
  --scope "Read synthetic JSON and write local evidence only" \
  --use-capability python-stdlib --check source-view-drift \
  --acceptance-test fixtures/acceptance_contract.json \
  --input fixtures/records.json --input reports/result.json \
  --done "Record count and ordered IDs match"
python3 proofloop drift --root examples/synthetic-project \
  --run-id guide-advisory --source fixtures/records.json --view reports/result.json \
  --log .proof/runs/guide-advisory/logs/source-view-drift.json
python3 proofloop status --root examples/synthetic-project --run-id guide-advisory
python3 proofloop resume --root examples/synthetic-project --run-id guide-advisory
```

The drift command returns 0 for a match and 1 for divergence; input/path errors return 2. A passing check in this advisory run is evidence only: the run cannot validate as accepted. This source/view check demonstrates projection consistency, not arbitrary game or document correctness.

## 3. Use acceptance mode only with its trust prerequisites

Follow [the existing acceptance procedure](HOW-TO.md): retain a reviewer-controlled pin outside the author's writable project, start without `--advisory`, and have the reviewer approve the exact Run Card hash. Complete the receipt from current evidence, then run:

```sh
python3 proofloop validate --root /path/to/project --run-id RUN_ID \
  --baseline-pin /separate/reviewer-controlled/acceptance-pin.json
```

Exit 0 is a scoped accepted receipt; exit 2 means blocked/invalid. A named check does not install an adapter. Current app/Node/PDF results remain Reported when no supported adapter can recompute them. Never change an unknown runner label or rewrite failure/expiry/environment fields to force acceptance. Rerun the proper check instead.

## 4. Resume or stop honestly

Inspect `status` and `resume` after interruption. Fresh inputs alone do not prove a valid receipt; coverage, outcomes and trust prerequisites still matter. Check for a live writer before recovering a stale lock.

```sh
python3 proofloop close --root /path/to/project --run-id RUN_ID \
  --state blocked --reason "Required app adapter is not available"
```

Use `repair` only within the task's approved two-round budget, then escalate an unresolved blocker. Record actual commands, exits, current input/output hashes, runner attribution and limitations. Do not substitute a confidence number for evidence coverage. [Release](RELEASE-CHECKLIST.md) and [CI](CI-PROPOSAL.md) have separate gates.




## Agent execution

For an explicitly authorized task and an installed signed-in Codex host, see the [native agent bridge](AGENT-BRIDGE.md) and [run status guide](AGENT-STATUS-GUIDE.md). It launches separate worker and verifier sessions and loops only within declared budgets. It does not wake itself, schedule future work, or replace portable acceptance requirements.
