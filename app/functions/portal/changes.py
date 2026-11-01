"""Change requests, per-environment approval, execution evidence and failure recovery."""

from copy import deepcopy
from datetime import timedelta
import json
import os
import time
from uuid import uuid4
from portal.auth import AccessDenied
from portal.change_policy import (
    approval_environment,
    digest,
    find_runbook,
    manifest,
    runbooks,
    validate_target,
)
from portal.domain import parse_time, timestamp, utcnow

TERMINAL = {"SUCCEEDED", "FAILED", "PARTIAL_FAILURE", "MANUAL_ACTION_REQUIRED", "STALE_PLAN", "EXPIRED"}


def demo():
    return os.environ.get("PORTAL_MODE") == "demo"


def update(store, job_id, mutate):
    for _ in range(20):
        job = store.get("changes", job_id)
        if not job:
            raise ValueError("Change request not found")
        revision = job["revision"]
        mutate(job)
        job.update(revision=revision + 1, updated_at=timestamp())
        if store.compare_put("changes", job_id, job, revision):
            return job
    raise RuntimeError("Change request is busy; retry the operation")


def event(job, message, **details):
    job.setdefault("events", []).append({"at": timestamp(), "message": message, **details})
    job["events"] = job["events"][-100:]


def authorized(store, principal, job_id, mutate=False):
    job = store.get("changes", job_id)
    if not job:
        raise ValueError("Change request not found")
    for target in job["targets"]:
        account = store.get("accounts", target["account_id"])
        if not account or not principal.can_read(account):
            raise AccessDenied("Change request is outside your account scope")
        if mutate and not (principal.admin or account["owner_team"] in principal.teams):
            raise AccessDenied("Only an administrator or the owning team may request remediation")
    return job


def list_changes(store, principal):
    items = []
    for job in store.all("changes"):
        try:
            authorized(store, principal, job["id"])
            items.append(job)
        except (AccessDenied, ValueError):
            continue
    return sorted(items, key=lambda j: j["created_at"], reverse=True)


def active_waiver(store, target):
    return any(
        w["account_id"] == target["account_id"]
        and w["rule_id"] == target["rule_id"]
        and not w.get("revoked_at")
        and parse_time(w["expires_at"]) > utcnow()
        and (not w.get("resource") or w["resource"] == target["resource"])
        for w in store.all("waivers")
    )


def create(store, principal, body):
    requests = body.get("targets", [])
    if not isinstance(requests, list) or not 1 <= len(requests) <= 50:
        raise ValueError("Select between 1 and 50 findings")
    groups, seen = {}, set()
    batch_id = str(uuid4())
    for request in requests:
        account = store.get("accounts", request.get("account_id", ""))
        if (
            not account
            or not principal.can_read(account)
            or not (principal.admin or account["owner_team"] in principal.teams)
        ):
            raise AccessDenied("You may request changes only for accounts you own")
        finding = next(
            (
                f
                for f in store.findings(account["id"])
                if f["rule_id"] == request.get("rule_id")
                and f["resource"] == request.get("resource")
                and (not request.get("region") or f.get("region") == request["region"])
            ),
            None,
        )
        if not finding:
            raise ValueError("Finding no longer exists")
        validate_target(account, finding)
        runbook = find_runbook(finding)
        env = approval_environment(account)
        identity = (account["id"], finding["rule_id"], finding["resource"], finding.get("region", ""))
        if identity in seen:
            continue
        seen.add(identity)
        target = {
            "id": str(uuid4()),
            "account_id": account["id"],
            "account_name": account["name"],
            "environment": env,
            "ou_path": account.get("ou_path", ""),
            "rule_id": finding["rule_id"],
            "resource": finding["resource"],
            "region": finding.get("region") or os.environ.get("AWS_REGION", "ap-southeast-1"),
            "runbook_id": runbook["id"] if runbook else "terraform-configuration",
            "action": runbook["action"] if runbook else "terraform_configuration",
            "desired": deepcopy(runbook["desired"]) if runbook else {},
            "impact": runbook["impact"]
            if runbook
            else runbooks()["manual_controls"].get(
                finding["rule_id"], "Requires a reviewed workspace binding or an owner-managed runbook."
            ),
            "before": None,
        }
        if active_waiver(store, target):
            raise ValueError("An active governance exception excludes this resource from remediation")
        groups.setdefault(env, []).append(target)
    jobs = []
    for env, targets in groups.items():
        now = timestamp()
        job = {
            "id": str(uuid4()),
            "batch_id": batch_id,
            "revision": 0,
            "status": "PLANNING",
            "kind": "remediation",
            "created_at": now,
            "updated_at": now,
            "requested_by": principal.subject,
            "environment": env,
            "requires_approval": env == "production",
            "targets": targets,
            "driver": "api" if demo() else os.environ.get("REMEDIATION_DRIVER", "terraform"),
            "expires_at": (utcnow() + timedelta(hours=24)).isoformat(),
            "policy_version": runbooks()["version"],
            "outcomes": {},
            "events": [],
            "simulate_failure": bool(body.get("simulate_failure")) if demo() else False,
        }
        event(job, "Change requested from dashboard", subject=principal.subject)
        if not store.compare_put("changes", job["id"], job):
            raise RuntimeError("Duplicate request ID")
        if demo():
            prepare(store, job["id"])
        else:
            start(job["id"], "prepare")
        jobs.append(store.get("changes", job["id"]))
    return {"batch_id": batch_id, "items": jobs}


