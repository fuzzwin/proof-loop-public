# Read-only CI

The [GitHub Actions workflow](.github/workflows/ci.yml) checks pull requests
against `main` and pushes to `main`. It runs the unit suite, protected pilot
suite, and both smoke scripts on Ubuntu and macOS with Python 3.10 and 3.13.
Each job records the checked-out commit and runtime and has a 20-minute timeout.

The workflow uses GitHub-hosted runners, a token with only `contents: read`,
and actions pinned to full commit SHAs. Checkout does not persist credentials.
The checks use synthetic fixtures and require no secrets or live model calls.

See [Test status](TEST-STATUS.md) for recorded runs. CI checks source behavior
on the listed runners. Release, deployment, and independent acceptance of a
project's evidence are separate decisions.
