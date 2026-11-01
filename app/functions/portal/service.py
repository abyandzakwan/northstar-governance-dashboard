"""HTTP-independent application routes. Every account read is scoped before aggregation."""

from portal.change_policy import approval_environment

from datetime import date, timedelta
from uuid import uuid4
import csv
import io
import json
import os
from portal.auth import AccessDenied
from portal.domain import (
    ENVIRONMENTS,
    catalog,
    evaluate,
    framework_from_summaries,
    parse_time,
    summaries,
    timestamp,
    utcnow,
)


class NotFound(Exception):
    pass


def audit(action, principal, **details):
    print(
        json.dumps(
            {"event": "audit", "action": action, "subject": principal.subject, "at": timestamp(), **details}
        )
    )


def scoped_accounts(store, principal):
    accounts = [a for a in store.all("accounts") if principal.can_read(a) and not a.get("closed_at")]
    for account in accounts:
        for key, value in {
            "healthy": False,
            "score_pct": 0,
            "critical_count": 0,
            "fail_count": 0,
            "accepted_risk_count": 0,
        }.items():
            account.setdefault(key, value)
        scanned = account.get("last_scanned_at")
        account["stale"] = not scanned or utcnow() - parse_time(scanned) > timedelta(hours=26)
    return accounts


def account_for(store, principal, account_id):
    account = store.get("accounts", account_id)
    if not account or not principal.can_read(account):
        raise NotFound("Account not found")
    return account


def refresh_evaluation(store, account_id):
    account = store.get("accounts", account_id)
    observations = store.get("observations", account_id)
    if observations is None:
        observations = [
            {**r, "status": r.get("observed_status", "UNKNOWN")} for r in store.findings(account_id)
        ]
    results, result = evaluate(account, observations, store.all("waivers"))
    store.put("findings", account_id, results)
    store.put("scores", account_id, result)
    store.put("accounts", account_id, {**account, **result, **summaries(results)})


