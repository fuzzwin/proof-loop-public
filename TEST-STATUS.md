# Test status

## General harness candidate — 2026-10-28

The general harness adds tests that exercise actual local subprocesses with explicitly synthetic
agent fixtures, conditional specialist/builder/verifier flows, bounded repairs
and calls, false-pass controls, skips/empty suites, malformed protocols, partial
coverage, changed evidence, timeouts, report limits and read-only status. Review
repairs add independent-snapshot, candidate-freshness, legacy-status, and frozen
test-case coverage regressions. See the change's review and verification record for results
tied to its exact commit and tested runtime.

Run `python3 -B tools/harness_smoke.py` for disposable software, JSON-data and
document checks without a model. See [the harness guide](HARNESS.md) for contracts
and extension protocols. Synthetic checks do not qualify live-model/provider behavior or additional
platforms. The portable receipt acceptance boundary is unchanged.

## Published baseline checks

Fresh local verification on 2026-10-26: macOS (Darwin 23.5.0), arm64, CPython 3.14.3. Implementation, existing tests and schemas match source baseline `85e4f51963d346ec0703123e5545a2e81c4f7e26`. The distribution adds first-run documentation and a disposable smoke script; it carries no historical receipts.

- Regression suite: 149 tests passed, no skips reported (29.889 seconds).
- Protected agent-status acceptance suite: 20 tests passed (12.214 seconds).
- Clean-start smoke: passed initialization, registry validation, matching drift, status/resume, intentional mismatch exit 1 and blocked acceptance exit 2.
- Local Markdown targets: no broken links found.
- Source scan: no personal machine paths, private repository URLs, employer names, credentials or runtime payloads found by the targeted scan. The MIT copyright attribution retains the author's public handle.

Reproduce from the extracted source folder:

```sh
python3 -B tools/clean_start_smoke.py
python3 -B -m unittest discover -s tests -v
python3 -B pilot/acceptance.py
```

These are synthetic checks. They do not qualify real applications, independent pin custody, live model performance, Linux, Windows, Python 3.10, or automatic host activation. A successful package scan is bounded by its patterns and review coverage. Tie any later results to the exact exported source commit.

On 2026-10-27, a connector download of public candidate commit `5987b5704d8950167175690e622e31b270e766b5` was checked on macOS 14.5 arm64 with CPython 3.14.3: all 149 regression tests and 20 protected acceptance tests passed, and the clean-start smoke passed. The smoke also passed from a fresh Git clone of that commit. Subsequent publication-status edits change documentation only.
