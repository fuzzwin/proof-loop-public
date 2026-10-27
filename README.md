# Proof Loop

A fresh, project-local Python toolkit for scoped tasks, reproducible evidence, bounded repairs, and explicit validation. Bring your own task and files. This distribution contains source, tests, schemas, instruction templates, and synthetic examples; it includes no personal projects, attachments, saved agent sessions, or preaccepted receipts.

The [general task harness](HARNESS.md) adds typed software, data, document and
external-tool checks to the bounded agent loop. Task contracts declare separate
verification and validation criteria. Use the native Codex host or connect your
own local agent adapter through the documented JSON protocol.

## Start here

You need a local Python runtime (source targets Python 3.10+), a terminal, and permission to read and write your chosen project. No package installation, account, model, or network connection is required for the core or synthetic checks. Extract the source bundle, open a terminal in its folder, then run:

```sh
python3 proofloop --version
python3 tools/clean_start_smoke.py
python3 -B tools/harness_smoke.py
```

Use your system's Python command if it differs, such as `python` or `py -3`. The smoke check creates a temporary synthetic project, checks success and failure, then removes its temporary data. It does not modify your project or install anything. Check [platform support](HOST-COMPATIBILITY.md) before using an optional integration.

Next, follow [the first-run guide](HOW-TO-USE.md) to declare your own task, evidence files, acceptance criteria, and permitted writes. Keep project data outside this toolkit. Preserve existing project instructions when adding templates.

## Choose a layer

| Layer | What happens | Requirements and limits |
| --- | --- | --- |
| Portable instructions | An operator or assistant follows task planning, evidence, and review guidance | Advisory text; execution requires your tools and permission |
| Core CLI and validator | Python records cards, hashes, checkpoints and receipts; recomputes JSON source/view drift | Only the built-in drift adapter supports recomputed Verified checks; external pins and exact-card approval are required for acceptance |
| Deterministic orchestration | Coordinator runs bounded synthetic inspect/repair/recheck stages | Executed code; see [orchestration](ORCHESTRATION.md); completion remains advisory |
| General task harness | Typed checks, protected criteria, conditional specialists, builder and fresh verifier | [Task contracts and host adapters](HARNESS.md); all criteria must pass within explicit limits |
| Optional experimental agent bridge | Local Codex specialist, builder, and fresh verifier sessions run within explicit budgets | Compatible POSIX host, installed signed-in Codex, and explicit invocation; may consume account usage |

A receipt does not authenticate the reviewer or establish the truth of arbitrary evidence. Same-user pins demonstrate consistency only. The toolkit provides no OS confinement, autonomous truth verification, global installation, or production certification.

## Guides and checks

- [First-run guide](HOW-TO-USE.md) and [detailed receipt workflow](HOW-TO.md)
- [When to use it](WHEN-TO-USE.md) and [synthetic scenarios](examples/learning/CASE-STUDIES.md)
- [Optional agent bridge](AGENT-BRIDGE.md) and [saved-run status](AGENT-STATUS-GUIDE.md)
- [Validation limits](VALIDATION-STATUS.md), [release status](RELEASE-STATUS.md), and [MIT license](LICENSE)

```sh
python3 -B -m unittest discover -s tests -v
python3 -B pilot/acceptance.py
```

Tests use synthetic fixtures. Passing them establishes their covered behavior on the tested runtime, not universal compatibility or correctness of your task.
