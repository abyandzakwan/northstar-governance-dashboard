from datetime import timedelta
import pytest
from portal.domain import catalog, evaluate, resolve_environment, summaries, framework_from_summaries, utcnow


def account(env="production"):
    return {"id": "123456789012", "name": "test", "environment": env, "owner_team": "payments"}


def evidence(status="PASS"):
    return [{"rule_id": r["id"], "resource": "resource", "status": status} for r in catalog()["rules"]]


@pytest.mark.parametrize(
    "tags,path,expected",
    [
        ({"Environment": "dev"}, "Root/Production", "dev"),
        ({}, "Root/Workloads/Production/Payments", "production"),
        ({"Environment": "typo"}, "Root/Production", "unclassified"),
        ({}, "Root/DevelopmentSandbox", "unclassified"),
        ({}, "Root/Testing", "testing"),
    ],
)
def test_mapping_precedence(tags, path, expected):
    assert resolve_environment(tags, path) == expected


def test_missing_evidence_cannot_appear_healthy():
    results, score = evaluate(account(), [])
    assert all(r["status"] == "UNKNOWN" for r in results)
    assert score["score_pct"] == 0
    assert not score["healthy"]


def test_environment_severity_and_not_applicable():
    observations = evidence()
    for item in observations:
        if item["rule_id"] in {"S3_PUBLIC_ACCESS_BLOCKED", "BACKUP_PLAN_ATTACHED"}:
            item["status"] = "FAIL"
    dev, _ = evaluate(account("dev"), observations)
    prod, score = evaluate(account(), observations)
    assert next(r for r in dev if r["rule_id"] == "S3_PUBLIC_ACCESS_BLOCKED")["status"] == "WARN"
    assert next(r for r in dev if r["rule_id"] == "BACKUP_PLAN_ATTACHED")["status"] == "NOT_APPLICABLE"
    assert score["critical_count"] == 1
    assert not score["healthy"]
    assert next(r for r in prod if r["rule_id"] == "S3_PUBLIC_ACCESS_BLOCKED")["severity"] == "critical"


def test_waiver_scope_expiry_and_framework_counters():
    observations = evidence()
    observations[0]["status"] = "FAIL"
    now = utcnow()
    waiver = {
        "id": "w1",
        "account_id": account()["id"],
        "rule_id": observations[0]["rule_id"],
        "resource": "resource",
        "expires_at": (now + timedelta(days=1)).isoformat(),
    }
    results, score = evaluate(account(), observations, [waiver], now)
    assert results[0]["status"] == "ACCEPTED_RISK"
    assert results[0]["observed_status"] == "FAIL"
    assert score["score_pct"] == 100
    assert score["accepted_risk_count"] == 1
    expired, score = evaluate(account(), observations, [waiver], now + timedelta(days=2))
    assert expired[0]["status"] == "FAIL"
    assert score["critical_count"] == 1
    report = framework_from_summaries("cis-aws", [{**account(), **summaries(results)}])
    control = next(c for c in report["controls"] if c["control"] == "3.1")
    assert control["accepted_risk"] == 1 and control["total"] == 0 and control["pass_pct"] is None


def test_all_waived_or_not_applicable_is_not_a_verified_pass():
    from portal.domain import score

    assert not score([{"status": "ACCEPTED_RISK", "severity": "fail"}], "production")["healthy"]
