# Acceptance evidence matrix

This matrix suggests task-scoped checks and evidence to request. It does not imply that tools or adapters are installed. The portable receipt currently recomputes only the built-in source-view-drift JSON check as Verified. Other tool outputs remain reported evidence until a dedicated runner is implemented and independently checked.

| Work type | Candidate checks | Candidate artifacts (not automatically verified) |
|---|---|---|
| Code change | Acceptance tests, unit tests, lint/type check | Test output, changed-file hashes, diff summary |
| Data | Schema, row counts, invariants, source/view match | Row samples, aggregate comparison, source and output hashes |
| Research | Source coverage, claim-to-source map, date scope | Source list, quotes/locations, access limits |
| Security | Threat model, dependency scan, permission review | Scanner output, affected paths, remediation state |
| CAD | Geometry constraints, export/import sanity, dimensions | Native checker/version, measurements, export hashes |
| Media | Dimensions/duration/codec, visual/audio review | Probe output, file hashes, sampled frame/audio review |

Only mark a check pass for CLI acceptance when its runner is recognized and recomputed by the local validator. Arbitrary runner labels do not prove execution. Use Reported or Unknown for outputs from external tools without a supported adapter.
