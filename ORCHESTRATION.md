# Integrated orchestration: bounded first host

The portable core integrates with a durable coordinator and an executable, deterministic synthetic worker. This path runs only when explicitly invoked; it provides bounded local orchestration and advisory evidence.

For native model-worker execution, see [the agent execution bridge](AGENT-BRIDGE.md). The deterministic synthetic path below remains available without model calls.

## Run an actual loop

Use an installed Python 3.10+ runtime. No dependencies, keys or network are needed. The following shell example uses POSIX copy commands and a temporary path. On another platform, copy the folder to a new writable directory and substitute its path. Copy the example so the original failure fixture stays intact:

```sh
cp -R examples/orchestration /tmp/proof-loop-demo
python3 proofloop orchestrate --root /tmp/proof-loop-demo --run-id demo --plan plan.json
```

The loop does real work within this scope:
1. Freeze the source, initial view, plan, acceptance file and explicit repair permission.
2. Claim the inspection task; create a core advisory Run Card and execute its drift runner. Record the failing log without rewriting it.
3. Claim the authorized repair after checking dependencies and fresh inputs. Rebuild only the declared synthetic JSON projection. Record the repair round. Supersede, rather than rewrite, the old input baseline.
4. Create a new advisory Run Card for the repaired inputs. Execute and recompute the core drift check.
5. Return `completed_advisory`, always with `acceptance: not_accepted`. Stage success means an artifact was produced, not that an arbitrary claim passed independent acceptance.

The demo intentionally starts with drift. A successful final check proves only record count, ordered IDs and fixture classification. It does not prove source truth, general app correctness, hardware fit or security.

### Resume and replay

```sh
python3 proofloop orchestrate --root /tmp/proof-loop-demo --run-id demo --plan plan.json --max-steps 1
```

For a fresh copy this runs one stage. Run the same command again to execute the next stage, or omit `--max-steps` to finish. Each completed checkpoint is durable. Replay rechecks stage artifacts, nested core logs and frozen core control files; the final passing drift evidence is recomputed. The superseded failed check is hash-checked, not incorrectly recomputed against repaired inputs. Repeating a completed run checks freshness and returns its state without rerunning writes. It does not count as a new run. Failed or cancelled stages return `blocked` and CLI exit 2; they are not resumable checkpoints. Inspect their recorded outcome and explicitly prepare a new run when appropriate. Projection matching and repair use exact canonical JSON comparison: booleans cannot stand in for numbers, including nested ID values.

If interrupted inside a stage, the durable reservation remains running or uncertain and retains its writer resource. This runner does not steal the claim, assume elapsed time proves failure, or repeat an uncertain write. Inspect the dispatch, actual bytes and evidence, then use the coordinator's explicit recovery/reconciliation operations. A queued result after `not_executed` requires real evidence that execution did not happen. If the view already changed, preserve it and the evidence and use an explicitly reviewed new run. A crash between view write and result commit is deliberately blocked, not transparently resumed.

## GPT/Codex host integration contract

The coordinator is available as Python API `Coordinator.apply` and a JSON CLI:

```sh
python3 coordinator.py --db /path/to/state.sqlite apply request.json
python3 coordinator.py --db /path/to/state.sqlite show RUN_ID
```

A request has `key`, `operation`, and `args`. The host submits task contracts containing `id`, `description`, `dependencies`, `resources`, `reviewer`. It then:
- Atomically `claim`s ready work with the expected task version. Claim checks all ancestor artifact hashes, the shared three-worker ceiling and exclusive resources.
- Uses its actual authorized native worker tool, then `dispatch`es the returned dispatch reference. Do not invent a reference for a model call that never happened.
- Supplies actual output bytes, SHA256, evidence and outcome references with `result`.
- For reviewer-required tasks, invokes a distinct reviewer and supplies that review's exact artifact hash and reference with `accept`. A caller-supplied reviewer name is attribution, not authenticated independence.
- Uses `recover`/`reconcile` on uncertain outcomes instead of automatic duplicate work. Version conflicts stop the action. Reusing an idempotency key returns the historical response; independently read `snapshot` for current state.

The deterministic host in `orchestration.py` actually uses these operations, but does not dispatch GPT/Codex models. The separate `agent_bridge.py` now dispatches native Codex sessions, binds reported IDs and exact candidates, limits calls/time/repairs and stops uncertain outcomes. Its host, cancellation and review limits are documented in AGENT-BRIDGE.md. Model selection and API spending remain outside the deterministic implementation. Multiple databases do not share a global resource lock; one project must use one coordinator database.

## Learning without self-authorizing

`improvement.ImprovementGate` is a proposal-only API. A host registers an existing evaluator callable, frozen base/candidate/evaluator files, disjoint JSON regression and holdout cases (`case_id`), protected control files, hypothesis and explicit unknowns.

It executes the registered evaluator with a one-attempt budget, regression first. Holdout bytes and case identities are marked consumed before any evaluator call, even on failure. Re-encoding the same case IDs cannot recycle them. Known exposed holdouts can be registered as exposed. Regression bytes and stable case IDs become exposed at proposal registration, so they cannot later be relabelled as a fresh holdout across proposals. Regression failure rejects. Both datasets must pass and at least one must improve on the baseline to await review. Artifact/control drift, unresolved unknowns and evaluator substitution block.

The evaluator returns exact `base_passed` and `candidate_passed` booleans. These are attributed evaluator reports, not portable Verified receipts or proof that the evaluator is correct. A separate reviewer can obtain `eligible_for_human_decision` for the exact evidence hash; no method installs, promotes or activates the candidate. Reviewer names are not authentication. Holdout custody is trusted-host attestation, not OS-enforced secrecy. The file-based host can inspect data; genuine sealed holdouts require a separately controlled evaluation host. Case IDs must remain stable; maliciously relabelled copied data cannot be detected generically.

Permission, policy, acceptance and gate change kinds are rejected. Protected hashes detect declared control changes, but this is not a sandbox and cannot stop a malicious evaluator writing files. Use only trusted, authorized evaluators under the host's actual permissions. Do not import/install arbitrary GitHub code to run this API.

## Security boundaries

- Project-relative paths, symlink rejection, frozen hashes, exclusive evidence creation, optimistic versions and writer claims reduce accidental corruption and stale acceptance.
- Unsupported adapters/checks stop rather than implying security coverage.
- Evidence structure, hashes and matching opinions do not establish factual truth or independent identity.
- The deterministic path performs no external communication or installation. The opt-in native bridge uses existing Codex authentication and model communication, but does not provision credentials, install software, configure global hooks or deploy.
- External reviewer-controlled acceptance pins and exact Run Card approval remain mandatory for portable accepted receipts. This integration does not manufacture those approvals.

See [frozen acceptance](ORCHESTRATION-ACCEPTANCE.md) and [research rationale](SELF-IMPROVEMENT-SOURCES.md).

## Role separation, not majority-vote truth

A host may assign a coordinator, bounded specialist workers, a fact checker that re-reads primary evidence, a verifier that reruns relevant checks, and a final checker that reconciles scope, unresolved gaps and evidence freshness. Use extra roles where the task's consequences justify their cost; lightweight questions do not need a committee. Reviewers need genuinely separate execution/evidence access, not simply a different role name or another agreeing answer. A majority cannot turn an unsupported claim into evidence. Model choice is host-configurable and independent of the framework. No comparison here demonstrates superiority over Astra High or any other single model; that requires a controlled, matched evaluation.
