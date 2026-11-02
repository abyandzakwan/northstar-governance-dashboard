"""Remediation scope and immutable plan policy. No AWS mutations in this module."""

from hashlib import sha256
from pathlib import Path, PurePosixPath
import json
import os
import re
import yaml
from portal.domain import catalog


def runbooks():
    path = Path(
        os.environ.get(
            "REMEDIATIONS_PATH", Path(__file__).resolve().parents[3] / "governance/remediations.yaml"
        )
    )
    return yaml.safe_load(path.read_text())


def approval_environment(account):
    # An Environment tag may add protection; it may never downgrade a Production OU.
    parts = {p.lower() for p in account.get("ou_path", "").split("/")}
    env = account.get("environment", "unclassified").lower()
    if parts & {"production", "prod"} or env in {"production", "prod"}:
        return "production"
    if env in {"dev", "development"}:
        return "dev"
    if env in {"testing", "staging", "stg", "stage"}:
        return "testing"
    return "unclassified"


def find_runbook(finding):
    return next(
        (
            r
            for r in runbooks()["runbooks"]
            if finding.get("rule_id") in r["rules"] or finding.get("security_control_id") in r["controls"]
        ),
        None,
    )


def digest(value):
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def manifest(job):
    return {
        "request_id": job["id"],
        "policy_version": job["policy_version"],
        "environment": job["environment"],
        "requires_approval": job["requires_approval"],
        "driver": job["driver"],
        "expires_at": job["expires_at"],
        "targets": job["targets"],
    }


def validate_target(account, finding):
    if finding.get("sample_data") or finding.get("source") == "securityhub-sample":
        raise ValueError("Sample findings cannot be used for live remediation")
    if account.get("closed_at") or approval_environment(account) == "unclassified":
        raise ValueError("Closed or unclassified accounts cannot be remediated")
    if finding["status"] not in {"FAIL", "WARN"}:
        raise ValueError(
            "Only current non-compliant findings can be remediated; unknown and waived evidence is excluded"
        )
    rule = next((r for r in catalog()["rules"] if r["id"] == finding["rule_id"]), None)
    if not rule and finding.get("source") != "securityhub":
        raise ValueError("Unsupported finding source")


def safe_repo_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        raise ValueError("Invalid monorepo path")
    if path.parts[0] in {".git", ".github"}:
        raise ValueError("Remediation cannot modify repository control files")
    return value


def set_variable(document, path, value):
    if not path or not all(isinstance(p, str) and re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in path):
        raise ValueError("Invalid Terraform variable binding")
    cursor = document
    for key in path[:-1]:
        cursor = cursor[key]
        if not isinstance(cursor, dict):
            raise ValueError("Variable binding must point into an existing JSON object")
    if path[-1] not in cursor or type(cursor[path[-1]]) is not type(value):
        raise ValueError("Variable binding must match an existing value and type")
    cursor[path[-1]] = value
