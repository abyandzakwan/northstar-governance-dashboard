from datetime import timedelta
import json
import pytest
from portal.auth import AccessDenied, Principal, from_event, token_handler
from portal.demo import seed
from portal.domain import utcnow
from portal.service import NotFound, export_bytes, route
from portal.store import LocalStore

ADMIN = Principal("admin", ("gov-portal-admins",))
TEAM = Principal("team", ("gov-portal-team-payments",))
VIEWER = Principal("viewer", ("gov-portal-viewers",))


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("PORTAL_MODE", "demo")
    return LocalStore(seed=seed())


def test_no_unverified_headers_can_grant_access():
    with pytest.raises(AccessDenied):
        from_event({"headers": {"x-user-role": "admin", "authorization": "Bearer fake"}})
    event = {"request": {"userAttributes": {"custom:groups": "[gov-portal-admins,unrelated]"}}}
    assert token_handler(event, None)["response"]["claimsOverrideDetails"]["groupOverrideDetails"][
        "groupsToOverride"
    ] == ["gov-portal-admins"]


def test_team_filter_applies_before_dashboard_and_framework_aggregation(store):
    _, response = route(store, TEAM, "GET", "/accounts")
    assert response["items"] and all(a["owner_team"] == "payments" for a in response["items"])
    other = next(a for a in store.all("accounts") if a["owner_team"] != "payments")
    with pytest.raises(NotFound):
        route(store, TEAM, "GET", f"/accounts/{other['id']}/findings")
    _, report = route(store, TEAM, "GET", "/frameworks/cis-aws")
    assert report["accounts_in_scope"] == len(response["items"])
    allowed = {a["id"] for a in response["items"]}
    assert all(set(c["failing_accounts"]) <= allowed for c in report["controls"])


def test_prod_waiver_requires_admin_and_90_day_limit(store):
    prod = next(
        a for a in store.all("accounts") if a["environment"] == "production" and a["owner_team"] == "payments"
    )
    request = {
        "account_id": prod["id"],
        "rule_id": "CLOUDTRAIL_ENABLED",
        "owner": "owner@example.com",
        "reason": "Temporary migration approved",
        "expires_at": (utcnow() + timedelta(days=30)).isoformat(),
    }
    with pytest.raises(AccessDenied):
        route(store, TEAM, "POST", "/waivers", body=request)
    with pytest.raises(ValueError):
        route(
            store,
            ADMIN,
            "POST",
            "/waivers",
            body={**request, "expires_at": (utcnow() + timedelta(days=91)).isoformat()},
        )
    status, pending = route(store, ADMIN, "POST", "/waivers", body=request)
    assert status == 202 and pending["pending_approval"]
    assert not store.get("waivers", pending["waiver_id"])
    route(store, ADMIN, "POST", f"/changes/{pending['request_id']}/demo-approve", body={})
    waiver = store.get("waivers", pending["waiver_id"])
    assert waiver["approved_by"] == ["demo-approved-pr"]
    _, pending_revoke = route(store, ADMIN, "DELETE", "/waivers/" + waiver["id"])
    assert not store.get("waivers", waiver["id"]).get("revoked_at")
    route(store, ADMIN, "POST", f"/changes/{pending_revoke['request_id']}/demo-approve", body={})
    assert store.get("waivers", waiver["id"])["revoked_at"]


def test_only_admin_can_scan_and_request_scope_is_validated(store):
    with pytest.raises(AccessDenied):
        route(store, VIEWER, "POST", "/scans", body={"environment": "production"})
    with pytest.raises(ValueError):
        route(store, ADMIN, "POST", "/scans", body={})
    status, response = route(store, ADMIN, "POST", "/scans", body={"environment": "testing"})
    assert status == 202 and response["status"] == "complete"


