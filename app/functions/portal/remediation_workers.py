"""Lambda entry points. Approval callbacks are IAM-only, never exposed through HTTP routes."""

import json
import os
from portal import changes
from portal.store import get_store


def planner(event, context):
    store = get_store()
    job_id = event["request_id"]
    try:
        job = (
            changes.prepare(store, job_id)
            if event.get("phase") == "prepare"
            else changes.submit(store, job_id)
        )
        return {
            "request_id": job_id,
            "status": job["status"],
            "driver": job["driver"],
            "targets": [{"request_id": job_id, "target_id": t["id"]} for t in job["targets"]],
        }
    except Exception as exc:
        job = changes.fail(store, job_id, str(exc))
        return {"request_id": job_id, "status": job["status"], "driver": job["driver"], "targets": []}


def applier(event, context):
    return changes.execute_target(get_store(), event["request_id"], event["target_id"])


def finalizer(event, context):
    job = changes.finalize(get_store(), event["request_id"])
    return {"request_id": job["id"], "status": job["status"]}


def approval_callback(event, context):
    from portal.aws import client
    from portal.change_policy import digest, manifest
    from portal.remediation_github import GitHub

    store = get_store()
    job = store.get("changes", event["request_id"])
    changes.validate_plan(job)
    if event["plan_hash"] != job["plan_hash"] or digest(manifest(job)) != job["plan_hash"]:
        raise ValueError("Callback does not match the immutable request")
    operation = event.get("operation", "approve")
    if operation == "validate":
        GitHub().verify(job, require_merged=False)
        return {
            "valid": True,
            "plan_hash": job["plan_hash"],
            "manifest": manifest(job),
            "base_sha": job["pull_request"]["base_sha"],
        }
    if operation == "approve" and os.environ.get("REMEDIATION_ENABLED") != "true":
        raise ValueError("Live remediation is disabled")
    if operation == "approve":
        if job["status"] == "SUCCEEDED":
            raise ValueError("Change already completed")
        workflow_run = event.get("workflow_run_id")
        if job["driver"] == "terraform":
            if not workflow_run or (job.get("workflow_run_id") and job["workflow_run_id"] != workflow_run):
                raise ValueError(
                    "A different workflow already owns this Terraform apply; prepare a new request"
                )
            changes.update(store, job["id"], lambda row: row.update(workflow_run_id=workflow_run))
        from portal.remediation_actions import current_scope

        for target in job["targets"]:
            if current_scope(store.get("accounts", target["account_id"])) != target["environment"]:
                changes.fail(store, job["id"], "OU/environment changed after planning", "STALE_PLAN")
                raise ValueError("OU/environment changed; prepare a new plan")
            if job["kind"] != "exception" and changes.active_waiver(store, target):
                raise ValueError("A governance exception became active; prepare a new plan")
        if job["status"] in {"APPLYING", "READY"}:
            GitHub().verify(job)
            if job["driver"] == "terraform":
                return {"status": job["status"], "driver": job["driver"]}
        else:
            job = changes.approve(store, job["id"], job["plan_hash"])
        if job["driver"] != "terraform":
            from botocore.exceptions import ClientError

            try:
                client("stepfunctions").start_execution(
                    name="approved-" + job["id"],
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
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ExecutionAlreadyExists":
                    raise
        else:
            changes.update(store, job["id"], lambda row: row.update(status="APPLYING"))
        return {"status": job["status"], "driver": job["driver"]}
    if operation == "complete" and job["driver"] == "terraform":
        if not event.get("workflow_run_id") or event["workflow_run_id"] != job.get("workflow_run_id"):
            raise ValueError("Completion belongs to a different workflow")
        GitHub().verify(job)
        if job["status"] not in {"APPLYING", "SUCCEEDED", "FAILED", "PARTIAL_FAILURE"}:
            raise ValueError("Terraform apply was not authorized")
        outcomes = event.get("outcomes", {})
        if set(outcomes) != {t["id"] for t in job["targets"]}:
            raise ValueError("Every planned target must have a result")
        for target_id, result in outcomes.items():
            if result["status"] not in {"SUCCEEDED", "FAILED"}:
                raise ValueError("Invalid Terraform outcome")
            changes.target_result(
                store,
                job["id"],
                target_id,
                result["status"],
                result.get("message", "Terraform workflow completed")[:2000],
            )
        return {"status": changes.finalize(store, job["id"])["status"]}
    raise ValueError("Unknown callback operation")


def validate_callback(event, context):
    """Separate IAM boundary: the plan identity can validate, never approve or complete."""
    return approval_callback({**event, "operation": "validate"}, context)
