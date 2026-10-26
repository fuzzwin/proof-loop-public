# Optional host integrations (disabled)

Nothing in this directory is installed or enabled. Host configuration changes by product and version. Recheck the official documentation for the installed host before adapting a template. Keep integrations project-scoped and preserve inherited permissions and approval settings.

## Codex custom verifier agent template

`verifier.toml.disabled` illustrates a project-scoped `.codex/agents/*.toml` agent. The template includes `name`, `description`, `developer_instructions`, and optional reasoning and memory settings; verify their support in your installed version. This file is not discovered or loaded while it has the `.disabled` suffix. Before enabling, verify the target Codex version and actual project path in its official agent docs.

No hook configuration is supplied. Hooks can fail open, do not cover every tool path, and cannot enforce permissions or guarantee delivery validation. Use the Proof Loop CLI's explicit `validate` command as the receipt gate. Runtime permissions or a separately configured OS sandbox remain the actual boundary.

Use the optional coordinator only when your task needs explicit stage ownership, bounded calls or resumable checkpoints. A scheduled job alone is not a trigger. If used, cap at a coordinator plus three workers. Invocation/spend totals are observational unless a runtime enforces them.

Reference docs reviewed for this template: Codex [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) and [hooks](https://learn.chatgpt.com/docs/hooks). Recheck before enabling.
