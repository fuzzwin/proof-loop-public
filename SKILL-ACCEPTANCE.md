# Scoped skill behavior review

`tools/skill_acceptance.py` checks a frozen case contract, candidate and evidence
files, then checks an optional external reviewer approval of the exact packet.
It reads files only: it does not dispatch agents, run tests, write files or
approve its own observations. It does not change `proofloop validate` or expand
that validator's recognized runners.

Use this to prepare a reviewable acceptance decision for declared behavioral
cases. A reviewer must judge the actual observed behavior against the criteria;
matching hashes and a producer's `outcome: pass` do not establish that judgment.

## Freeze before execution

Keep the contract and requirements outside the builder's editable scope. Each
file binding has a project-relative path and lowercase SHA-256 of its raw bytes.
Freeze the contract's own raw-byte SHA-256 under separate operator/reviewer
control. The command requires it explicitly; it never silently regenerates it.

Example `contract.json` (replace all digest placeholders before use):

```json
{
  "schema_version": 1,
  "skill": {"path": "skill/SKILL.md", "sha256": "<skill SHA-256>"},
  "requirements": [{"path": "requirements.md", "sha256": "<requirements SHA-256>"}],
  "cases": [{
    "id": "read-only",
    "prompt": "Read notes.txt without changing the project.",
    "criteria": ["Report the actual content.", "Leave the project unchanged."]
  }]
}
```

Exercise the frozen cases in fresh sessions, preserve actual tool output and
before/after state, and record observations. A minimal observation file is:

```json
{
  "schema_version": 1,
  "contract_sha256": "<frozen contract SHA-256>",
  "candidate_sha256": "<skill SHA-256>",
  "cases": [{
    "id": "read-only",
    "outcome": "pass",
    "artifacts": [{"path": "evidence/read-only.json", "sha256": "<artifact SHA-256>"}],
    "notes": "Observed content and before/after snapshot; limits are in the evidence."
  }]
}
```

All cases must appear exactly once, have a passing recorded outcome, notes and at
least one bound artifact. Failed, missing or malformed evidence produces
`needs_evidence`, exit 2. There are at most 32 cases and each file is at most
1 MiB. Unknown fields, duplicate keys, invalid versions, symlinks and parent or
absolute project paths are rejected. Required artifacts can still be incomplete
or misleading; the reviewer must inspect their content and coverage.

```sh
python3 -B tools/skill_acceptance.py --root PROJECT \
  --contract contract.json --observations observations.json \
  --contract-sha256 FROZEN_CONTRACT_SHA256
```

An internally consistent packet returns `ready_for_review`, exit 0, with a
`packet_sha256` binding the raw-byte hashes of the contract, observations,
candidate, requirements and artifacts. Exit 0 means the inspection is consistent;
it does not mean an independent reviewer approved it. Changed files invalidate
the packet and any approval. Rerun the relevant cases after candidate changes.

## Separate reviewer decision

A separately controlled reviewer inspects requirements, frozen prompts/criteria
and raw evidence, then keeps this decision outside the project:

```json
{
  "schema_version": 1,
  "packet_sha256": "<exact packet SHA-256>",
  "reviewer": "<attributed reviewer>",
  "decision": "approve",
  "scope": "declared_cases"
}
```

Pass its path with `--reviewer-approval /separate/reviewer/approval.json`.
The state becomes `reviewer_approved_for_cases` only if approval and current
packet match. This is an attributed operator decision for these cases, not
recomputable proof of an agent's semantics, independence or future performance.
`custody: unverified` is always explicit: the tool does not authenticate the
reviewer or enforce separate OS ownership. A same-user synthetic approval proves
only consistency and exercises the protocol.

Every state retains `portable_acceptance: not_accepted`. A portable accepted
receipt still needs its own supported recomputable runner, separately controlled
acceptance pin, exact Run Card approval, complete fresh receipt and successful
`proofloop validate`. This packet does not provide a new portable Verified runner.
General acceptance of a skill across arbitrary tasks additionally needs a
representative independently chosen evaluation set and evidence of runtime
behavior; this bounded review cannot establish that claim.
