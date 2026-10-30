---
name: proof-loop-workflow
description: Apply Proof Loop to a substantial, scoped task when the user requests it or evidence-led verification would materially help. Use actual available Proof Loop commands and preserve their acceptance limits; avoid formal runs for trivial requests.
---

# Proof Loop working method

Carry the user's authorized task through to a checked result or a precise blocker. Follow higher-priority instructions and the owning project's rules; do not expand permissions, change scope, or overwrite unrelated work. A simple request needs only proportionate checks.

For a substantial task, establish the outcome, exact inputs and permitted changes, observable criteria, appropriate checks, limits, and any decision that really needs the user. Resolve routine choices yourself. Freeze the criteria and acceptance tests before work; keep them and verification evidence outside the builder's editable scope. Where that separation cannot be enforced, proceed with proportionate checks and clearly report that protection and independent acceptance could not be established. Inspect current state before changing anything or retrying an uncertain side effect.

Find the user's actual Proof Loop executable and inspect its current help and project guidance. When the user explicitly requests Proof Loop, attempt the real CLI even for a small task; inspect the relevant command and choose the smallest suitable check. If it cannot run within the authorized scope (including a read-only request), explain the specific limitation. For implicit use, choose it when the benefit warrants the setup. Use a task-specific run ID, scoped files and budgets, and retain the resulting evidence. Do not initialize or write to a project merely to answer a read-only question. If the executable or suitable adapter is unavailable, say so and use the best available proportionate checks without claiming that Proof Loop ran or accepted the result.

After a repair changes declared inputs, preserve the old run and its failed evidence. Use `proofloop recheck` when supported to create a fresh run with the original frozen criteria and repair count; rerun the checks against that candidate. A new acceptance-required Run Card needs fresh external reviewer approval. If recheck is unavailable, create a new run only after comparing the criteria with the original frozen baseline; never edit old manifests to make them current.

Use a fresh independent verifier for meaningful builds or consequential decisions when delegation is available and authorized. Give the verifier the requirements and primary evidence, not just the author's summary. Review findings, make at most two repair rounds, re-run checks affected by each repair, and stop on uncertainty or exhausted limits. Do not treat an agent's self-reported identity or matching opinion as proof of independence.

Report what changed, what was actually checked and what remains unknown. Separate observed results from reported claims and inference. A passing test or hash establishes only its covered check and consistency, not universal correctness. A completed advisory run is not acceptance: run the applicable validator and respect its result and independent-pin requirements. Never claim this method modifies the underlying model, authenticates evidence, provides an OS sandbox, schedules itself, or guarantees future behavior.

For skill behavior evaluation, freeze realistic cases before testing, retain primary observations and the exact candidate hash, and use the available skill acceptance packet evaluator when supplied. Distinguish a reviewer approval for the covered cases from portable receipt acceptance. Report untested cases and whether reviewer custody was actually established.
