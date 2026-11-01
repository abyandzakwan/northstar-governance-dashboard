# One test account, without SSO

Deploy **one central portal** in your test account. `deployment_mode=standalone` collects only that account; `auth_mode=cognito` provides username/password plus authenticator-app MFA. No Organizations, SSO, member roles, StackSets, domain or private certificate is required. CloudFront provides HTTPS. Local preview needs no AWS account at all.

The six local demo accounts (`ns-fligth-{dev,testing,production}` and `ns-book-{dev,testing,production}`) remain virtual. This deployment does not create AWS accounts or inject demo evidence into AWS. To monitor six real accounts later, enroll their real IDs using organization mode.

Before deployment, provide:

- The testing AWS account's 12-digit ID and an authenticated local AWS CLI profile with permission to bootstrap the infrastructure (IAM/OIDC, KMS and S3). Keep credentials in the local profile.
- The Region; examples use `ap-southeast-1`. The portal also creates global CloudFront/WAF resources through `us-east-1`.
- An email for the initial portal administrator.
- GitHub administrator access to `abyandzakwan/northstar-governance-dashboard` to configure Actions variables and environments; authenticate GitHub CLI as `abyandzakwan`.
- Whether `token.actions.githubusercontent.com` already exists as an IAM OIDC provider in this AWS account. Reuse it if present.

GitHub Actions uses [OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), so it does not need long-lived AWS access-key secrets. Initial collection needs no GitHub bot token. Security Hub and Config evidence require those services to be enabled separately; their charges and the portal's infrastructure charges apply.

## 1. Configure the central profile

For a new standalone account, AWS CLI v2.32+ supports browser sign-in using your console credentials. This Mac has v2.38.4. Create a named profile:

```sh
aws login --profile northstar-test --region ap-southeast-1
aws sts get-caller-identity --profile northstar-test --query Account --output text
```

