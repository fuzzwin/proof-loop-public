# Test status

## 0.3.0 Linux checks and A/B comparison: recorded 8 November 2026 UTC

The source tree of the `release: Proof Loop 0.3.0` commit, `d1d12b9c2ab953c8001af9052b25876200769389`, was compared with the previous public head `5dc531412d34f991b44d3411c6a85062e32b7254` (CLI 0.2.2) in the same environment: Linux 6.18 x86_64, ext4, CPython 3.11.18, 3.12.3 and 3.13.18. Each run used a fresh copy of the source and the five documented commands.

| Check, per Python version | 0.2.2 baseline | 0.3.0 |
| --- | --- | --- |
| `proofloop --version` | Proof Loop 0.2.2 | Proof Loop 0.3.0 |
| Clean-start and harness smokes | passed | passed |
| Regression suite | 297 run, 0 failed, 5 skipped | 310 run, 0 failed, 5 skipped |
| Protected status acceptance | 20 passed | 20 passed |

All 297 shared regression tests had the same outcome in both versions on every runtime. The 13 added tests passed: 10 Linux POSIX ACL regressions and 3 release-consistency checks. The 5 skips on both sides are the macOS extended-ACL regressions.

- **Why the Linux ACL tests were added.** With the Linux branch of the ACL detection or clearing code changed to never detect an ACL, ignore default ACLs, skip clearing, or swallow inspection errors, the 297-test suite still passed on Linux. The new tests failed for each of those four changes.
- **Version migration.** With both CLIs on a synthetic project: the 0.3.0 validator rejected a 0.2.2 receipt on `checker_version`, and 0.3.0 `recheck` of a 0.2.2 run created a 0.3.0 child with the parent's criteria and one used repair round. Its drift log recorded runner version 0.3.0 and validation reported no version errors.
- **Python 3.10 and 3.14** were not run; only a static parse found no syntax or standard-library use newer than 3.10. The macOS behavior of the new tests was simulated (all skip), not run on macOS.
- **The manual workflow** in `.github/workflows/checks.yml` has been parsed but not yet run on GitHub runners.

These checks and the changes were made in one AI agent session, so they are not independent review. Live native Codex trials, Linux ACL models other than POSIX ACLs on ext4, network filesystems, and Windows remain untested. Raw logs are not included.

## Repair/recheck and scoped skill review: recorded 30 October 2026 UTC

[Merged PR #4](https://github.com/fuzzwin/proof-loop-public/pull/4) reports **297 regression tests and 20 protected status acceptance tests passed**, along with clean-start/harness smoke checks and standard skill validation, on macOS arm64 with CPython 3.14.3. It also reports a fresh independent agent review with focused compatibility/negative cases and a synthetic repair/recheck/approval flow.

The reviewed tree was `66b7c6724f7a3955a2825c16583769ef5d566db7`. GitHub's commit record confirms that public merge commit [`eb7d7f12d6b3ebb5bddb8296eff51bdb6ad60af2`](https://github.com/fuzzwin/proof-loop-public/commit/eb7d7f12d6b3ebb5bddb8296eff51bdb6ad60af2) points to that tree. This section was reconciled from existing records on 5 November 2026 UTC; no tests or model calls were rerun for this documentation update. Raw reviewer logs are not included in the distribution.

- [Recheck regressions](tests/test_recheck.py) cover fresh child manifests, preserved parent evidence/criteria, carried repair usage, rejected metadata/criteria changes and required approval of the new Run Card.
- [Skill packet regressions](tests/test_skill_acceptance.py) distinguish packet consistency, exact attributed approval and portable acceptance; tampered/missing/duplicate evidence and unsafe paths fail closed.
- The reusable [workflow skill](templates/codex/.agents/skills/proof-loop-workflow/SKILL.md) instructs explicit CLI invocation when available. Passing its static validation does not establish arbitrary task behavior.

The counts above describe this snapshot; do not add them to earlier suites. These are scoped reported local results, not new Linux/Windows qualification, authenticated independent custody or production acceptance. See [workflow examples and result meanings](FLOW-EXAMPLES.md).

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

## Integration with the general task harness

Public main gained a separate harness while the one-request changes were under review.
Integration preserves both commands, binds typed checks to file and directory modes, reruns
checks after verification, provides typed promotion recovery, and keeps old task evidence
inspectable without migrating it. Two independent agent sessions reviewed the integration
and the final recovery diagnostic at `8a69dbbafc0c593c2a803f345ad6f86a8b80a05f`.

On the preceding integrated source `e092d51e0cf0aa05580e933af2100c08b0908770`, all
281 regression tests and 20 protected status checks passed on the macOS runtime above;
both clean-start and three-domain harness smoke checks passed. The final source changes
only the harness recovery diagnostic and adds its regression; its focused harness suite
also passed. An old public-main harness run was separately checked through both the old
and integrated status readers. Replay of the ACL native trial on the integrated source
returned the same diff without another agent call.
