# CI proposal — disabled

No workflow file is included or enabled. Start with local/manual checks until repository permissions, budget, trusted-runner design, and evidence retention are reviewed.

## Suggested first pipeline

1. Run manually on an exact reviewed commit with read-only repository permissions, no secrets, no deployment, and a bounded job timeout.
2. Check documentation links and the source manifest, then run the Python suite and synthetic CLI smoke.
3. Add platform jobs only when the actual environments are supported and their results are reported separately.
4. Have a verifier with genuinely separate authority rerun or recompute evidence.
5. Allow release steps to consume only fresh passing evidence tied to the exact source and output artifact. Keep consequential release approval human-controlled.

Missing identities, changed tests, stale hashes, truncated output, skipped/cancelled tests, nonzero process outcomes, deadline overruns, runner loss, and invalid receipts must block release. Preserve failed evidence and state the blocker. No success defaults, automatic gate weakening, or unbounded repair loop.

Local/manual evidence remains useful when labeled with its environment, scope, and custody limits. Publication and CI are separate decisions.
