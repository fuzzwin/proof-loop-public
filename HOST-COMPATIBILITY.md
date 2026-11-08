# Platform support and optional hosts

The core source targets Python 3.10+ and uses the standard library. This package has been exercised on macOS and, for 0.3.0, on Linux x86_64 with the runtimes recorded in TEST-STATUS.md. Other Python versions and operating systems need their own checks. Python 3.10 is a source target, not a tested minimum in this release.

| Component | Support boundary |
| --- | --- |
| Core CLI, synthetic drift and deterministic coordinator | Tested on the recorded macOS runtime and on Linux x86_64 with CPython 3.11–3.13. Windows has not been tested for this package |
| Native agent bridge | Experimental POSIX only. Uses process groups and directory-relative filesystem operations; unsupported hosts are rejected. Synthetic regressions pass on macOS and Linux; live native trials have run only on macOS |
| Windows | No native bridge support; no Windows-specific implementation is bundled. Run the core smoke and regressions before relying on it; use WSL only as a separate untested Linux environment |
| Hosted chat | Can work from supplied text and files. Local execution requires a connected runtime; local templates do not automatically load in another app |

No global setup is required. Instruction templates are optional advisory material. Review and merge project instructions without replacing existing files. Keep inactive host templates disabled until you have checked them against your installed host's documentation. This distribution installs no hooks, agents, packages, credentials, or background services.

The optional bridge needs an installed, signed-in local Codex runtime and explicit task authorization. New run boundaries require ACL inspection; the bridge checks macOS extended ACLs and Linux POSIX ACL attributes. Since 0.3.0 the Linux path has synthetic regressions on ext4; it is untested with live native runs. Unavailable inspection blocks the run; other ACL models and network filesystems need separate verification. Its model calls may consume account usage. Synthetic tests use protocol doubles and do not establish live-model performance. Runtime permissions and separately configured OS controls govern execution; instructions, hooks, receipts, and process groups are not an OS security boundary.

Run `python3 tools/clean_start_smoke.py`, `python3 -B -m unittest discover -s tests -v`, and `python3 -B pilot/acceptance.py` in a disposable checkout on each intended platform. Inspect failures and skips before claiming support.
