# Platform support and optional hosts

The core source targets Python 3.10+ and uses the standard library. [GitHub Actions run 43813450484](https://github.com/fuzzwin/proof-loop-public/actions/runs/43813450484) passed the unit suite, protected pilot suite, and both smokes on GitHub-hosted Ubuntu and macOS with Python 3.10 and 3.13. See [Test status](TEST-STATUS.md#phase-0-read-only-ci) for exact interpreter versions and platform skips.

| Component | Support boundary |
| --- | --- |
| Core CLI, synthetic drift and deterministic coordinator | Tested on the Ubuntu and macOS runners listed above |
| Native agent bridge | Experimental POSIX only. Uses process groups and directory-relative filesystem operations; unsupported hosts are rejected. CI exercises synthetic bridge cases on these Ubuntu and macOS runners. Live host behavior needs separate testing |
| Windows | No native bridge support; no Windows-specific implementation is bundled. Run the core smoke and regressions before relying on it; use WSL only as a separate untested Linux environment |
| Hosted chat | Can work from supplied text and files. Local execution requires a connected runtime; local templates do not automatically load in another app |

No global setup is required. Instruction templates are optional advisory material. Review and merge project instructions without replacing existing files. Keep inactive host templates disabled until you have checked them against your installed host's documentation. This distribution installs no hooks, agents, packages, credentials, or background services.

The optional bridge needs an installed, signed-in local Codex runtime and explicit task authorization. New run boundaries require ACL inspection; the bridge checks macOS extended ACLs and Linux POSIX ACL attributes. Unavailable inspection blocks the run; other ACL models and network filesystems need separate verification. Its model calls may consume account usage. Synthetic tests use protocol doubles and do not establish live-model performance. Runtime permissions and separately configured OS controls govern execution; instructions, hooks, receipts, and process groups are not an OS security boundary.

Run `python3 tools/clean_start_smoke.py`, `python3 -B -m unittest discover -s tests -v`, `python3 -B pilot/acceptance.py`, and `python3 -B tools/harness_smoke.py` in a disposable checkout on each intended platform. Inspect failures and skips before claiming support.
