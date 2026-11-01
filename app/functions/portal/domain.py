"""Pure governance policy, shared by collectors, API and the local demo."""

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import os
import yaml

ENVIRONMENTS = ("dev", "testing", "production", "unclassified")


def utcnow():
    return datetime.now(timezone.utc)


def timestamp():
    return utcnow().isoformat()


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


@lru_cache
def catalog():
    path = Path(os.environ.get("RULES_PATH", Path(__file__).resolve().parents[3] / "governance/rules.yaml"))
    with path.open() as stream:
        data = yaml.safe_load(stream)
    ids = set()
    for rule in data["rules"]:
        if rule["id"] in ids or set(rule["severity"]) != {"dev", "testing", "production"}:
            raise ValueError("Rules require unique IDs and all three severity profiles")
        if not set(rule["severity"].values()) <= {"warn", "fail", "critical", "n/a"}:
            raise ValueError("Unsupported severity")
        if not set(rule["frameworks"]) <= set(data["frameworks"]):
            raise ValueError("Unknown framework mapping")
        ids.add(rule["id"])
    return data


def resolve_environment(tags, ou_path):
    explicit = tags.get("Environment", "").strip().lower()
    aliases = {
        "stg": "testing",
        "staging": "testing",
        "stage": "testing",
        "prod": "production",
        "development": "dev",
    }
    explicit = aliases.get(explicit, explicit)
    if explicit:
        return explicit if explicit in ENVIRONMENTS[:3] else "unclassified"
    parts = [aliases.get(p.lower(), p.lower()) for p in ou_path.split("/")]
    for env in ("production", "testing", "dev"):
        if env in parts:
            return env
    return "unclassified"