def create_exception(store, principal, waiver, revoke=False):
    account = store.get("accounts", waiver["account_id"])
    job_id = str(uuid4())
    target = {
        "id": str(uuid4()),
        "account_id": account["id"],
        "account_name": account["name"],
        "environment": approval_environment(account),
        "ou_path": account.get("ou_path", ""),
        "rule_id": waiver["rule_id"],
        "resource": waiver.get("resource") or account["id"],
        "region": os.environ.get("AWS_REGION", "ap-southeast-1"),
        "runbook_id": "governance-exception",
        "action": "waiver_revoke" if revoke else "waiver_create",
        "before": {},
        "desired": {"waiver": waiver},
        "impact": "Governance exception changes require a reviewed production PR.",
    }
    job = {
        "id": job_id,
        "batch_id": job_id,
        "revision": 0,
        "status": "PLANNED",
        "kind": "exception",
        "created_at": timestamp(),
        "updated_at": timestamp(),
        "requested_by": principal.subject,
        "environment": "production",
        "requires_approval": True,
        "targets": [target],
        "driver": "policy",
        "expires_at": (utcnow() + timedelta(hours=24)).isoformat(),
        "policy_version": runbooks()["version"],
        "outcomes": {},
        "events": [],
    }
    job["plan_hash"] = digest(manifest(job))
    event(job, "Production exception requested; it is not effective until an approved PR is applied")
    store.compare_put("changes", job_id, job)
    if demo():
        submit(store, job_id)
    else:
        start(job_id, "submit")
    return {"pending_approval": True, "request_id": job_id, "waiver_id": waiver["id"]}


def start(job_id, phase):
    from portal.aws import client

    return client("stepfunctions").start_execution(
        stateMachineArn=os.environ["REMEDIATION_STATE_MACHINE_ARN"],
        name=f"{phase}-{job_id}-{uuid4().hex[:8]}",
        input=json.dumps({"request_id": job_id, "phase": phase}),
    )


