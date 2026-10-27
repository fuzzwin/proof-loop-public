# Roadmap

Proof Loop is developed as a generic workflow. Project-specific source code, adapters, acceptance tests, runtime locks and credentials belong with their owning projects. Keep operational run state, real fixtures and private evidence outside source distributions.

## Current capabilities

- Portable intake, run cards, receipts and built-in source/view drift validation.
- Deterministic orchestration for bounded synthetic inspect/repair/recheck flows.
- An opt-in local Codex execution bridge with separate specialist, builder and verifier sessions.
- Read-only status inspection for saved agent runs.
- General task contracts with typed check adapters, external-tool and local agent-host
  protocols, and separate verification and validation results; see [harness guide](HARNESS.md).

## Open work

1. Qualify additional external adapters and providers on real tasks using the general harness protocols.
2. Improve cross-platform evidence while keeping platform claims tied to actual checks.
3. Establish externally controlled acceptance pins and reviewer authority for real projects.
4. Validate host permissions and runtime behavior for each supported integration.
5. Consider release automation only after source, evidence, permissions and review gates are established.

Roadmap items are proposals. They do not imply that adapters, host activations, automation or production acceptance are currently available.
