# One test account, without SSO

Deploy **one central portal** in your test account. `deployment_mode=standalone` collects only that account; `auth_mode=cognito` provides username/password plus authenticator-app MFA. No Organizations, SSO, member roles, StackSets, domain or private certificate is required. CloudFront provides HTTPS. Local preview needs no AWS account at all.

## 1. Configure the central profile

Confirm your AWS CLI identity:

```sh
aws sts get-caller-identity --query Account --output text
```

Edit `infra/envs/central.tfvars`: replace `111111111111` with your account ID and choose the same Region for `region` and `collection_regions`. Leave `stage="central"`, `deployment_mode="standalone"`, `auth_mode="cognito"`, and `enable_remediation=false` for initial collection. The standalone account's governance environment can be dev, testing, or production without changing the portal deployment stage. Using production exercises the real approval policy but does not create an OU.

## 2. Bootstrap state and GitHub OIDC

Use an authorized bootstrap identity. Set the actual owner of your private monorepo in the command below:

```sh
terraform -chdir=infra/stacks/foundation init
terraform -chdir=infra/stacks/foundation plan \
  -var='stage=central' \
  -var='account_id=YOUR_12_DIGIT_ACCOUNT_ID' \
  -var='region=ap-southeast-1' \
  -var='repository=YOUR-OWNER/ns-infrastructure-terraform' \
  -out=foundation.tfplan
terraform -chdir=infra/stacks/foundation apply foundation.tfplan
terraform -chdir=infra/stacks/foundation output
```

This creates private, encrypted, versioned state/artifact buckets, an OIDC provider, central plan/deploy roles, and separate remediation plan/apply roles. If your account already has GitHub's OIDC provider, also pass `existing_github_provider_arn` to the plan. Keep foundation state protected and backed up; it is excluded from Git. Do not commit any state, binary plans or tokens.

## 3. Configure the deployment workflow

Create GitHub environments `central-plan` and `central`, restricted to main. Set the following environment variables from the foundation outputs:

| Variable | central-plan | central |
|---|---|---|
| AWS_ROLE_ARN | plan_role_arn | deploy_role_arn |
| AWS_REGION | ap-southeast-1 | same |
| TF_STATE_BUCKET | state_bucket | same |
| ARTIFACT_BUCKET | artifact_bucket | same |

Set repository Actions variables `AWS_DEPLOYMENT_ENABLED=true` and `PORTAL_DEPLOYMENT_MODE=standalone`. Configure the `central` environment's deployment review policy as appropriate. These approvals concern deploying the tool itself; production-resource approvals happen separately through remediation PRs.

Replace example CODEOWNERS with your real GitHub users/teams. Push your configuration and code to main or run **Actions → Deploy central governance portal**. The workflow builds once, saves a plan, applies that exact plan, publishes the frontend, and runs a collection smoke check. There is one portal and one state key, `portal/central.tfstate`.

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

If you already deployed the previous `stage=dev` profile, keep that stage and state key while migrating deliberately. Changing to central renames resources and creates a separate state path; it is not an in-place migration. The legacy stage variables remain supported for that reason. New deployments use central. Delete unused test resources through a reviewed Terraform plan after migration; retained evidence/state buckets intentionally prevent accidental destruction.