def prepare(store, job_id):
    from portal import remediation_actions as actions
    from portal.remediation_github import GitHub

    job = store.get("changes", job_id)
    targets = deepcopy(job["targets"])
    try:
        github = GitHub() if job["driver"] == "terraform" and not demo() else None
        for target in targets:
            account = store.get("accounts", target["account_id"])
            if actions.current_scope(account) != target["environment"]:
                raise ValueError("Account scope changed; create a fresh plan")
            if demo():
                target["before"] = {
                    key: False if isinstance(value, bool) else "Suspended"
                    for key, value in target["desired"].items()
                }
                if not target["desired"]:
                    raise ValueError(target["impact"])
            else:
                if github:
                    target["workspace"] = github.binding(target)
                if target["action"] == "terraform_configuration":
                    if not github:
                        raise ValueError(target["impact"])
                    target["before"] = {"configuration": target["workspace"]["before"]}
                else:
                    target["before"], managed = actions.inspect(target)
                    if managed and not github:
                        raise ValueError(
                            "Terraform/CloudFormation-owned resources require a monorepo workspace binding"
                        )

        def save(row):
            row["targets"] = targets
            row["plan_hash"] = digest(manifest(row))
            row["status"] = "PLANNED"
            event(row, "Immutable plan prepared; no changes applied", plan_hash=row["plan_hash"])
            if len(json.dumps(row).encode()) > 350000:
                raise ValueError("Plan too large; split this request into smaller batches")

        return update(store, job_id, save)
    except Exception as exc:
        return fail(
            store, job_id, str(exc), "MANUAL_ACTION_REQUIRED" if isinstance(exc, ValueError) else "FAILED"
        )


def fail(store, job_id, message, status="FAILED"):
    def save(row):
        row.update(status=status, error=message)
        event(row, message, status=status)

    return update(store, job_id, save)


def submit(store, job_id):
    from portal.remediation_github import GitHub

    job = store.get("changes", job_id)
    validate_plan(job)
    if job["status"] not in {"PLANNED", "SUBMITTED", "FAILED", "PARTIAL_FAILURE"}:
        raise ValueError("This change cannot be executed in its current state")
    if not demo() and os.environ.get("REMEDIATION_ENABLED") != "true":
        raise ValueError("Live remediation is disabled; enable it in the central Terraform configuration")
    if job["requires_approval"] or job["driver"] == "terraform":
        if demo():
            return update(
                store,
                job_id,
                lambda row: (
                    row.update(status="AWAITING_APPROVAL"),
                    event(row, "Demo: waiting for a simulated approved PR; no GitHub request sent"),
                ),
            )
        pr = job.get("pull_request") or GitHub().create_pr(job)
        return update(
            store,
            job_id,
            lambda row: (
                row.update(
                    status="AWAITING_APPROVAL" if row["requires_approval"] else "AWAITING_PLAN",
                    pull_request=pr,
                ),
                event(row, "Monorepo change request opened", pull_request=pr["url"]),
            ),
        )
    return update(
        store,
        job_id,
        lambda row: (row.update(status="READY"), event(row, "Dev/staging change requires no human approval")),
    )


def validate_plan(job):
    if parse_time(job["expires_at"]) <= utcnow():
        raise ValueError("Plan expired; prepare a new request")
    if digest(manifest(job)) != job.get("plan_hash"):
        raise ValueError("Plan contents changed after planning")


def approve(store, job_id, plan_hash, demo_approval=False):
    from portal.remediation_github import GitHub

    job = store.get("changes", job_id)
    validate_plan(job)
    if plan_hash != job["plan_hash"] or job["status"] not in {
        "AWAITING_APPROVAL",
        "AWAITING_PLAN",
        "FAILED",
        "PARTIAL_FAILURE",
    }:
        raise ValueError("Approval does not match a pending immutable plan")
    if demo_approval:
        if not demo():
            raise AccessDenied("Simulated approvals are unavailable in AWS")
        evidence = {"simulation": True, "plan_hash": plan_hash}
    else:
        evidence = GitHub().verify(job)
    return update(
        store,
        job_id,
        lambda row: (
            row.update(status="READY", approval=evidence),
            event(
                row,
                "Production PR verified"
                if row["requires_approval"]
                else "Automated plan and merged change verified",
                **evidence,
            ),
        ),
    )


