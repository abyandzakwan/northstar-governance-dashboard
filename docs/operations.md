# Operations

## Daily use

The central dashboard filters governed accounts by dev, staging and production. Review unknown evidence, staleness and unclassified accounts before interpreting pass rates. A scan is read-only. Security Hub and Config events cause targeted re-evaluation through the central queue and scan Step Functions; they do not start writes. Framework views separate AWS-reported standards from internal technical mappings.

For remediation, select current non-compliant resources in **Remediation & changes**, prepare a plan, inspect its before/desired state and impact, then execute or request PR. Multi-environment selections create independent requests. Production requires reviewed merge. The live toggle is deployment-controlled, never browser-controlled. `enable_remediation=false` disables new live executions; remove/restrict member write trust as an infrastructure change when revoking already-issued sessions or active workflows is necessary.

## Failed and stale changes

Open the change card's plan, per-target outcomes and event timeline. Locate the request ID in Step Functions execution input, Lambda logs or the GitHub PR. Evidence includes plan hash, exact target, scope, result and attempts. Unsupported actions show manual requirements rather than a successful fake remediation.

- **Native failure:** inspect the AWS/API error, correct access or service prerequisites, then retry failed targets before the plan expires. Successful targets are skipped. A lock prevents competing resource writes. An updated OU, exception or current resource state may require a new plan.
- **Terraform failure / partial apply:** inspect the restricted saved plan/workflow, state lock and current state. A fresh dashboard request produces a new PR/plan for failed targets. Do not rerun a stale saved production plan or automatically undo successful changes across other accounts.
- **STALE_PLAN / expiry:** refresh source evidence, correct the owning configuration where appropriate and prepare a new request. Approval does not transfer to changed plan contents.
- **Workflow interrupted before completion callback:** status may remain APPLYING. Inspect actual workflow and Terraform state before recovery. Re-run the trusted callback/completion only for the exact request after verifying outcomes; do not mark success from the UI. An expired request requires a fresh plan. There is no automatic claim that a timed-out external deployment succeeded.

Native writes independently read state back. Terraform completion records apply success and schedules a scan. Security Hub may take time to publish compliance results. The dashboard must retain actual source status while waiting; a successful deployment alone is not proof that every standard now passes.

## Exceptions

An administrator can request a production exception with an owner, reason, resource/rule scope and expiry of at most 90 days. Creation and revocation go through a production PR; pending requests are visible in Changes and do not alter compliance scores. Team owners can create non-production exceptions without mandatory approval. Waivers are re-evaluated during scans and every 15 minutes. Expired/revoked waivers restore source-derived status. Accepted risk is shown separately and excluded from the pass denominator.

## Missing evidence or access

Check enabled Security Hub standards, account/Region coverage, aggregation and delegated administration. Inspect collector/read-role trust and Config aggregator mappings. Missing services/permissions must produce UNKNOWN, never PASS. Verify the target OU list and account owner tags. Cross-account API writes require both the central apply-role permission and exact-principal trust in `GovPortalRemediation`; read access uses `GovPortalReadOnly`. Standalone mode never assumes another account's role.

Cognito native users are administrator-created and use authenticator MFA. For SAML, inspect verified group claims. Never add access by accepting client-supplied role headers. Role groups are `gov-portal-admins`, `gov-portal-viewers`, and `gov-portal-team-<owner>`. Every change/report/aggregate is scoped to the caller's readable accounts; mutation requires admin or owning-team privileges.

## Queues, logs and exports

Inspect collector/remediation Step Functions failures, API errors/latency, stale evidence and DLQ depth alarms. The DLQ retries account scans once hourly before retaining repeated failures for investigation; it does not repeat remediations. Lambda logs and X-Ray trace the runtime; the request ledger retains bounded event history and target outcomes. CloudTrail/S3 audit capture AWS access. For long-term change audit, configure retention/export to your organization's immutable evidence destination as required.

Private report exports use 15-minute signed URLs; do not share them outside authorized recipients. Daily Parquet history and Athena provide account trends; history is retained approximately 400 days. Weekly SNS/Slack summaries require configured destinations and secrets; local mode does not send them. CSV output escapes spreadsheet formulas. GitHub PRs, manifests and Terraform plan artifacts may contain confidential resource metadata or state; restrict repository access and use the one-day remediation artifact retention.

## Pilot reset and rollback

Stop local demo servers and remove `.local/state.json` to reset virtual changes; browser tests start with fresh in-memory state. This affects no AWS resources. Real workload rollback is an explicit reviewed change based on current state. Disabling a default-encryption control, suspending versioning or weakening public-access block is never an automatic recovery action. Review portal infrastructure changes through the central deployment plan; CodeDeploy handles API alias health rollback separately.
