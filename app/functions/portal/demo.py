"""Deterministic synthetic evidence. Never used by AWS handlers."""

from datetime import timedelta
from random import Random
from portal.domain import catalog, evaluate, summaries, timestamp, utcnow


def seed(count=36):
    rng = Random(42)
    data = {
        k: {} for k in ("accounts", "findings", "observations", "deployments", "waivers", "reports", "trends")
    }
    teams = ["platform", "payments", "data", "experience"]
    names = [
        "core-platform",
        "payment-services",
        "data-analytics",
        "customer-experience",
        "booking-engine",
        "identity-services",
        "search-platform",
        "notification-hub",
        "revenue-systems",
        "partner-connect",
        "mobile-services",
        "shared-infrastructure",
    ]
    from portal.securityhub import standard_id

    standard_arn = (
        "arn:aws:securityhub:ap-southeast-1::standards/aws-foundational-security-best-practices/v/1.0.0"
    )
    standard = {
        "id": standard_id(standard_arn),
        "name": "AWS Foundational Security Best Practices",
        "edition": "1.0.0",
        "source": "securityhub",
        "standards_arn": standard_arn,
        "synthetic": True,
    }
    data["standards"] = {standard["id"]: standard}
    for i in range(count + 1):
        env = ("production", "testing", "dev")[i % 3] if i < count else "unclassified"
        aid = str(100000000001 + i)
        account = {
            "id": aid,
            "name": names[(i // 3) % len(names)] + "-" + env,
            "environment": env,
            "owner_team": teams[(i // 3) % 4],
            "ou_path": f"Root/Workloads/{env.title()}/{teams[(i // 3) % 4].title()}",
            "tags": {
                "Environment": env,
                "Owner": teams[i % 4],
                "CostCenter": "CC-210",
                "Service": "platform",
            },
            "last_scanned_at": (utcnow() - timedelta(minutes=3 + i % 10)).isoformat(),
        }
        observations = []
        for j, rule in enumerate(catalog()["rules"]):
            for k in range(3):
                failed = rng.random() < (0.06 if env == "production" else 0.11 if env == "testing" else 0.17)
                observations.append(
                    {
                        "rule_id": rule["id"],
                        "frameworks": {standard["id"]: [rule["id"]]},
                        "evidence_source": "securityhub"
                        if rule.get("source") == "securityhub"
                        else rule.get("source", "config"),
                        "resource": f"arn:aws:ec2:ap-southeast-1:{aid}:instance/resource-{j}-{k}",
                        "region": "ap-southeast-1",
                        "status": "FAIL" if failed else "PASS",
                        "detail": "Configuration does not meet the environment profile."
                        if failed
                        else "Control verified.",
                    }
                )
        observations.append(
            {
                "rule_id": "SECURITY_HUB::S3.14",
                "security_control_id": "S3.14",
                "resource": f"arn:aws:s3:::gov-portal-pilot-{aid}",
                "region": "ap-southeast-1",
                "title": "S3 buckets should have versioning enabled",
                "aws_severity": "LOW",
                "evidence_source": "securityhub",
                "frameworks": {standard["id"]: ["S3.14"]},
                "status": "FAIL" if i % 4 != 3 else "PASS",
                "detail": "Synthetic Security Hub versioning evidence.",
            }
        )
        findings, result = evaluate(account, observations)
        data["accounts"][aid] = {**account, **result, **summaries(findings), "stale": False}
        data["findings"][aid] = findings
        data["observations"][aid] = observations
        data["deployments"][aid] = [
            {
                "name": "organization-baseline",
                "version": "v2.8.1" if i % 5 else "v2.7.0",
                "status": "CURRENT" if i % 5 else "OUTDATED",
                "drift_status": "DRIFTED" if i % 7 == 0 else "IN_SYNC",
                "deployed_at": (utcnow() - timedelta(hours=8 + i * 7)).isoformat(),
                "pipeline_run_url": None,
                "region": "ap-southeast-1",
            }
        ]
    for env in ("all", "dev", "testing", "production"):
        accounts = [a for a in data["accounts"].values() if env == "all" or a["environment"] == env]
        average = sum(a["score_pct"] for a in accounts) / len(accounts)
        data["trends"][env] = [
            {
                "date": (utcnow() - timedelta(days=d)).date().isoformat(),
                "score_pct": round(max(0, average - d * 0.14 + rng.uniform(-1, 1)), 1),
            }
            for d in range(29, -1, -1)
        ]
    data["settings"] = {
        "weekly": {"enabled": False, "channel": "email", "destination": "", "updated_at": timestamp()}
    }
    return data
