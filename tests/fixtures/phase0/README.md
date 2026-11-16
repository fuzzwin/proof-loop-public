# Phase 0 compatibility fixtures

Captured with the public CLI 0.2.2 at commit
`5dc531412d34f991b44d3411c6a85062e32b7254` using synthetic inputs. The JSON
containers store the original UTF-8 file text and POSIX modes needed by the
observers. Tests restore them into temporary projects and compare CLI stdout
byte for byte, including the JSON keys and nesting. They also assert exit codes
and empty stderr. Each observation starts from its own copy because the core
`status`, `resume`, and `validate` commands append to the run ledger.

- `agent-schema3.json` captures a completed managed diff run, including the
  source and candidate bytes, intake, checks, scripted verifier and delivery.
  Its command uses `python3` so the record carries no local interpreter path.
- `harness-bound.json` captures a passing `harness-check` checkpoint using the
  repository's synthetic document example. It binds the checks to the saved
  candidate.
- `harness-legacy-unbound.json` models the historical unbound representation
  of the same checkpoint. The check result omits the four candidate binding
  fields and its enclosing state references the resulting evidence hash.
  Status reads the run and reports historical quality without exposing it as
  current verification or validation.
- `receipt-schema3.json` captures a passing synthetic drift receipt and its
  acceptance inputs. The test places the saved synthetic approval pin beside
  the project, where the validator requires it. Validation has separate
  expected output for the recorded runtime and for a different Python or OS
  version. The receipt bytes are identical in both cases. The pin is test data.
- `cli-help.txt` captures the root help and every subcommand. Only whitespace
  in each usage paragraph is normalized, to account for argparse line wrapping.

Agent and harness deadlines are frozen in the past so remaining time is stable.
The receipt expires in 2100 so validation remains meaningful on repeated test
runs. These fixtures contain synthetic evidence and approvals. Update their
bytes or expected output only alongside a reviewed compatibility decision.
