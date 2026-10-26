# Test status

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