def evaluate(account, observations, waivers=(), now=None):
    now = now or utcnow()
    env = account["environment"]
    results = []
    for rule in catalog()["rules"]:
        severity = rule["severity"].get(env, "fail")
        evidence = [r for r in observations if r["rule_id"] == rule["id"]]
        if not evidence:
            evidence = [
                {
                    "rule_id": rule["id"],
                    "resource": account["id"],
                    "status": "UNKNOWN",
                    "detail": "No evidence returned. Check source coverage and collector permissions.",
                }
            ]
        for item in evidence:
            status = item["status"]
            if status not in {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}:
                status = "UNKNOWN"
            if severity == "n/a":
                status = "NOT_APPLICABLE"
            elif status == "FAIL" and severity == "warn":
                status = "WARN"
            waiver = next(
                (
                    w
                    for w in waivers
                    if w["account_id"] == account["id"]
                    and w["rule_id"] == rule["id"]
                    and not w.get("revoked_at")
                    and parse_time(w["expires_at"]) > now
                    and (not w.get("resource") or w["resource"] == item["resource"])
                ),
                None,
            )
            if waiver and status in {"FAIL", "WARN"}:
                status = "ACCEPTED_RISK"
            results.append(
                {
                    **item,
                    "account_id": account["id"],
                    "environment": env,
                    "title": rule["title"],
                    "severity": severity,
                    "source": item.get("evidence_source", rule["source"]),
                    "frameworks": {**rule["frameworks"], **item.get("frameworks", {})},
                    "status": status,
                    "observed_status": item["status"],
                    "evaluated_at": now.isoformat(),
                    "waiver_id": waiver["id"] if waiver else None,
                }
            )
    known = {r["id"] for r in catalog()["rules"]}
    for item in observations:
        if item["rule_id"] in known or not item["rule_id"].startswith("SECURITY_HUB::"):
            continue
        status = (
            item["status"] if item["status"] in {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"} else "UNKNOWN"
        )
        severity = {
            "CRITICAL": "critical",
            "HIGH": "fail",
            "MEDIUM": "fail",
            "LOW": "warn",
            "INFORMATIONAL": "warn",
        }.get(item.get("aws_severity"), "fail")
        waiver = next(
            (
                w
                for w in waivers
                if w["account_id"] == account["id"]
                and w["rule_id"] == item["rule_id"]
                and not w.get("revoked_at")
                and parse_time(w["expires_at"]) > now
                and (not w.get("resource") or w["resource"] == item["resource"])
            ),
            None,
        )
        results.append(
            {
                **item,
                "account_id": account["id"],
                "environment": env,
                "source": "securityhub",
                "title": item.get("title", item["rule_id"]),
                "severity": severity,
                "status": "ACCEPTED_RISK" if waiver and status == "FAIL" else status,
                "observed_status": status,
                "waiver_id": waiver["id"] if waiver else None,
                "frameworks": item.get("frameworks", {}),
                "evaluated_at": now.isoformat(),
            }
        )
    if env == "unclassified":
        results.append(
            {
                "rule_id": "ENVIRONMENT_CLASSIFIED",
                "account_id": account["id"],
                "environment": env,
                "resource": account["id"],
                "status": "FAIL",
                "severity": "critical",
                "source": "organizations",
                "frameworks": {},
                "title": "Account has no valid environment",
                "evaluated_at": now.isoformat(),
            }
        )
    return results, score(results, env)


def score(results, environment):
    applicable = [r for r in results if r["status"] not in {"NOT_APPLICABLE", "ACCEPTED_RISK"}]
    pct = (
        round(100 * sum(r["status"] == "PASS" for r in applicable) / len(applicable), 1) if applicable else 0
    )
    critical = sum(r["severity"] == "critical" and r["status"] in {"FAIL", "UNKNOWN"} for r in results)
    profile = catalog()["profiles"][environment]
    return {
        "score_pct": pct,
        "critical_count": critical,
        "fail_count": sum(r["status"] == "FAIL" for r in results),
        "unknown_count": sum(r["status"] == "UNKNOWN" for r in results),
        "accepted_risk_count": sum(r["status"] == "ACCEPTED_RISK" for r in results),
        "healthy": pct >= profile["threshold"]
        and (profile["max_critical"] is None or critical <= profile["max_critical"]),
        "profile_version": catalog()["version"],
    }


def framework_report(framework, accounts, findings):
    definition = catalog()["frameworks"][framework]
    controls = {}
    for account in accounts:
        for result in findings(account["id"]):
            for control in result.get("frameworks", {}).get(framework, []):
                controls.setdefault(control, []).append(result)
    rows = []
    for control, results in sorted(controls.items()):
        applicable = [r for r in results if r["status"] not in {"NOT_APPLICABLE", "ACCEPTED_RISK"}]
        passed = sum(r["status"] == "PASS" for r in applicable)
        rows.append(
            {
                "control": control,
                "pass_pct": round(100 * passed / len(applicable), 1) if applicable else None,
                "passed": passed,
                "total": len(applicable),
                "accepted_risk": sum(r["status"] == "ACCEPTED_RISK" for r in results),
                "failing_accounts": sorted({r["account_id"] for r in applicable if r["status"] != "PASS"}),
            }
        )
    return {**definition, "id": framework, "accounts_in_scope": len(accounts), "controls": rows}


def summaries(results):
    """Denormalized counters keep organization dashboards independent of the number of resources."""
    rules, frameworks = {}, {}
    for result in results:
        row = rules.setdefault(result["rule_id"], {"issues": 0, "passing": 0, "total": 0})
        row["issues"] += int(result["status"] in {"FAIL", "WARN", "UNKNOWN"})
        row["passing"] += int(result["status"] == "PASS")
        row["total"] += 1
        for framework, controls in result["frameworks"].items():
            for control in controls:
                counter = frameworks.setdefault(framework, {}).setdefault(
                    control, {"passed": 0, "total": 0, "accepted_risk": 0}
                )
                counter["passed"] += int(result["status"] == "PASS")
                counter["total"] += int(result["status"] not in {"NOT_APPLICABLE", "ACCEPTED_RISK"})
                counter["accepted_risk"] += int(result["status"] == "ACCEPTED_RISK")
    return {"rule_summary": rules, "framework_summary": frameworks}


def framework_from_summaries(framework, accounts, definition=None):
    controls = {}
    for account in accounts:
        for control, counter in account.get("framework_summary", {}).get(framework, {}).items():
            row = controls.setdefault(
                control,
                {"control": control, "passed": 0, "total": 0, "accepted_risk": 0, "failing_accounts": []},
            )
            for key in ("passed", "total", "accepted_risk"):
                row[key] += counter[key]
            if counter["passed"] < counter["total"]:
                row["failing_accounts"].append(account["id"])
    for row in controls.values():
        row["pass_pct"] = round(100 * row["passed"] / row["total"], 1) if row["total"] else None
    return {
        **(definition or catalog()["frameworks"][framework]),
        "id": framework,
        "accounts_in_scope": len(accounts),
        "controls": [controls[k] for k in sorted(controls)],
    }
