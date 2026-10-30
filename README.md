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

## Describe a fix once

With the optional Codex workflow and a configured project profile:

```sh
python3 proofloop fix --root /path/to/project \
  --request "Fix the empty-search crash while preserving existing results."
```

It runs bounded repairs and fresh verification, then returns a locally verified patch or
a precise blocker. Source files remain unchanged until you apply the patch. Follow the
[one-request guide](ONE-REQUEST-WORKFLOW.md) for a disposable example and one-time setup.
Native calls require an installed, signed-in Codex CLI on a compatible POSIX host and consume
account usage. Patch delivery also requires Git. The core and synthetic smoke above work without Codex.

Checks run in snapshots of declared content and permission modes. They do not establish
equivalence of timestamps, ownership, ACLs, inode identity, external services or undeclared files.

## Choose a layer

| Layer | What happens | Requirements and limits |
| --- | --- | --- |
| Portable instructions | An operator or assistant follows task planning, evidence, and review guidance | Advisory text; execution requires your tools and permission |
| Core CLI and validator | Python records cards, hashes, checkpoints and receipts; recomputes JSON source/view drift | Only the built-in drift adapter supports recomputed Verified checks; external pins and exact-card approval are required for acceptance |
| Deterministic orchestration | Coordinator runs bounded synthetic inspect/repair/recheck stages | Executed code; see [orchestration](ORCHESTRATION.md); completion remains advisory |
| Optional one-request workflow and agent bridge | A project profile supplies scope and checks; separate Codex workers repair and verify an isolated candidate | Compatible POSIX host, installed signed-in Codex, and explicit invocation; may consume account usage; local verification remains separate from portable acceptance |
| General task harness | Typed checks, protected criteria, conditional specialists, builder and fresh verifier | [Task contracts and host adapters](HARNESS.md); all criteria must pass within explicit limits |

A receipt does not authenticate the reviewer or establish the truth of arbitrary evidence. Same-user pins demonstrate consistency only. The toolkit provides no OS confinement, autonomous truth verification, global installation, or production certification.

## Guides and checks

- [First-run guide](HOW-TO-USE.md) and [detailed receipt workflow](HOW-TO.md)
- [When to use it](WHEN-TO-USE.md) and [synthetic scenarios](examples/learning/CASE-STUDIES.md)
- [One-request workflow](ONE-REQUEST-WORKFLOW.md), [agent bridge](AGENT-BRIDGE.md), and [saved-run status](AGENT-STATUS-GUIDE.md)
- [Validation limits](VALIDATION-STATUS.md), [release status](RELEASE-STATUS.md), and [MIT license](LICENSE)

After repairing a declared input, use the [recheck workflow](HOW-TO.md#repair-and-recheck-a-changed-candidate) to create a fresh run while preserving the original criteria, evidence and repair usage. The [scoped skill review evaluator](SKILL-ACCEPTANCE.md) binds behavioral observations to a candidate for a separate reviewer decision. It does not expand portable receipt acceptance. A reusable [Codex workflow skill](templates/codex/.agents/skills/proof-loop-workflow/SKILL.md) is included for explicit invocation and proportionate checks.

```sh
python3 -B -m unittest discover -s tests -v
python3 -B pilot/acceptance.py
```

Tests use synthetic fixtures. Passing them establishes their covered behavior on the tested runtime, not universal compatibility or correctness of your task.
The development regression suite also uses Git to check that returned patches apply.
