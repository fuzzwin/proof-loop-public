# Separate sandbox acceptance track

This track is independent of the read-only synthetic pilot. A missing sandbox does not block read-only local checking, but it does block treating risky code execution as contained. The Proof Loop CLI itself is not a sandbox and this file records no sandbox tests as passed.

## Required tests before claiming an OS boundary

- A write outside the designated scratch/output area is denied.
- Network access is denied by default and only explicitly approved destinations work.
- No ambient credentials are visible to the task process.
- CPU, memory, process, and elapsed-time limits fire as configured.
- The verifier cannot write owning source files; verify with actual permissions, not instructions alone.
- Logs identify runtime/version, policy, test command, and observed result.

Record separate results for each supported OS/runtime. Windows behavior is its own unverified track, including file permissions, installers, and credential stores. Do not generalize a result across operating systems. Optional OpenShell pilot status remains disabled until installed, reviewed, and tested under this acceptance matrix.
