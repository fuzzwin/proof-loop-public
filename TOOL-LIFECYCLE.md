# Capability registry and tool lifecycle

Capabilities progress through explicit states:

`discovered → reviewed → installed → authenticated → tested → enabled`

Not every tool needs authentication. Record `not_applicable` with a reason where appropriate. Each entry includes a health check, project fit, policy restrictions, license/telemetry status, known data destinations, version/pin, evidence date, and regression/rollback notes. New versions go to a review queue and repeat relevant checks before activation.

The CLI applies **policy first, then lifecycle**. Fit, `use_when`, `not_for`, health-check commands, evidence quality, and ranking are registry notes only; the CLI does not evaluate or execute them. The operator must assess those descriptions explicitly and record the rationale in the Run Card. Trust is earned from evidence and review; a publisher name is not a trust tier. Vendor README claims about license or telemetry remain **Reported** until checked against the installed version and opt-out behavior is tested.

No external capability is installed or enabled by this bundle. Optional evidence tools include Playwright for browser journeys and OSV-Scanner for dependency advisories, only after the same intake review and a healthy pinned installation. PageIndex local mode may still transmit content to the model provider; confidential files require a local model or approved endpoint. OpenShell is a pilot candidate, not a core dependency. Its OS-level enforcement would need a separate verified acceptance track.

Coordinator/orchestration remains deferred unless one of these triggers occurs: concurrent writers, recovery across machines, multiple clients sharing state, or a need for hard invocation/spend caps. A scheduled task alone is not sufficient. If introduced, cap it at one coordinator plus three workers. Invocation/spend accounting remains observational unless the runtime provides an actual enforcement mechanism.