def test_report_is_scoped_owned_classified_and_formula_safe(store):
    _, report = route(store, TEAM, "POST", "/reports", body={"format": "csv"})
    assert all(store.get("accounts", aid)["owner_team"] == "payments" for aid in report["account_ids"])
    with pytest.raises(NotFound):
        route(store, ADMIN, "GET", "/reports/" + report["id"])
    aid = report["account_ids"][0]
    account = store.get("accounts", aid)
    store.put("accounts", aid, {**account, "name": "=DANGEROUS()"})
    csv = export_bytes(store, [aid], "csv").decode()
    assert "Confidential" in csv and "'=DANGEROUS()" in csv
    evidence = json.loads(export_bytes(store, [aid], "json"))
    assert evidence["classification"] == "Confidential"
    store.put("accounts", aid, {**account, "owner_team": "other"})
    with pytest.raises(AccessDenied):
        route(store, TEAM, "GET", "/reports/" + report["id"])


def test_local_state_survives_reopening(tmp_path):
    path = tmp_path / "state.json"
    LocalStore(path).put("settings", "weekly", {"enabled": True})
    assert LocalStore(path).get("settings", "weekly")["enabled"]


def test_export_owner_matches_the_applied_waiver_not_a_newer_revoked_one(store):
    from portal.service import refresh_evaluation

    account = next(a for a in store.all("accounts") if a["fail_count"])
    finding = next(f for f in store.findings(account["id"]) if f["status"] == "FAIL")
    waiver = {
        "id": "active",
        "account_id": account["id"],
        "rule_id": finding["rule_id"],
        "resource": finding["resource"],
        "owner": "active-owner",
        "reason": "Accepted migration risk",
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": (utcnow() + timedelta(days=1)).isoformat(),
    }
    store.put("waivers", "active", waiver)
    store.put(
        "waivers",
        "revoked",
        {
            **waiver,
            "id": "revoked",
            "owner": "different-owner",
            "created_at": "2026-02-01T00:00:00+00:00",
            "revoked_at": utcnow().isoformat(),
        },
    )
    refresh_evaluation(store, account["id"])
    report = json.loads(export_bytes(store, [account["id"]], "json"))
    row = next(
        r
        for r in report["results"]
        if r["rule_id"] == finding["rule_id"] and r["resource"] == finding["resource"]
    )
    assert row["status"] == "ACCEPTED_RISK"
    assert row["waiver_id"] == "active" and row["waiver_owner"] == "active-owner"


def test_deployments_are_paginated_and_team_scoped(store):
    _, first = route(store, TEAM, "GET", "/deployments", query={"limit": "2"})
    assert len(first["items"]) == 2
    assert first["next_cursor"] == "2"
    _, second = route(store, TEAM, "GET", "/deployments", query={"limit": "2", "cursor": "2"})
    assert {d["account_id"] for d in first["items"]}.isdisjoint(d["account_id"] for d in second["items"])
    assert all(store.get("accounts", d["account_id"])["owner_team"] == "payments" for d in second["items"])


def test_expired_waivers_are_reevaluated_by_health_schedule(store, monkeypatch):
    from portal import health
    from portal.service import refresh_evaluation

    target = next(a for a in store.all("accounts") if a["fail_count"])
    finding = next(f for f in store.findings(target["id"]) if f["status"] == "FAIL")
    waiver = {
        "id": "expired-risk",
        "account_id": target["id"],
        "rule_id": finding["rule_id"],
        "resource": finding["resource"],
        "owner": "owner",
        "reason": "Migration accepted",
        "expires_at": (utcnow() + timedelta(days=1)).isoformat(),
    }
    store.put("waivers", waiver["id"], waiver)
    refresh_evaluation(store, target["id"])
    assert any(f["status"] == "ACCEPTED_RISK" for f in store.findings(target["id"]))
    store.put(
        "waivers", waiver["id"], {**waiver, "expires_at": (utcnow() - timedelta(seconds=1)).isoformat()}
    )
    monkeypatch.setattr(health, "get_store", lambda: store)

    class Metrics:
        def put_metric_data(self, **kwargs):
            assert kwargs["Namespace"] == "GovPortal"

    monkeypatch.setattr(health, "client", lambda _: Metrics())
    health.handler({}, None)
    assert store.get("waivers", waiver["id"])["expired_at"]
    assert not any(f["status"] == "ACCEPTED_RISK" for f in store.findings(target["id"]))
