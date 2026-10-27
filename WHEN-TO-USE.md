# When to use Proof Loop

Use it when a task needs a reviewable answer to “what was checked, against which inputs, with what result?” The strongest tasks have bounded inputs, explicit acceptance criteria, cheap deterministic checks and a meaningful failure example.

| Use case | Best starting scope | Current acceptance boundary |
|---|---|---|
| Generated data or views | Compare a declared JSON source with a generated count/ordered-ID projection | Built-in drift adapter can support scoped acceptance with the required pin and receipt |
| App regression work | Run retained engine/API tests in an isolated copy; pin source, tests and runtime | Logs are Reported until that app has a supported, independently tested adapter |
| Daily puzzles and simulations | Repeat the same dated/versioned case across fresh processes and clocks; test a deliberate regression | Determinism is useful evidence, not proof of every puzzle or UI behavior |
| PDF/export repair | Reopen and validate the saved output bytes, not just an in-memory object | Requires an artifact-specific adapter; test certificates do not establish production identity or legal validity |
| Sync and background work | Check clocks, ordering, idempotency, fencing and transport end to end | Passing unit tests do not prove a complete synchronization contract |
| Companion/MCP integration | Pin client, server and upstream versions; check actual health separately | Offline tests cannot establish a healthy live companion |
| Routine monitoring | Read-only policy/history checks with explicit freshness and alert coverage | Continue authorized monitoring; gate code/policy changes separately |
| Research or prose review | Record supplied sources, covered questions and unresolved claims | Human/tool review remains explicit; no invented automatic factual verification |

## Match the ceremony to the task

For a small exploratory or reversible task, use a brief Run Card and advisory evidence. A brainstorm or wording correction rarely needs a full acceptance receipt. Use the full acceptance workflow when a result will be reused, released, handed off or used to justify a consequential change.

Increase independent verification with risk. Consequential changes retain human approval even after automation exists. Do not begin with broad unattended write access, live deployment or a task whose “done” criteria can be rewritten by its builder.

## Good first pilot

Start with one app's existing tests, one exact source commit and runtime, a frozen acceptance contract, a failing control and an interruption/resume check. A new task-family adapter should begin with one exact source commit and runtime, frozen acceptance contract, a failing control, and interruption/resume checks. Its portable receipt remains blocked until the adapter is independently tested; see [validation status](VALIDATION-STATUS.md).

Use a second app only after the first adapter rejects false passes, stale evidence and tampered/missing provenance. Measure handling time and failures prospectively; the proposed two-week 50% time target has not been achieved or established by these trials.