def execute_target(store, job_id, target_id):
    from portal import remediation_actions as actions
    from portal.remediation_github import GitHub
    from portal.service import refresh_evaluation

    job = store.get("changes", job_id)
    validate_plan(job)
    if job["status"] not in {"READY", "APPLYING"} or job["driver"] not in {"api", "policy"}:
        raise ValueError("Change is not authorized for API execution")
    target = next(t for t in job["targets"] if t["id"] == target_id)
    if job.get("outcomes", {}).get(target_id, {}).get("status") == "SUCCEEDED":
        return {"request_id": job_id, "target_id": target_id, "status": "SUCCEEDED"}
    account = store.get("accounts", target["account_id"])
    if actions.current_scope(account) != target["environment"]:
        return target_result(
            store,
            job_id,
            target_id,
            "STALE_PLAN",
            "OU/environment changed; production policy must be evaluated again",
        )
    if job["kind"] != "exception" and active_waiver(store, target):
        return target_result(store, job_id, target_id, "EXCLUDED", "A governance exception became active")
    if job["requires_approval"]:
        if not job.get("approval"):
            raise AccessDenied("Production cannot execute without a verified PR")
        if not demo():
            GitHub().verify(job)
    lock = digest([target["account_id"], target["region"], target["resource"], target["action"]])
    owner = str(uuid4())
    if not store.acquire_lock(lock, owner, int(time.time()) + 600):
        raise RuntimeError("Resource is locked by another change request")
    try:
        update(store, job_id, lambda row: row.update(status="APPLYING"))
        attempts = job.get("outcomes", {}).get(target_id, {}).get("attempts", 0) + 1
        if job["kind"] == "exception":
            waiver = deepcopy(target["desired"]["waiver"])
            if target["action"] == "waiver_revoke":
                waiver["revoked_at"] = timestamp()
            elif parse_time(waiver["expires_at"]) <= utcnow():
                raise ValueError("Exception expired while awaiting approval")
            waiver["approved_by"] = job.get("approval", {}).get("reviewers", ["demo-approved-pr"])
            store.put("waivers", waiver["id"], waiver)
            refresh_evaluation(store, target["account_id"])
            return target_result(
                store, job_id, target_id, "SUCCEEDED", "Approved governance exception applied", attempts
            )
        if demo():
            if job.get("simulate_failure") and attempts == 1:
                return target_result(
                    store,
                    job_id,
                    target_id,
                    "FAILED",
                    "Simulated deployment failure; no AWS mutation occurred",
                    attempts,
                )
            observations = store.get("observations", target["account_id"]) or []
            for result in observations:
                if result["rule_id"] == target["rule_id"] and result["resource"] == target["resource"]:
                    result["status"] = "PASS"
            store.put("observations", target["account_id"], observations)
            refresh_evaluation(store, target["account_id"])
            return target_result(
                store,
                job_id,
                target_id,
                "SUCCEEDED",
                "Demo runbook applied and result re-evaluated",
                attempts,
            )
        actual, managed = actions.inspect(target)
        if managed:
            raise ValueError("Resource is now IaC-owned; route this change through Terraform")
        if actual == target["desired"]:
            return target_result(store, job_id, target_id, "SUCCEEDED", "Already in desired state", attempts)
        if actual != target["before"]:
            return target_result(
                store,
                job_id,
                target_id,
                "STALE_PLAN",
                "Configuration drifted after planning; review a fresh plan",
                attempts,
            )
        actions.apply(target)
        after, _ = actions.inspect(target)
        if after != target["desired"]:
            raise ValueError(
                "Post-apply verification failed; inspect the captured before state and create a new plan"
            )
        return target_result(
            store,
            job_id,
            target_id,
            "SUCCEEDED",
            "AWS state independently verified; Security Hub re-evaluation requested",
            attempts,
        )
    except Exception as exc:
        return target_result(store, job_id, target_id, "FAILED", str(exc), locals().get("attempts", 1))
    finally:
        store.release_lock(lock, owner)


