# AWS Governance & Compliance Portal

One central AWS application monitors dev, staging and production accounts. React/TypeScript dashboard, Python Lambda, Step Functions, DynamoDB, Security Hub, Config, S3/Athena and Terraform. **GitHub Actions replaces GitLab CI.** The user's revised central-account and remediation requirements supersede the original PDF's deployment assumptions.

## Try the functional demo

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest ruff
npm --prefix app/frontend ci
PYTHONPATH=app/functions PORTAL_MODE=demo .venv/bin/python -m portal.local
```

In another terminal:

```sh
npm --prefix app/frontend run dev
```

Run one API server at a time. If port 8000 is busy, use the existing demo or stop its terminal with Ctrl+C before restarting. To choose another API port, set `LOCAL_PORT=8001` on the API command and `PORTAL_API_URL=http://127.0.0.1:8001` on the frontend command.

Open **http://127.0.0.1:5173**. The demo has 37 virtual accounts. In **Remediation & changes**, select a failed/warning S3 public-access finding, prepare a plan, and execute. Dev/staging executes on dashboard action. Production waits for **Simulate approved PR**. Enable **Simulate first deployment failure** to exercise failure evidence and retry. Unsupported controls show the required manual or Terraform action. Production risk exceptions and revocations also wait for simulated PR approval.

The demo writes local state in `.local/state.json` and downloads real CSV/JSON reports. It makes no AWS/GitHub writes. Remove that state file to reset. Browser tests use fresh in-memory state on isolated local ports.

## What is implemented

- One central controller, cross-account read roles, optional separately trusted bounded write roles, and per-environment policy. A Production OU always requires approval even if its Environment tag says dev.
- Security Hub findings for all reported unified control IDs; actual AWS standard associations, including CIS/FSBP and other standards returned by AWS. Missing evidence stays unknown. Internal CIS/ISO mappings are separately labeled technical mappings requiring Security review.
- Dashboard-initiated batches across up to 50 findings, split by environment. Immutable plans record before/desired state, impact, expiry and hash. No event-triggered automatic writes.
- Three allowlisted native runbooks: account S3 public access block, S3 bucket versioning, and default EBS encryption for new volumes. Additional Terraform changes require explicit reviewed workspace bindings. Root MFA, destructive migrations and unsupported fixes remain manual.
- Terraform-owned changes update `owner/ns-infrastructure-terraform`. Workflow validates only approved variable patches, saves the binary plan and applies that exact plan after merge. Production requires a current authorized human PR review. Dev/staging requires no human approval; optional auto-merge is configurable.
- Drift checks before native mutation; Terraform state/configuration checks and stale-plan rejection; resource leases, revision checks, bounded concurrency, per-target results, failed-target retry for native actions and fresh-plan recovery for Terraform.
- Scoped RBAC, production exception PRs, expiring waivers, audit timeline, daily scans, event-driven re-evaluation, deployment/drift evidence, exports and trends.

## Deploy with your one account and no SSO

Follow [the single-account guide](docs/standalone.md). Use `infra/envs/central.tfvars`: `stage=central`, `deployment_mode=standalone`, and native Cognito login with authenticator MFA. It needs no Organizations, OUs, SSO, custom domain or StackSets. Live remediation is disabled by default.

Later, retain the same central app and enroll member accounts using [the organization guide](docs/deployment.md). See [the revised architecture and remediation contract](docs/architecture.md) and [operations](docs/operations.md).

## Verify

```sh
.venv/bin/ruff check app/functions tests ci
.venv/bin/pytest -q
PYTHONPATH=app/functions .venv/bin/python ci/validate_rules.py
npm --prefix app/frontend test
npm --prefix app/frontend run build
terraform fmt -check -recursive infra
```

After `terraform init -backend=false` in each infrastructure stack, run `terraform validate` and `terraform test` (mocked providers, no AWS deployment). GitHub Actions also runs TFLint, encryption/recovery policy checks, and Playwright. For local browser tests, activate `.venv`, install Playwright Chromium, and run `npm --prefix app/frontend run test:e2e`.

Live AWS collection, PR creation, Cognito sign-in and cross-account applies require your actual account, GitHub owner, reviewed bindings and credentials; local/mock verification does not establish that those integrations are deployed.
