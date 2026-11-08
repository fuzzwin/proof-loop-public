# Security policy

Proof Loop records evidence and runs checks and optional agent workers as the current user. It is not a sandbox. Read the boundaries below before relying on it.

## What it does not protect against

These are documented limits, not vulnerabilities:

- **No OS confinement.** Checks, adapters and agent workers run with your permissions. Instructions, hooks, receipts and process groups are not a security boundary.
- **Receipts do not authenticate anyone.** A receipt does not prove who reviewed it or that arbitrary evidence is true. Same-user pins show consistency, not independent custody.
- **Snapshots cover declared content and permission modes only.** Timestamps, ownership, undeclared files and external services are outside candidate verification. ACL inspection covers macOS extended ACLs and Linux POSIX ACLs; other ACL models and network filesystems are unverified.
- **Experimental native bridge.** It needs a POSIX host and a signed-in Codex CLI, and its model calls run under that account.

See [validation limits](VALIDATION-STATUS.md) and [platform support](HOST-COMPATIBILITY.md) for detail.

## What to report

Please report anything that lets evidence pass when it should be rejected, or crosses a boundary the code claims to enforce, for example:

- a failed or tampered check, runner log or receipt that validates as a pass (as happened in withdrawn versions 0.2.0 and 0.2.1);
- a path, symlink or permission escape from the declared project or private run directories;
- an ACL or privacy check that fails open instead of blocking;
- a worker process that survives cleanup or a run that exceeds its declared limits;
- credentials or private data written into evidence.

## How to report

Use GitHub private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. If that option is not shown, open an issue asking for a private contact and leave out exploit details.

Include the commit SHA, platform, Python version, and the smallest synthetic project that reproduces the problem. Do not include real project data or credentials.

## Supported versions

Fixes go into the latest tagged release and `main`. Older versions are not patched; rerun checks with the current version rather than relabeling old evidence.
