# Release status

This is a local clean-source candidate for a separate public distribution. It contains the current implemented core from source baseline `85e4f51963d346ec0703123e5545a2e81c4f7e26`, with MIT licensing, synthetic examples and revised first-run documentation. It has no release tag and has not been published.

The CLI identifies as version 0.2.2. Historical version 0.2.0 is withdrawn for acceptance because failed runner evidence could be accepted after receipt edits. Version 0.2.1 also had reproduced false-pass cases. This candidate includes later corrections; rerun older checks rather than relabeling evidence.

See TEST-STATUS.md for fresh package checks and the tested runtime. A fresh independent skeptical review passed the source and final documentation through commit `b01aa1f78265c01a51431f99bb4a355e51f2190d`. The reviewer independently reran the smoke, 149 regression tests and 20 protected acceptance tests. Tracked-source archive inspection and extracted smoke passed; release-status/checklist edits are documentation-only. Public repository creation, remote readback and public-clone testing remain separate pending publication steps.

No historical projects, source attachments, operational databases, run receipts, session recordings, private repository URLs or original Git history are included in the source archive. Source, tests, schemas and synthetic fixtures are retained. Git metadata and local verification logs are excluded by tracked-source export.

The experimental native agent bridge is opt-in. Advisory completion does not establish production acceptance, reviewer identity, OS confinement, universal compatibility, or automatic host activation.
