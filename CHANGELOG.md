# Changelog

## Unreleased — general task harness

Add versioned task contracts, separate verification and validation results,
JSON/text/unittest/external-tool check adapters, and a local command-host protocol
alongside native Codex sessions. Reuse protected snapshots, conditional
specialists, fresh verifiers, durable evidence and bounded repairs. Add checks-only
execution and read-only typed status, three domain examples and a disposable
smoke. Existing plans, core receipt acceptance and safe limits retain their behavior.

## Current source candidate

The current source includes the bounded native agent bridge and read-only agent status command. The bridge supports frozen plans, protected checks, conditional specialists, builder proposals, fresh verifier sessions, bounded repairs, durable call/time limits, checkpoint replay, and uncertain-outcome stops. Status inspection reads recorded state and reports evidence freshness; it does not resume runs or change acceptance.

The portable CLI identifies as 0.2.2. This source candidate has no release tag.

## 0.2.2 — independent-review corrections

Correct false-pass cases involving specially named inputs and JSON boolean/numeric comparisons. Reject malformed receipt fields with blocked reports. Treat failed and cancelled orchestration stages as blocked. Rerun older checks and create fresh receipts under the corrected version.

## 0.2.1 — scoped CLI corrections

Record the runner schema, version, and semantic exit code. Require the validator to recompute a genuine pass with consistent state and exit code zero. Older logs must be rerun.

## 0.2.0 — withdrawn for acceptance

A genuine failed drift runner log could be promoted to an accepted pass through receipt edits. Do not use 0.2.0 receipts for acceptance.
