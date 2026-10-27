# Release status

This is the public Proof Loop source distribution, prepared from implementation baseline `85e4f51963d346ec0703123e5545a2e81c4f7e26`, with MIT licensing, synthetic examples and first-run documentation. The CLI identifies as version 0.2.2.

This source adds the optional [one-request workflow](ONE-REQUEST-WORKFLOW.md), isolated
candidates, final check reruns, exact requirement/test coverage, and journaled promotion
recovery. New native runs use state schema 3; `agent-status` can inspect schemas 1, 2 and 3.
Schema-2 observations are indeterminate because that draft format lacks permission evidence.
Legacy runs are never silently migrated or resumed by the new execution host. The portable
receipt schema and core CLI version remain unchanged. Pin the source commit for this feature.

Historical version 0.2.0 is withdrawn for acceptance because failed runner evidence could be accepted after receipt edits. Version 0.2.1 also had reproduced false-pass cases. This distribution includes later corrections; rerun older checks rather than relabeling evidence.

See [test status](TEST-STATUS.md) for the package checks and tested runtime. Project records report an independent review of the source and an earlier documentation snapshot. The publication-status documentation was subsequently updated; raw reviewer logs are not included in this distribution.

Two independent agent review sessions examined the one-request additions and subsequent
repairs, including frozen source commit `623267f45bdae97c3f3485baa88087285c8dd6a4`.
They are scoped source reviews, not an independent human audit or exhaustive security review.
See the test status for the checks, platform and remaining limits.

The experimental native agent bridge is opt-in. Advisory completion does not establish production acceptance, reviewer identity, OS confinement, universal compatibility, or automatic host activation. Source, tests, schemas and synthetic fixtures are included; historical project payloads and runtime evidence are excluded.
