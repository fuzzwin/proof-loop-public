# Validation status

Proof Loop separates deterministic checks, reported external results, and human acceptance. A passing software check does not establish that a host integration ran, a reviewer was independent, or production requirements were met.

| Area | Current scope | Separate evidence still required |
|---|---|---|
| Portable CLI | Recomputes the built-in JSON source/view drift check | External reviewer-controlled acceptance pin and exact Run Card approval |
| Agent bridge | Opt-in local Codex sessions with bounded calls, repairs, and time | Host-specific acceptance and independent review of each candidate |
| One-request fixes | Project-owned scope/checks, isolated repairs, fresh verification and a final check rerun produce a diff or blocker | Project setup, meaningful checks and sufficient context; local verification is separate from portable acceptance |
| Agent status | Read-only inspection of recorded run data and evidence freshness | Freshness can become indeterminate while a stage is active or state changes |
| Orchestration | Deterministic synthetic inspect/repair/recheck workflow | It does not dispatch language models or create OS isolation |
| External tools | Their outputs can be retained as reported evidence | A dedicated runner and independent validation before Verified status |

Synthetic pins establish consistency only. Matching file hashes are consistency checks, not authenticated proof. Advisory completion is not production acceptance.
