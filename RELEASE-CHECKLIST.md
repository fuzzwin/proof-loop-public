# Source release checklist

Use these checks when preparing a source release:

- Review the exact files, license, examples, and documentation intended for publication.
- Exclude private project data, credentials, runtime state, and unrelated history.
- Decide the version. Bump `VERSION` in `proofloop.py` whenever published behavior or features change. Runner logs and receipts must carry the executing CLI version, so evidence from the previous version stops validating; say so in the changelog entry.
- Move the changes from any "Unreleased" changelog sections into one dated entry for that version. Update the schema and guides that state the current version; `tests/test_release_consistency.py` fails if they disagree.
- Run the regression and protected acceptance suites and the disposable clean-start and harness smokes.
- Check documentation links with `python3 -B tools/check_links.py` and scan the package for accidental disclosures.
- Run the manual [source checks workflow](CI-PROPOSAL.md) on the exact commit, read its logs including skips, and record the run link.
- Obtain independent review appropriate to the change, and record its scope and limitations.
- Confirm the remote tree matches the reviewed package and test a fresh public checkout.
- Create an annotated tag `vX.Y.Z` on the reviewed commit and a GitHub release that points to it, so users can pin a version instead of a commit hash.
- Record the actual runtime, results, and any remaining platform limitations.

The toolkit does not install integrations globally or establish production acceptance.
