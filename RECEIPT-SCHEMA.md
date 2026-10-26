# Proof receipt schema, version 3

Each run has `.proof/runs/<run-id>/receipt.json`. `.proof/` is an index and evidence record, not a master for project data. A receipt is accepted only when `proofloop validate` exits 0.

## Deterministic acceptance requirements

- Run ID matches `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` and selects a non-symlink run directory contained beneath `.proof/runs`.
- The receipt, Run Card, and run manifest are regular files, not symlinks.
- `checker_version` exactly matches the running CLI version. `environment` contains nonempty `python` and `platform` strings and must match the current validator runtime. These fields identify an environment; they do not prove isolation.
- Acceptance-required `start` and `validate` require a `--baseline-pin` JSON file. It must be outside the project and match every acceptance path, SHA-256, and byte count. After `start`, a separate reviewer must add `approved_run_card_sha256` for the exact Run Card bytes to that pin. The CLI checks this approval at validation, binding scope, risk tier, review mode, and required checks. Reviewer/operator custody is required; the CLI does not enforce OS ownership or sign the pin.
- `baseline_pin_sha256` binds the canonical acceptance-tests map rather than the whole pin file, allowing the reviewer to add the post-start Run Card approval without changing the acceptance baseline digest. Validation separately checks the Run Card approval field.
- Low-risk exploratory work may use `--advisory` instead. Advisory runs are progress/evidence records only: the validator rejects any advisory receipt marked `passed_for_scope`. Acceptance-required mode with the external pin is the only mode that can pass.
- The separately stored acceptance-baseline file must match its hash-chained `.proof/baselines/history.jsonl` record. The run manifest and receipt acceptance digest must match that baseline. The baseline file is created exclusively and marked read-only.
- Inputs, accepted baseline, and declared outputs must be unchanged and project-relative. Any symlink component is rejected.
- Every required Run Card check has a state and nonempty reason. `fail`, `blocked`, and `not_run` prevent acceptance. `not_applicable` requires a reason. At least one required check must pass through a recomputable built-in runner.
- The only Verified check currently implemented is `source-view-drift` using `proofloop-drift`. Validation recomputes its source projection, checks input hashes, and compares the actual view. A receipt pass requires a recomputed pass, log state `pass`, and integer `exit_code: 0`. Log schema version 2 and `runner_version: 0.2.2` are required. Genuine failed evidence remains failure and cannot support a Verified pass. Older logs must be rerun, not relabeled. Unrecognized runners cannot be promoted to Verified by writing matching labels and hashes.
- Every Verified claim must use the exact structured claim form `{"label":"Verified","claim_type":"check_pass","check":"source-view-drift","supports_checks":["source-view-drift"],"claim":"Check source-view-drift passed.","evidence":["<matching log path>"]}`. Broader statements must be Reported, Inferred, or Unknown.
- `coverage.checked` must exactly equal coverage declared by validated passing runner evidence. It cannot include an arbitrary user-provided claim.
- Medium/high/critical risk follows the independent-review requirement in the Run Card. Reviewer identity remains attribution, not proof of independence. High/critical risk also requires `human_decision: "approved"`.
- Receipts expire; rerun checks when an input or output changes.

## Baseline and writer model

The CLI uses an exclusive project lock at `.proof.write.lock`, unique temporary files, atomic replacements, SHA-256 compare-and-swap preconditions, exclusive creation for evidence, and a hash-chained baseline history. Concurrent CLI writers that read the same prior receipt cannot both overwrite it; the stale writer is refused. A crashed process may leave a lock file; inspect that no writer remains before removing it.

These are safeguards against accidental edits and ordinary concurrent CLI use. The external pin and Run Card approval provide an author/reviewer trust anchor only when the reviewer/operator controls the pin independently. If the author can rewrite that pin too, the CLI cannot prevent coordinated edits. It has no signing key or protected server.

## Runner and claim trust limits

The CLI currently recomputes only its built-in drift check. It rejects user-written logs attributed to an unknown runner and constrains Verified language to the exact check-pass statement. It does not cryptographically prove which process wrote a log; a same-user actor with full local file control can fabricate all inputs, logs, and manifests. No external command logs are accepted as Verified until a versioned, tested runner adapter is added.

`Reported` requires a source, `Inferred` a basis, and `Unknown` an open question plus owner. The validator checks structure and declared evidence relationships, not the truth of arbitrary prose. No confidence percentage is provided.

Projection comparison is exact after canonical JSON serialization: object key order and whitespace are ignored, but boolean/numeric values and integer/decimal representations remain distinct. This applies recursively to IDs and to stored expected/actual runner evidence. Freshness checks keep input and acceptance-test paths in separate namespaces, so literal filenames beginning with `[acceptance-test] ` are safe. The internal `current_input_state.current` map is grouped by `inputs` and `acceptance_tests`; CLI `input_fresh` and `changed_inputs` fields are unchanged.
