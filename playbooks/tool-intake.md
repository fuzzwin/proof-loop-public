# Tool intake playbook

1. Record the canonical source and exact version/commit; no floating `latest` or implicit package downloads.
2. Apply project and organizational policy before comparing tools. Stop if policy disallows the tool or data transfer.
3. Review code, scripts, hooks, configuration, license, telemetry/data destinations, authentication, and outbound access. Keep vendor claims labeled Reported until installed-version checks and opt-out tests.
4. Record lifecycle state and health check in the project capability registry. Trust comes from review evidence, not publisher identity or a clean scanner alone.
5. Trial only with synthetic data in an approved environment. Record files/hosts touched and regression results.
6. Enable only after required checks pass; keep rollback instructions. Upgrades return to review before activation.
7. On an uncertain external write, reconcile remote state before retry. Never resend solely because acknowledgment was lost.

Playwright and OSV-Scanner are optional examples, not installed claims. Local document indexing does not imply local model inference: a provider may receive content. Never send confidential material to an unapproved endpoint.
