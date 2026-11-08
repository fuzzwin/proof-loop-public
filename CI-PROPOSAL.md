# CI proposal — manual source checks only

[`.github/workflows/checks.yml`](.github/workflows/checks.yml) implements steps 1–3 below and nothing else. It runs only when a maintainer starts it from the Actions tab (`workflow_dispatch`), optionally on an exact commit SHA or tag. It has a read-only token, no secrets, no deployment, pinned action commits, and per-job time limits. Each operating system and Python version reports as its own job. Nothing runs on push or pull request; changing that remains a separate review of permissions, budget, trusted-runner design and evidence retention.

## Suggested first pipeline

1. Run manually on an exact reviewed commit with read-only repository permissions, no secrets, no deployment, and a bounded job timeout. *Implemented.*
2. Check documentation links and the source manifest, then run the Python suite and synthetic CLI smoke. *Implemented, except for a source manifest check, which this repository does not yet define.*
3. Add platform jobs only when the actual environments are supported and their results are reported separately. *Implemented for `ubuntu-latest` and `macos-latest` with Python 3.10, 3.12 and 3.14. Windows is excluded because the native bridge is POSIX-only and no Windows qualification exists.*
4. Have a verifier with genuinely separate authority rerun or recompute evidence. *Not implemented.*
5. Allow release steps to consume only fresh passing evidence tied to the exact source and output artifact. Keep consequential release approval human-controlled. *Not implemented; the workflow publishes nothing.*

Missing identities, changed tests, stale hashes, truncated output, skipped/cancelled tests, nonzero process outcomes, deadline overruns, runner loss, and invalid receipts must block release. Preserve failed evidence and state the blocker. No success defaults, automatic gate weakening, or unbounded repair loop.

Platform-specific skips are expected and named: macOS jobs skip the Linux ACL regressions and Linux jobs skip the macOS ACL regressions. The workflow does not yet fail on other skips automatically, so a reviewer must read the regression log before treating a run as release evidence.

Local/manual evidence remains useful when labeled with its environment, scope, and custody limits. A green workflow run qualifies the checked commit on the listed hosted runners only. Publication and CI are separate decisions.