def target_result(store, job_id, target_id, status, message, attempts=1):
    def save(row):
        row.setdefault("outcomes", {})[target_id] = {
            "status": status,
            "message": message,
            "at": timestamp(),
            "attempts": attempts,
        }
        event(row, message, target_id=target_id, status=status)

    update(store, job_id, save)
    return {"request_id": job_id, "target_id": target_id, "status": status}


def finalize(store, job_id):
    def save(row):
        outcomes = [row.get("outcomes", {}).get(t["id"], {}).get("status", "FAILED") for t in row["targets"]]
        passed = outcomes.count("SUCCEEDED")
        row["status"] = "SUCCEEDED" if passed == len(outcomes) else "PARTIAL_FAILURE" if passed else "FAILED"
        event(row, "Execution finished", status=row["status"], succeeded=passed, total=len(outcomes))

    result = update(store, job_id, save)
    if not demo() and any(v["status"] == "SUCCEEDED" for v in result.get("outcomes", {}).values()):
        from portal.aws import client

        for account_id in {t["account_id"] for t in result["targets"]}:
            client("stepfunctions").start_execution(
                stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                input=json.dumps({"account_id": account_id, "scan_id": str(uuid4())}),
            )
    return result


def run_demo(store, job_id):
    job = store.get("changes", job_id)
    for target in job["targets"]:
        execute_target(store, job_id, target["id"])
    return finalize(store, job_id)


def route(store, principal, method, path, body):
    parts = path.strip("/").split("/")
    if method == "GET" and path == "/changes":
        return 200, {
            "items": list_changes(store, principal),
            "mode": "demo" if demo() else "aws",
            "live_enabled": demo() or os.environ.get("REMEDIATION_ENABLED") == "true",
            "runbooks": runbooks(),
        }
    if method == "POST" and path == "/changes":
        return 202, create(store, principal, body)
    if len(parts) >= 2 and parts[0] == "changes":
        job = authorized(store, principal, parts[1], mutate=method != "GET")
        if method == "GET" and len(parts) == 2:
            return 200, job
        if method == "POST" and len(parts) == 3 and parts[2] in {"execute", "retry"}:
            validate_plan(job)
            if demo():
                if job["requires_approval"] and job.get("approval"):
                    update(store, job["id"], lambda row: row.update(status="READY"))
                    return 202, run_demo(store, job["id"])
                job = submit(store, job["id"])
                return 202, run_demo(store, job["id"]) if job["status"] == "READY" else job
            if job["driver"] == "terraform" and job["status"] in {"FAILED", "PARTIAL_FAILURE"}:
                raise ValueError("Terraform recovery requires a new plan and PR for the failed targets")
            if os.environ.get("REMEDIATION_ENABLED") != "true":
                raise ValueError("Live remediation is disabled in the central configuration")
            if job["status"] not in {"PLANNED", "FAILED", "PARTIAL_FAILURE"}:
                raise ValueError("Request is already submitted or complete")
            if job["requires_approval"] and job.get("approval") and job["driver"] != "terraform":
                from portal.remediation_github import GitHub

                GitHub().verify(job)
                update(store, job["id"], lambda row: row.update(status="READY"))
                job = store.get("changes", job["id"])
                from portal.aws import client

                client("stepfunctions").start_execution(
                    stateMachineArn=os.environ["REMEDIATION_STATE_MACHINE_ARN"],
                    input=json.dumps(
                        {
                            "request_id": job["id"],
                            "phase": "apply",
                            "targets": [
                                {"request_id": job["id"], "target_id": t["id"]} for t in job["targets"]
                            ],
                        }
                    ),
                )
            else:
                update(store, job["id"], lambda row: row.update(status="SUBMITTED"))
                start(job["id"], "submit")
            return 202, store.get("changes", job["id"])
        if method == "POST" and len(parts) == 3 and parts[2] == "demo-approve" and demo():
            if not principal.admin:
                raise AccessDenied("Only demo administrators can simulate approval")
            approve(store, job["id"], job["plan_hash"], demo_approval=True)
            return 202, run_demo(store, job["id"])
    raise ValueError("Unknown change request operation")
