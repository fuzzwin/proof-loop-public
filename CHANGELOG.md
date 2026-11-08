# Changelog

## 0.3.0 — 8 November 2026

Releases the features below, which were previously published as unreleased source under CLI 0.2.2 and identified only by commit.

**Evidence compatibility.** Receipts and drift runner logs must record the executing CLI version, so those recorded by 0.2.2 no longer validate. Rerun the checks, or use `proofloop recheck` on a 0.2.2 run to carry its frozen criteria and repair usage into a fresh 0.3.0 run. Receipt rules and acceptance boundaries are otherwise unchanged. Agent-bridge, harness and one-request run state does not record the CLI version and is unaffected.

### Preserved rechecks and scoped skill reviews

Merged in [PR #4](https://github.com/fuzzwin/proof-loop-public/pull/4) on 30 October 2026 UTC. `proofloop recheck` freezes a new candidate while preserving criteria, prior evidence and repair usage. Changed criteria, unbound legacy cards and reset repair budgets are rejected; acceptance-required child cards need fresh external approval. Add a read-only skill packet evaluator and reusable explicit-invocation Codex workflow skill. Packet consistency, attributed case approval and portable acceptance remain separate. See [test evidence](TEST-STATUS.md) and [visual examples](FLOW-EXAMPLES.md).

### General task harness

Add versioned task contracts, separate verification and validation results,
JSON/text/unittest/external-tool check adapters, and a local command-host protocol
alongside native Codex sessions. Reuse protected snapshots, conditional
specialists, fresh verifiers, durable evidence and bounded repairs. Add checks-only
execution and read-only typed status, three domain examples and a disposable
smoke. Existing plans, core receipt acceptance and safe limits retain their behavior.

Review repairs give each check its own guarded snapshot, bind each check result to
its candidate, and require frozen unittest module and case coverage. Older draft
harness runs remain readable as unbound historical evidence and require a new run.

### Native agent bridge and one-request workflow

The bounded native agent bridge and read-only agent status command support frozen plans, protected checks, conditional specialists, builder proposals, fresh verifier sessions, bounded repairs, durable call/time limits, checkpoint replay, and uncertain-outcome stops. Status inspection reads recorded state and reports evidence freshness; it does not resume runs or change acceptance. The optional [one-request workflow](ONE-REQUEST-WORKFLOW.md) adds isolated candidates, final check reruns, exact requirement/test coverage and journaled promotion recovery; new native runs use state schema 3.

### Linux coverage and release process

Add Linux POSIX ACL regressions. The existing ACL regressions are macOS-only, and on Linux the suite stayed green with the ACL detection or clearing branch broken. Align the drift-runner-log schema, which still required runner version 0.2.1, and add release-consistency tests tying the schema, current-version guide statements and this changelog to the CLI version. Add a manual, read-only source-checks workflow, a local link checker, a security policy and the missing version, changelog and tag steps in the release checklist. See [test status](TEST-STATUS.md) for the first recorded Linux results.

## 0.2.2 — independent-review corrections

Correct false-pass cases involving specially named inputs and JSON boolean/numeric comparisons. Reject malformed receipt fields with blocked reports. Treat failed and cancelled orchestration stages as blocked. Rerun older checks and create fresh receipts under the corrected version.

## 0.2.1 — scoped CLI corrections

Record the runner schema, version, and semantic exit code. Require the validator to recompute a genuine pass with consistent state and exit code zero. Older logs must be rerun.

## 0.2.0 — withdrawn for acceptance

A genuine failed drift runner log could be promoted to an accepted pass through receipt edits. Do not use 0.2.0 receipts for acceptance.
