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

On 2026-10-27, a connector download of public candidate commit `5987b5704d8950167175690e622e31b270e766b5` was checked on macOS 14.5 arm64 with CPython 3.14.3: all 149 regression tests and 20 protected acceptance tests passed, and the clean-start smoke passed. The smoke also passed from a fresh Git clone of that commit. Subsequent publication-status edits change documentation only.

## One-request workflow verification, 2026-10-28

The one-request additions were checked on macOS 14.8.9 arm64 with CPython 3.14.3
and Codex CLI 0.154.0 using existing ChatGPT authentication:

- All 197 regression tests passed. Coverage includes isolated repair and promotion recovery,
  exact test/requirement coverage, applicable patches, malformed setup/intake, source drift,
  completed replay, and read-only status for both native state formats.
- All 20 protected legacy status checks passed; their acceptance fixtures were preserved.
- The disposable clean-start smoke passed, and local Markdown links resolved.
- Two real native trials each used one builder and one fresh verifier after one repair round.
  A single-file addition fix and the documented two-file order-total demo returned verified
  diffs while retaining the original source. Both status reports were current. Replaying the
  completed requests made no new calls or persistent evidence-content changes in those trials.
- The order patch applied in a separate disposable project, produced the exact candidate
  file hashes, and passed all three protected order tests. Its optional test adapter also
  recorded those exact three IDs during the workflow and final recheck.
- The publication scan found no personal machine paths, private repository URLs, credential
  values, saved native session IDs, or runtime payloads in the proposed source files.

The native trials verify the demonstration candidates. Raw native events, account data and
historical project payloads are excluded from this distribution. These checks establish no
additional platform support or portable production acceptance.

## Independent review and repairs, 2026-10-28

Two independent agent sessions reviewed the original one-request changes and subsequent
frozen repair revisions, including source commit `623267f45bdae97c3f3485baa88087285c8dd6a4`.
They found and prompted repairs to test collection failures, patch and permission fidelity,
private evidence boundaries, missing or changed coordinator history, and replay and recovery.
Regression tests cover those reported cases. This was scoped source review, not an
independent human audit or an exhaustive security review.

On that frozen source, 246 regression tests and all 20 protected status checks passed
on macOS 14.8.9 arm64 with CPython 3.14.3; the clean-start smoke also passed.

Three additional real native trials on earlier repair revisions exercised import failure,
SystemExit during collection, and inherited macOS ACLs. Each returned a diff after one repair
and fresh verifier. On the final reviewed source, replay of the ACL trial returned the same
diff without another agent dispatch. Linux ACL handling remains untested. A target with mixed
file modes that Git cannot reproduce with one umask receives a blocker. The older explicit
apply path cannot atomically exclude external editors; pause them during promotion and
recovery. Source timestamps, ownership, ACLs and undeclared state are outside candidate
verification. Evidence and privacy are checked before completion is saved; unrelated
changes after that point are a separate observation, not an atomic filesystem guarantee.
These checks do not confer portable receipt acceptance.
