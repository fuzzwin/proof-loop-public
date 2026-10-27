# Source release checklist

Use these checks when preparing a source release:

- Review the exact files, license, examples, and documentation intended for publication.
- Exclude private project data, credentials, runtime state, and unrelated history.
- Run the regression and protected acceptance suites and the disposable clean-start smoke.
- Check documentation links and scan the package for accidental disclosures.
- Obtain independent review appropriate to the change, and record its scope and limitations.
- Confirm the remote tree matches the reviewed package and test a fresh public checkout.
- Record the actual runtime, results, and any remaining platform limitations.

The toolkit does not install integrations globally or establish production acceptance.