def export_bytes(store, account_ids, format_):
    rows = []
    allowed = set(account_ids)
    waivers = sorted(
        [w for w in store.all("waivers") if w["account_id"] in allowed],
        key=lambda w: w.get("created_at", ""),
        reverse=True,
    )
    by_id = {w["id"]: w for w in waivers}
    by_account = {}
    for waiver in waivers:
        by_account.setdefault(waiver["account_id"], []).append(waiver)
    for aid in account_ids:
        account = store.get("accounts", aid)
        for finding in store.findings(aid):
            waiver = by_id.get(finding.get("waiver_id")) or next(
                (
                    w
                    for w in by_account.get(aid, [])
                    if w["rule_id"] == finding["rule_id"]
                    and (not w.get("resource") or w["resource"] == finding["resource"])
                ),
                {},
            )
            rows.append(
                {
                    "classification": "Confidential",
                    "account_id": aid,
                    "account_name": account["name"],
                    "environment": account["environment"],
                    "owner_team": account["owner_team"],
                    "waiver_owner": waiver.get("owner", ""),
                    "waiver_reason": waiver.get("reason", ""),
                    "waiver_expires_at": waiver.get("expires_at", ""),
                    "waiver_revoked_at": waiver.get("revoked_at", ""),
                    **finding,
                    "waiver_id": finding.get("waiver_id") or waiver.get("id", ""),
                }
            )
    if format_ == "json":
        return json.dumps(
            {
                "classification": "Confidential",
                "generated_at": timestamp(),
                "scope": "Technical evidence only; not a certification of compliance.",
                "waivers": waivers,
                "results": rows,
            }
        ).encode()
    output = io.StringIO()
    fields = [
        "classification",
        "account_id",
        "account_name",
        "environment",
        "owner_team",
        "rule_id",
        "resource",
        "status",
        "severity",
        "source",
        "evaluated_at",
        "waiver_id",
        "waiver_owner",
        "waiver_reason",
        "waiver_expires_at",
        "waiver_revoked_at",
        "frameworks",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
        # Prevent spreadsheet formula injection from account tags, resource names and waiver evidence.
        safe = {
            k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v
            for k, v in safe.items()
        }
        writer.writerow(safe)
    return output.getvalue().encode()


def route(store, principal, method, path, query=None, body=None):
    query, body = query or {}, body or {}
    parts = path.strip("/").split("/")
    if parts[0] == "changes":
        from portal.changes import route as change_route

        return change_route(store, principal, method, path, body)
    accounts = scoped_accounts(store, principal)
    env = query.get("env", "all")
    if env not in (*ENVIRONMENTS, "all"):
        raise ValueError("Invalid environment")
    selected = [a for a in accounts if env == "all" or a["environment"] == env]
    demo = os.environ.get("PORTAL_MODE") == "demo"
    if method == "GET" and path == "/session":
        return 200, {
            "subject": principal.subject,
            "admin": principal.admin,
            "teams": principal.teams,
            "mode": "demo" if demo else "aws",
            "sample_securityhub": os.environ.get("SECURITYHUB_SAMPLE_FALLBACK") == "true",
            "deployment_mode": os.environ.get("DEPLOYMENT_MODE", "organization"),
            "auth_mode": os.environ.get("AUTH_MODE", "saml"),
            "classification": "Confidential",
        }
    if method == "GET" and path == "/environments":
        summaries = []
        for environment in ENVIRONMENTS:
            subset = [a for a in accounts if a["environment"] == environment]
            summaries.append(
                {
                    "environment": environment,
                    "accounts_total": len(subset),
                    "healthy": sum(a.get("healthy", False) for a in subset),
                    "critical_count": sum(a.get("critical_count", 0) for a in subset),
                    "stale_count": sum(a["stale"] for a in subset),
                    "avg_score": round(sum(a.get("score_pct", 0) for a in subset) / len(subset), 1)
                    if subset
                    else None,
                }
            )
        return 200, {
            "items": summaries,
            "updated_at": max((a.get("last_scanned_at", "") for a in accounts), default=None),
        }
    if method == "GET" and (
        (len(parts) == 3 and parts[0] == "environments" and parts[2] == "accounts") or path == "/accounts"
    ):
        requested = parts[1] if len(parts) == 3 else env
        if requested not in (*ENVIRONMENTS, "all"):
            raise ValueError("Invalid environment")
        items = [a for a in accounts if requested == "all" or a["environment"] == requested]
        for key in ("team", "ou", "status", "search"):
            value = query.get(key, "")
            if value:
                items = [
                    a
                    for a in items
                    if (
                        a["owner_team"] == value
                        if key == "team"
                        else value.lower() in a["ou_path"].lower()
                        if key == "ou"
                        else (a["healthy"] if value == "healthy" else not a["healthy"])
                        if key == "status"
                        else value.lower() in (a["name"] + a["id"]).lower()
                    )
                ]
        return 200, {
            "items": [
                {k: v for k, v in a.items() if k not in {"deployment_summary", "framework_summary"}}
                for a in sorted(items, key=lambda a: a.get("score_pct", 0))
            ]
        }
    if parts[0] == "accounts" and len(parts) in (2, 3) and method == "GET":
        account = account_for(store, principal, parts[1])
        if len(parts) == 2:
            return 200, {**account, "profile": catalog()["profiles"][account["environment"]]}
        if parts[2] == "deployments":
            return 200, {"items": store.deployments(parts[1])}
        if parts[2] == "findings":
            findings = store.findings(parts[1])
            for field in ("severity", "status", "rule_id"):
                value = query.get("rule" if field == "rule_id" else field)
                if value:
                    findings = [f for f in findings if f[field] == value]
            if query.get("framework"):
                findings = [f for f in findings if query["framework"] in f["frameworks"]]
            return 200, {"items": findings}
    if method == "GET" and path == "/rules":
        rules = []
        for rule in catalog()["rules"]:
            counters = [a.get("rule_summary", {}).get(rule["id"], {}) for a in selected]
            rules.append(
                {
                    **rule,
                    "failing_accounts": sum(c.get("issues", 0) > 0 for c in counters),
                    "passing": sum(c.get("passing", 0) for c in counters),
                    "total": sum(c.get("total", 0) for c in counters),
                }
            )
        return 200, {"version": catalog()["version"], "items": rules}
    if method == "GET" and len(parts) == 3 and parts[0] == "rules" and parts[2] == "accounts":
        if parts[1] not in {r["id"] for r in catalog()["rules"]}:
            raise NotFound("Rule not found")
        return 200, {
            "items": [a for a in selected if a.get("rule_summary", {}).get(parts[1], {}).get("issues", 0) > 0]
        }
    if method == "GET" and path == "/frameworks":
        return 200, {
            "items": store.all("standards")
            + [{"id": key, "source": "internal", **value} for key, value in catalog()["frameworks"].items()]
        }
    if method == "GET" and len(parts) == 2 and parts[0] == "frameworks":
        definition = store.get("standards", parts[1]) or catalog()["frameworks"].get(parts[1])
        if not definition:
            raise NotFound("Framework not found")
        scope = (
            [a for a in selected if parts[1] in a.get("framework_summary", {})]
            if definition.get("source") == "securityhub"
            else selected
        )
        return 200, framework_from_summaries(parts[1], scope, definition)
    if method == "GET" and path == "/deployments":
        deployments = [
            {
                **d,
                "account_id": a["id"],
                "account_name": a["name"],
                "environment": a["environment"],
                "ou_path": a["ou_path"],
            }
            for a in selected
            for d in a.get("deployment_summary", store.deployments(a["id"]) if demo else [])
        ]
        cursor = int(query.get("cursor", 0))
        deployments.sort(key=lambda d: (d["account_id"], d["name"], d.get("region", "")))
        limit = min(int(query.get("limit", 100)), 200)
        if cursor < 0 or limit < 1:
            raise ValueError("Invalid pagination")
        return 200, {
            "items": deployments[cursor : cursor + limit],
            "total": len(deployments),
            "drifted": sum(
                a.get(
                    "deployment_drift_count",
                    sum(d["drift_status"] == "DRIFTED" for d in store.deployments(a["id"])) if demo else 0,
                )
                for a in selected
            ),
            "next_cursor": str(cursor + limit) if cursor + limit < len(deployments) else None,
        }
    if method == "GET" and path == "/trends":
        start = date.fromisoformat(query.get("from", (utcnow() - timedelta(days=29)).date().isoformat()))
        end = date.fromisoformat(query.get("to", utcnow().date().isoformat()))
        if end < start or (end - start).days > 400:
            raise ValueError("Trend range must be between 0 and 400 days")
        if demo:
            # Demo principals share synthetic history only when they have org-wide access.
            series = (
                store.get("trends", env)
                if principal.admin or "gov-portal-viewers" in principal.groups
                else []
            )
            return 200, {
                "items": [r for r in series or [] if start.isoformat() <= r["date"] <= end.isoformat()]
            }
        from portal.history import trends

        return trends(store, principal, selected, env, start, end)
    if path == "/waivers" and method == "GET":
        allowed = {a["id"] for a in accounts}
        return 200, {"items": [w for w in store.all("waivers") if w["account_id"] in allowed]}
    if path == "/waivers" and method == "POST":
        account = account_for(store, principal, body.get("account_id", ""))
        if not principal.can_waive(account):
            raise AccessDenied(
                "Production waivers require an administrator; team leads may waive their own other accounts"
            )
        if body.get("rule_id") not in {r["id"] for r in catalog()["rules"]} | {
            f["rule_id"] for f in store.findings(account["id"])
        }:
            raise ValueError("A valid rule is required")
        expires = parse_time(body.get("expires_at", ""))
        if expires <= utcnow() or (
            approval_environment(account) == "production" and expires > utcnow() + timedelta(days=90)
        ):
            raise ValueError("Expiry must be in the future and within 90 days for Production")
        if not body.get("owner", "").strip() or len(body.get("reason", "").strip()) < 10:
            raise ValueError("Owner and a reason of at least 10 characters are required")
        waiver = {k: body.get(k, "") for k in ("account_id", "rule_id", "resource", "owner", "reason")}
        if any(len(str(v)) > 2048 for v in waiver.values()):
            raise ValueError("Waiver fields must be shorter than 2048 characters")
        waiver.update(
            id=str(uuid4()),
            expires_at=expires.isoformat(),
            created_at=timestamp(),
            approved_by=principal.subject,
        )

        if approval_environment(account) == "production":
            from portal.changes import create_exception

            return 202, create_exception(store, principal, waiver)
        store.put("waivers", waiver["id"], waiver)
        refresh_evaluation(store, account["id"])
        audit("waiver.created", principal, waiver_id=waiver["id"], account_id=account["id"])
        return 201, waiver
    if len(parts) == 2 and parts[0] == "waivers" and method == "DELETE":
        waiver = store.get("waivers", parts[1])
        if not waiver:
            raise NotFound("Waiver not found")
        account = account_for(store, principal, waiver["account_id"])
        if not principal.can_waive(account):
            raise AccessDenied("Not allowed to revoke this waiver")

        if approval_environment(account) == "production":
            from portal.changes import create_exception

            return 202, create_exception(store, principal, waiver, revoke=True)
        waiver["revoked_at"] = timestamp()
        store.put("waivers", waiver["id"], waiver)
        refresh_evaluation(store, account["id"])
        audit("waiver.revoked", principal, waiver_id=waiver["id"])
        return 200, waiver
    if path == "/scans" and method == "POST":
        if not principal.admin:
            raise AccessDenied("Only administrators can trigger scans")
        if bool(body.get("account_id")) == bool(body.get("environment")):
            raise ValueError("Specify exactly one account_id or environment")
        target = (
            {"account_id": account_for(store, principal, body["account_id"])["id"]}
            if body.get("account_id")
            else {"environment": body["environment"]}
        )
        if target.get("environment") and target["environment"] not in ENVIRONMENTS:
            raise ValueError("Invalid environment")
        scan_id = str(uuid4())
        if demo:
            for account in accounts:
                if account["id"] == target.get("account_id") or account["environment"] == target.get(
                    "environment"
                ):
                    refresh_evaluation(store, account["id"])
                    refreshed = store.get("accounts", account["id"])
                    store.put("accounts", account["id"], {**refreshed, "last_scanned_at": timestamp()})
        else:
            from portal.aws import client

            client("stepfunctions").start_execution(
                stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                name=scan_id,
                input=json.dumps({**target, "scan_id": scan_id}),
            )
        audit("scan.requested", principal, scan_id=scan_id, **target)
        return 202, {"scan_id": scan_id, "status": "complete" if demo else "queued"}
    if path == "/reports" and method == "POST":
        format_ = body.get("format", "csv")
        report_env = body.get("environment", "all")
        if format_ not in {"csv", "json"} or report_env not in (*ENVIRONMENTS, "all"):
            raise ValueError("Invalid report format or environment")
        report = {
            "id": str(uuid4()),
            "format": format_,
            "environment": report_env,
            "owner": principal.subject,
            "status": "pending",
            "created_at": timestamp(),
            "classification": "Confidential",
            "account_ids": [
                a["id"] for a in accounts if report_env == "all" or a["environment"] == report_env
            ],
        }
        store.put("reports", report["id"], report)
        if demo:
            report["status"] = "ready"
            store.put("reports", report["id"], report)
        else:
            from portal.aws import client

            client("lambda").invoke(
                FunctionName=os.environ["REPORTS_FUNCTION"],
                InvocationType="Event",
                Payload=json.dumps({"report_id": report["id"]}).encode(),
            )
        audit("report.requested", principal, report_id=report["id"])
        return 202, report
    if method == "GET" and parts[0] == "reports" and len(parts) in (2, 3):
        report = store.get("reports", parts[1])
        if not report or report["owner"] != principal.subject:
            raise NotFound("Report not found")
        # Recheck access at download time, in case account ownership changed since export creation.
        allowed = {a["id"] for a in accounts}
        if not set(report["account_ids"]) <= allowed:
            raise AccessDenied("Report scope no longer matches your account access")
        if report["status"] == "ready":
            if demo:
                report["download_url"] = f"/api/reports/{report['id']}/download"
            else:
                from portal.aws import client

                report["download_url"] = client("s3").generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": os.environ["EXPORT_BUCKET"],
                        "Key": f"exports/{report['id']}.{report['format']}",
                        "ResponseContentDisposition": f'attachment; filename="governance-{report["id"]}.{report["format"]}"',
                    },
                    ExpiresIn=900,
                )
        return 200, report
    if path == "/settings/weekly" and method == "GET":
        if not principal.admin:
            raise AccessDenied("Only administrators can read report settings")
        return 200, store.get("settings", "weekly") or {"enabled": False, "channel": "email"}
    if path == "/settings/weekly" and method == "PUT":
        if not principal.admin:
            raise AccessDenied("Only administrators can edit report settings")
        if not isinstance(body.get("enabled"), bool) or body.get("channel") not in {"email", "slack"}:
            raise ValueError("Specify enabled and an email or slack channel")
        settings = {
            "enabled": body["enabled"],
            "channel": body["channel"],
            "updated_at": timestamp(),
            "updated_by": principal.subject,
        }
        store.put("settings", "weekly", settings)
        audit("settings.weekly.updated", principal)
        return 200, settings
    raise NotFound("Endpoint not found")
