# Central deployment and organization enrollment

For one account without SSO, start with [the standalone guide](standalone.md). It contains the complete foundation, GitHub Actions and Cognito setup. The same central portal expands to evaluate member accounts; do not deploy one portal per environment.

## Central account prerequisites

Use a dedicated security/tooling account. Enable trusted access for Organizations StackSets and delegate administration to this account. Configure Security Hub delegated administration, standards and aggregation; configure an AWS Config aggregator with the member/Region coverage needed by the checks. Use an authorized bootstrap operator for organization prerequisites; the application does not configure the management account or modify Control Tower-managed stacks.

Use the following portal inputs. Supply their JSON equivalent through the `PORTAL_TFVARS_JSON` Actions variable, as described in [the standalone guide](standalone.md#3-configure-the-deployment-workflow); ignored local tfvars files are not available on GitHub runners:

```hcl
stage             = "central"
account_id        = "YOUR_CENTRAL_ACCOUNT_ID"
region            = "ap-southeast-1"
deployment_mode   = "organization"
auth_mode         = "cognito" # or saml with saml_metadata_url
organization_id   = "o-YOURORG"
target_ou_ids     = ["ou-EXAMPLE-DEV", "ou-EXAMPLE-STG", "ou-EXAMPLE-PROD"]
config_aggregator = "YOUR_AGGREGATOR"
enable_remediation = false
remediation_repository = "YOUR-OWNER/ns-infrastructure-terraform"
```

OU IDs are explicit scope, not wildcards. Choose all relevant dev/staging/production OUs. Discovery walks ancestors and uses account Environment/Owner tags with OU fallback; invalid/unclassified environments remain visible but cannot be remediated. Production OU ancestry always forces approval at apply time, regardless of tag. Use explicit collection Regions and aggregator coverage. Cognito works without SSO; SAML is available by supplying an HTTPS metadata URL and mapping verified group claims.

Bootstrap `infra/stacks/foundation` with `stage=central` and the monorepo, then configure central-plan/central environments as in the standalone guide. Set `PORTAL_DEPLOYMENT_MODE=organization` and `AWS_DEPLOYMENT_ENABLED=true`. Deploy the portal once before enrolling member roles so the trusted principal ARNs exist. Initial findings may be unknown until member enrollment completes.

## Member-account enrollment

`infra/stacks/org-bootstrap` owns service-managed StackSets for read roles, event forwarding and optionally bounded remediation roles. It has separate reviewed state from the application. Use output `remediation_reader_role_arns` as the initial `collector_role_arns` list and add `foundation.remediation_github_roles.plan` if using Terraform. Those principals are trusted by exact ARN and organization ID. Also include the apply workflow role in read-role trust if your approved Terraform workspaces need read-role sessions explicitly.

Configure the bootstrap input JSON using actual outputs:

```json
{
  "security_account_id": "CENTRAL_ACCOUNT_ID",
  "organization_id": "o-YOURORG",
  "region": "ap-southeast-1",
  "name": "gov-portal-central-prod",
  "target_ou_ids": ["ou-EXAMPLE-PROD"],
  "collector_role_arns": [
    "arn:aws:iam::CENTRAL_ACCOUNT_ID:role/gov-portal-central-collector",
    "arn:aws:iam::CENTRAL_ACCOUNT_ID:role/gov-portal-central-remediation_plan",
    "arn:aws:iam::CENTRAL_ACCOUNT_ID:role/gov-portal-central-remediation_apply",
    "arn:aws:iam::CENTRAL_ACCOUNT_ID:role/gov-portal-central-remediation-github-plan"
  ],
  "remediation_writer_role_arns": [
    "arn:aws:iam::CENTRAL_ACCOUNT_ID:role/gov-portal-central-remediation_apply",
    "arn:aws:iam::CENTRAL_ACCOUNT_ID:role/gov-portal-central-remediation-github-apply"
  ],
  "central_bus_arn": "arn:aws:events:ap-southeast-1:CENTRAL_ACCOUNT_ID:event-bus/gov-portal-central",
  "forwarding_regions": ["ap-southeast-1"]
}
```

Leave `remediation_writer_role_arns=[]` until write access is explicitly wanted. Native/member permissions are limited to the three implemented runbooks. Terraform workspaces that require additional read or write actions need reviewed account-owned policies, supplied through `remediation_extra_policy_arns`. Do not attach AdministratorAccess merely to make an unsupported plan pass.

The current bootstrap workflow retains `prod` and `sandbox` scopes to support independent OU rollout and state keys. These are enrollment scopes, not separate portal deployments. Configure `ORG_BOOTSTRAP_TFVARS_JSON` for prod and `ORG_BOOTSTRAP_SANDBOX_TFVARS_JSON` for dev/staging with distinct StackSet names, OU targets and the same central bus. State keys are `org-bootstrap/prod.tfstate` and `org-bootstrap/sandbox.tfstate`. Global IAM roles deploy only in the primary Region; event-forwarding stacks deploy in every configured Region.

Configure GitHub environments `org-bootstrap-plan`, `org-bootstrap-review`, `org-bootstrap` with the foundation's plan/apply org roles. Restrict to main and use different authorized human reviewers for the two review/apply gates. Run the sandbox scope first, inspect StackSet instance status, then production. The application deploy role cannot silently change organization enrollment. The workflow is disabled in standalone mode.

## Terraform remediation onboarding

Use [the monorepo contract](architecture.md#monorepo-contract). The central state bucket grants the GitHub identities access to `accounts/*` backend state and lock keys. Member provider sessions are assumed separately; member roles never need central state-bucket access. Existing workspaces using other state backends should retain their ownership; adapt the reviewed runner/backend contract before enrolling them, rather than creating duplicate state.

Install the workflows in `ns-infrastructure-terraform` and configure the remediation environments, bot secret and repository protection in the standalone guide. Onboard dev first. Ensure the provider resolves `governance-target` and limits account ID. A request's approved bindings must reference existing state addresses. Apply roles deliberately have only bounded permissions by default; workspace owners supply narrowly scoped additions where needed.

Before expanding OU scope, demonstrate: non-production dashboard action; production waits for review; changed head review rejected; a stale state/configuration plan fails; an active exception excludes remediation; a failed target is recorded; successful targets remain visible; and post-apply source re-evaluation updates compliance. Local/mock tests cover these contracts, but live multi-account integration must be validated in your organization.

## Releases, rollback and drift

The deployment pipeline packages one immutable release, uploads objects conditionally, saves a central Terraform plan, applies that exact plan and publishes hashed frontend assets before index/config. The API alias uses CodeDeploy with health alarms and automatic rollback. A central nightly workflow reports infrastructure drift without applying it. A portal rollback uses an earlier immutable release through a reviewed plan; workload remediation recovery requires a fresh resource plan where drift or Terraform failure occurred.

State/artifact/evidence buckets use encryption, versioning and retention. Foundation starts with local state for enrollment; migrate it to its encrypted bucket using Terraform's standard backend migration process after adding the reviewed backend configuration. Never commit state. Retain the original stage/state key when upgrading an existing earlier dev deployment until a migration plan is reviewed.