Select your testing account in the browser. An IAM user/role needs `SignInLocalDevelopmentAccess` as well as the infrastructure permissions; see [AWS's console sign-in guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html). The login uses temporary credentials and does not require an Organization.

The pinned Terraform provider predates `login_session` support. Use AWS's documented `credential_process` bridge for Terraform and the pinned Python SDK:

```sh
aws configure set credential_process \
  'aws configure export-credentials --profile northstar-test --format process' \
  --profile northstar-test-tf
aws configure set region ap-southeast-1 --profile northstar-test-tf
```

Confirm your AWS CLI identity:

```sh
export AWS_PROFILE=northstar-test-tf
export AWS_REGION=ap-southeast-1
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}'

cp infra/envs/foundation.tfvars.example infra/envs/foundation.tfvars
cp infra/envs/central.tfvars.json.example infra/envs/central.tfvars.json
```

Replace `YOUR_12_DIGIT_ACCOUNT_ID` in both copied files with the verified account ID and use the same Region in both. Set `collection_regions` to the Regions you want to scan. Keep `stage="central"`, `deployment_mode="standalone"`, `auth_mode="cognito"`, `standalone_environment="testing"`, and `enable_remediation=false` for initial collection. The deployment stage is central even though the account's governance environment is testing. Both copied files are ignored by Git.

## 2. Bootstrap state and GitHub OIDC

Use the testing profile verified above. The foundation example already binds GitHub's trust policy to `abyandzakwan/northstar-governance-dashboard`:

```sh
terraform -chdir=infra/stacks/foundation init -input=false -lockfile=readonly
terraform -chdir=infra/stacks/foundation plan \
  -var-file=../../envs/foundation.tfvars \
  -out=foundation.tfplan
# Review the planned resource creation and target account before applying.
terraform -chdir=infra/stacks/foundation apply foundation.tfplan
terraform -chdir=infra/stacks/foundation output
```

This creates private, encrypted, versioned state/artifact buckets, an OIDC provider, central plan/deploy roles, and separate remediation plan/apply roles. If your account already has GitHub's OIDC provider, also pass `existing_github_provider_arn` to the plan. Keep foundation state protected and backed up; it is excluded from Git. Do not commit any state, binary plans or tokens.

## 3. Configure the deployment workflow

Create GitHub environments `central-plan` and `central`. Restrict `central` to `main`. Allow `main` and `refs/pull/*/merge` in `central-plan` if you want infrastructure plans on same-repository PRs; the workflow excludes fork PRs. These patterns follow GitHub's [environment deployment-branch rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments#deployment-branches-and-tags). Set the following environment variables from the foundation outputs:

| Variable | central-plan | central |
|---|---|---|
| AWS_ROLE_ARN | plan_role_arn | deploy_role_arn |
| AWS_REGION | ap-southeast-1 | same |
| TF_STATE_BUCKET | state_bucket | same |
| ARTIFACT_BUCKET | artifact_bucket | same |

Set repository Actions variable `PORTAL_TFVARS_JSON` to the complete contents of your edited `infra/envs/central.tfvars.json`. The workflows validate and materialize that JSON before planning, so they do not depend on an ignored local file. This variable contains non-secret infrastructure inputs only.

Use the official GitHub CLI (on this Mac, `/opt/homebrew/bin/gh`; the Python `gh` shim is unrelated):

```sh
/opt/homebrew/bin/gh auth switch --hostname github.com --user abyandzakwan
/opt/homebrew/bin/gh api user --jq '.login'
/opt/homebrew/bin/gh variable set PORTAL_TFVARS_JSON \
  --repo abyandzakwan/northstar-governance-dashboard < infra/envs/central.tfvars.json
/opt/homebrew/bin/gh variable set PORTAL_DEPLOYMENT_MODE \
  --repo abyandzakwan/northstar-governance-dashboard --body standalone
```

Configure the `central` environment's deployment review policy as appropriate for your GitHub plan. The saved binary plan and text plan are uploaded before the apply job, and apply uses that exact plan. A solo tester must be able to approve their own deployment, or use no required reviewer. Remediation production-resource approvals remain separate.

When the foundation, variables, environment policies and PR checks are ready, set repository Actions variable `AWS_DEPLOYMENT_ENABLED=true`. Leave `AWS_REMEDIATION_ENABLED` unset/false for this initial rollout. CODEOWNERS points to `@abyandzakwan`; add another authorized reviewer before enabling production remediation.

Push the code to main or run **Actions → Deploy central governance portal** from main. The workflow builds once, saves a plan, applies that exact plan, publishes the frontend, and runs a collection smoke check. There is one portal and one state key, `portal/central.tfstate`. An infrastructure PR before the first deployment reports that no portal state exists; the first main deployment produces the initial full plan.

PR checks include Python/browser tests, Terraform validation and mock tests, TFLint, and Checkov's default policies. Resolve failed checks before enabling deployment. The previous references to missing local linter/scanner configuration files have been removed.

As of 2026-11-01, a local Checkov 3.4.0 Terraform scan reports 441 passing checks and 93 policy failures across the existing infrastructure. These include VPC placement and code signing for Lambda, one-year log retention, bucket replication/access logs, and several logging/encryption controls. Some require an explicit architecture decision for a standalone test deployment; others need configuration changes. They have not been suppressed or accepted. The deployment's checks job will remain blocked until they are resolved or individually reviewed with documented exceptions.

Read the URL and Cognito pool after the deployment:

```sh
terraform -chdir=infra/stacks/portal init -reconfigure \
  -backend-config='bucket=YOUR_STATE_BUCKET' \
  -backend-config='key=portal/central.tfstate' \
  -backend-config='region=ap-southeast-1'
terraform -chdir=infra/stacks/portal output portal_url
terraform -chdir=infra/stacks/portal output user_pool_id
```

## 4. Create your portal user

```sh
aws cognito-idp admin-create-user \
  --region ap-southeast-1 \
  --user-pool-id YOUR_USER_POOL_ID \
  --username test-admin \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL
aws cognito-idp admin-add-user-to-group \
  --region ap-southeast-1 \
  --user-pool-id YOUR_USER_POOL_ID \
  --username test-admin \
  --group-name gov-portal-admins
```

Open the portal, sign in using the emailed temporary password, choose a permanent password and enroll an authenticator. Users are administrator-created; self-signup is disabled. `gov-portal-viewers` grants read access and `gov-portal-team-<owner>` scopes team actions. SSO is optional: later supply SAML metadata and change `auth_mode` without deploying another portal.

## 5. Enable AWS evidence sources

For a standalone test portal where Security Hub is unavailable, set
`securityhub_sample_fallback=true` in the portal inputs. The collector uses labelled
synthetic ASFF findings only when Security Hub fails or returns no findings; real
findings take precedence. Samples are not imported into Security Hub. The portal
shows a persistent sample-data banner, and synthetic findings are rejected by the
remediation API. This option requires standalone mode and
`enable_remediation=false`. Scores that include samples are preview values, not
evidence of the real account's compliance. Config and other missing evidence remain
unknown. Set the option to false and rescan when switching to real evidence only.

Enable Security Hub and the standards you want to evaluate in the selected Region. Enable Config recording and relevant managed rules if you need Config/custom inventory checks. Configure Backup/baseline inputs only if used. The portal reads those services; it does not silently subscribe to paid services or create workload controls. Missing sources show unknown evidence. Initial collection remains usable before those sources are complete.

## 6. Enable a real remediation pilot

For the fastest workflow demonstration, use the local virtual-account demo first. For real actions, review [the architecture/monorepo contract](architecture.md) and choose:

- **Terraform (default):** onboard the existing resource's owning workspace in `governance/workspaces.yaml`. `examples/pilot-workspace` provides a versioning example for a disposable existing bucket. Establish its existing state and a clean baseline first. The runner refuses creates, deletes, replacements and unrelated changes.
- **Native API:** set `remediation_driver="api"` for allowlisted unmanaged resources. Three runbooks are available; unsupported items return a manual-action requirement. Native S3 public-access block affects the entire account, and default EBS encryption affects new volumes only.

For production approval or any Terraform driver action, set `remediation_repository="YOUR-OWNER/ns-infrastructure-terraform"` and `remediation_github_secret_arn`. Create the secret yourself in Secrets Manager (plain token or JSON `{"token":"..."}`), using a private-repo bot token with Contents/Pull requests read/write plus Checks/Actions/Metadata read and access to collaborator permissions. The function IAM policy covers `GetSecretValue` on that secret; use the default Secrets Manager key or separately grant its customer-managed KMS decrypt permission. Do not put tokens in tfvars or Git.

Create environments `remediation-plan` and `remediation-apply`, allowing only the protected base/default branch. Do not require human approval on these shared environments if dev/staging should run without it; production human approval is enforced through PR review. Set:

| Variable | remediation-plan | remediation-apply |
|---|---|---|
| AWS_ROLE_ARN | remediation_github_roles.plan | remediation_github_roles.apply |
| AWS_REGION | same Region | same |
| CENTRAL_ACCOUNT_ID | your account ID | same |
| TF_STATE_BUCKET | foundation state bucket | same |
| REMEDIATION_VALIDATE_FUNCTION | gov-portal-central-remediation_validate | same |
| REMEDIATION_CALLBACK_FUNCTION | not needed | gov-portal-central-remediation_callback |

Set repository `AWS_REMEDIATION_ENABLED=true`, enable main's plan check, and protect the production manifest paths with authorized reviewers. Dev/staging can merge without review after the check; optionally provide `GOV_AUTOMERGE_TOKEN` (a separate bot token allowed to enable auto-merge) to merge non-production requests automatically. Production never auto-merges through this step.

Finally set `enable_remediation=true` in the central tfvars and redeploy. Choose a current supported non-compliant finding in the dashboard, inspect its plan, then execute/request PR. Production waits for an authorized current human review and merge. Terraform actions always use the monorepo; native dev/staging can execute directly. Finding status changes when source evidence is re-evaluated, not merely because a workflow returned success.

## Existing deployments from the earlier profile

For a Region migration, deploy a separate regional foundation and portal state.
Use the same `resource_suffix` (for example `-ap-southeast-1`) in both stacks so
global IAM and CloudFront names remain distinct during the transition. Set
`enable_org_bootstrap_roles=false` for a standalone regional foundation. Copy the
versioned release artifacts to the destination Region's encrypted artifact bucket;
Lambda code artifacts must be stored in the function's Region. Recreate the portal
administrator in the new Cognito pool and enroll MFA again. Switch deployment
variables and disable the old collection schedules only after the new portal's
collection and live login checks pass. Retain the old state and evidence until a
separate cleanup is reviewed.

Deployment verification includes a live browser check after the collection smoke
test. It verifies that CloudFront's CSP permits the regional API, that framework
and change-request routes require authentication, and that the login button
reaches Cognito. Local Vite tests do not exercise the deployed response headers.
After installing the frontend dependencies and Playwright Chromium, run
`node ci/browser_smoke.mjs` against the outputs saved by `ci/release.py publish`.

Keep Lambda permissions in the canonical base-function-plus-qualifier form and
state JSON logging's existing INFO defaults explicitly. Provider 5.100 otherwise
reports unnecessary permission replacement and Lambda version changes. CloudFront
fixes the default certificate's policy at TLSv1; a custom domain/certificate uses
the configured TLS 1.2 policy. These normalizations should yield a no-change plan
after a successful deployment.

If you already deployed the previous `stage=dev` profile, keep that stage and state key while migrating deliberately. Changing to central renames resources and creates a separate state path; it is not an in-place migration. The legacy stage variables remain supported for that reason. New deployments use central. Delete unused test resources through a reviewed Terraform plan after migration; retained evidence/state buckets intentionally prevent accidental destruction.
