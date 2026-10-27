# Frozen integration acceptance v1

Frozen before implementation, 2026-10-24 UTC. Base main: a72b8239647a0afda45c5252ec0968cde2a29481.

Scope: integrate the existing durable coordinator with the portable core using a deterministic, explicitly synthetic JSON projection worker. No model/API calls, dependency installation, live app activation, credentials or OS confinement claims.

Required checks:
1. Preserve all 29 core and 19 pilot cases; do not modify prior pilot evidence.
2. Frozen plans bind source, initial view, repair permission, acceptance contract and check identity. Changed plans/inputs block; unknown adapters/checks block.
3. Inspect actual JSON with core drift runner, repair only the declared synthetic projection within explicit scope, then rerun core drift under a new advisory Run Card. Never rewrite failed logs or frozen old input manifests.
4. DAG dependency freshness, three-worker limit, exclusive resources, idempotency, version conflict and uncertain-work retention remain enforced. No retry of uncertain physical/external side effects.
5. Interrupted checkpoint/resume has the same final finding as uninterrupted execution. Completed replay does not rerun writes or claim new execution.
6. Malformed input, denied repair and unknown checks produce an actionable non-success; failure is not promoted to success. Final receipt stays advisory, not accepted.
7. Improvement proposals are hash-bound to candidate/base/evaluator/regression/holdout evidence. Missing evidence, unresolved unknowns, failed regression, same proposer/reviewer, changed artifacts, reused/exposed holdout and permission/gate changes prevent eligibility. Eligibility is not promotion or activation.
8. Document commands, safety limits, observed tests and remaining host/independent acceptance limitations. No global install, merge or deployment.

Implementation repair budget: two rounds after first full new-suite execution; any scope change needs a new acceptance revision rather than editing this frozen file. Independent review is separate from the author’s tests.
