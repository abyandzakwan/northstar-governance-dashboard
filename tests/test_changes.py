from copy import deepcopy
from datetime import timedelta
import json
import time
import pytest
from portal import changes, remediation_actions as actions
from portal.auth import AccessDenied, Principal
from portal.change_policy import approval_environment, digest, manifest, safe_repo_path, set_variable
from portal.demo import seed
from portal.domain import utcnow
from portal.remediation_github import GitHub
from portal.service import route
from portal.store import LocalStore

ADMIN = Principal("admin", ("gov-portal-admins",))
TEAM = Principal("team", ("gov-portal-team-booking",))
VIEWER = Principal("viewer", ("gov-portal-viewers",))


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("PORTAL_MODE", "demo")
    return LocalStore(seed=seed())


def target(store, environment="dev", team=None):
    for account in store.all("accounts"):
        if account["environment"] != environment or (team and account["owner_team"] != team):
            continue
        for finding in store.findings(account["id"]):
            if finding["status"] in {"FAIL", "WARN"} and finding["rule_id"] == "S3_PUBLIC_ACCESS_BLOCKED":
                return {
                    "account_id": account["id"],
                    **{k: finding[k] for k in ["rule_id", "resource", "region"]},
                }
    raise AssertionError("Expected deterministic actionable finding")


def create(store, *targets, **options):
    return changes.create(store, ADMIN, {"targets": list(targets), **options})["items"]


def test_mixed_account_request_is_split_by_environment_and_never_auto_executes(store):
    jobs = create(store, target(store, "dev"), target(store, "testing"), target(store, "production"))
    assert len(jobs) == 3
    assert len({j["batch_id"] for j in jobs}) == 1
    assert all(j["status"] == "PLANNED" and not j["outcomes"] for j in jobs)
    prod = next(j for j in jobs if j["environment"] == "production")
    changes.submit(store, prod["id"])
    with pytest.raises(ValueError, match="not authorized"):
        changes.execute_target(store, prod["id"], prod["targets"][0]["id"])
    assert store.get("changes", prod["id"])["status"] == "AWAITING_APPROVAL"
    route(store, ADMIN, "POST", f"/changes/{prod['id']}/demo-approve", {})
    assert store.get("changes", prod["id"])["status"] == "SUCCEEDED"
    for job in jobs:
        if job["id"] != prod["id"]:
            assert store.get("changes", job["id"])["status"] == "PLANNED"


def test_nonproduction_failure_retry_preserves_history_and_updates_findings(store):
    pick = target(store)
    job = create(store, pick, simulate_failure=True)[0]
    route(store, ADMIN, "POST", f"/changes/{job['id']}/execute", {})
    failed = store.get("changes", job["id"])
    assert failed["status"] == "FAILED"
    assert failed["outcomes"][job["targets"][0]["id"]]["attempts"] == 1
    route(store, ADMIN, "POST", f"/changes/{job['id']}/retry", {})
    passed = store.get("changes", job["id"])
    assert passed["status"] == "SUCCEEDED"
    assert passed["outcomes"][job["targets"][0]["id"]]["attempts"] == 2
    assert any("Simulated deployment failure" in e["message"] for e in passed["events"])
    assert (
        next(
            f
            for f in store.findings(pick["account_id"])
            if f["rule_id"] == pick["rule_id"] and f["resource"] == pick["resource"]
        )["status"]
        == "PASS"
    )


def test_scope_and_current_evidence_are_server_authoritative(store):
    pick = target(store, "production")
    with pytest.raises(AccessDenied):
        changes.create(store, VIEWER, {"targets": [pick]})
    with pytest.raises(AccessDenied):
        changes.create(store, TEAM, {"targets": [target(store, "dev", "flight")]})
    job = create(store, {**pick, "environment": "dev", "desired": {"allow_all": True}})[0]
    assert job["requires_approval"]
    assert "allow_all" not in job["targets"][0]["desired"]
    assert changes.list_changes(store, TEAM) == []
    account = store.get("accounts", pick["account_id"])
    account["environment"] = "dev"
    assert approval_environment(account) == "production"


def test_expired_tampered_and_unsupported_plans_fail_closed(store):
    job = create(store, target(store))[0]
    altered = deepcopy(job)
    altered["targets"][0]["desired"]["BlockPublicAcls"] = False
    with pytest.raises(ValueError, match="contents changed"):
        changes.validate_plan(altered)
    altered = deepcopy(job)
    altered["expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="expired"):
        changes.validate_plan(altered)
    unsupported = next(
        (a, f)
        for a in store.all("accounts")
        for f in store.findings(a["id"])
        if f["status"] == "FAIL" and f["rule_id"] == "ROOT_MFA_ENABLED"
    )
    result = create(store, {"account_id": unsupported[0]["id"], **unsupported[1]})[0]
    assert result["status"] == "MANUAL_ACTION_REQUIRED"


def test_stale_scope_and_active_exception_block_execution(store):
    pick = target(store)
    job = create(store, pick)[0]
    changes.submit(store, job["id"])
    account = store.get("accounts", pick["account_id"])
    account["ou_path"] = "Root/Production"
    store.put("accounts", account["id"], account)
    result = changes.execute_target(store, job["id"], job["targets"][0]["id"])
    assert result["status"] == "STALE_PLAN"
    account["ou_path"] = "Root/Dev"
    store.put("accounts", account["id"], account)
    store.put("waivers", "test", {**pick, "expires_at": (utcnow() + timedelta(days=1)).isoformat()})
    result = changes.execute_target(store, job["id"], job["targets"][0]["id"])
    assert result["status"] == "EXCLUDED"


def test_live_drift_does_not_apply_and_success_is_independently_verified(store, monkeypatch):
    job = create(store, target(store))[0]
    changes.submit(store, job["id"])
    monkeypatch.delenv("PORTAL_MODE")
    monkeypatch.setattr(actions, "current_scope", lambda _: "dev")
    monkeypatch.setattr(actions, "inspect", lambda _: ({"changed": True}, False))
    applied = []
    monkeypatch.setattr(actions, "apply", lambda _: applied.append(True))
    result = changes.execute_target(store, job["id"], job["targets"][0]["id"])
    assert result["status"] == "STALE_PLAN" and not applied
    states = iter([(job["targets"][0]["before"], False), (job["targets"][0]["desired"], False)])
    monkeypatch.setattr(actions, "inspect", lambda _: next(states))
    assert changes.execute_target(store, job["id"], job["targets"][0]["id"])["status"] == "SUCCEEDED"
    assert applied == [True]


def test_cas_and_resource_leases_do_not_overwrite_other_execution(store):
    assert store.compare_put("changes", "x", {"revision": 0})
    assert not store.compare_put("changes", "x", {"revision": 0})
    assert not store.compare_put("changes", "x", {"revision": 1}, 9)
    assert store.compare_put("changes", "x", {"revision": 1}, 0)
    assert store.acquire_lock("resource", "first", int(time.time()) + 600)
    assert not store.acquire_lock("resource", "second", int(time.time()) + 600)
    store.release_lock("resource", "second")
    assert not store.acquire_lock("resource", "second", int(time.time()) + 600)
    store.release_lock("resource", "first")
    assert store.acquire_lock("resource", "second", int(time.time()) + 600)


def test_monorepo_patches_are_typed_and_path_scoped():
    document = {"versioning": False}
    set_variable(document, ["versioning"], True)
    assert document == {"versioning": True}
    for path in ["../secret", "/etc/passwd", ".github/workflows/apply.yml", "accounts/x;echo"]:
        with pytest.raises(ValueError):
            safe_repo_path(path)
    with pytest.raises(ValueError):
        set_variable(document, ["versioning"], "true")


def test_github_requires_exact_head_human_review_and_successful_plan(store, monkeypatch):
    job = create(store, target(store, "production"))[0]
    job["pull_request"] = {
        "repository": "owner/ns-infrastructure-terraform",
        "number": 1,
        "head_sha": "head",
        "path": ".governance/changes/production/request.json",
    }
    github = object.__new__(GitHub)
    github.repo, github.branch = "owner/ns-infrastructure-terraform", "main"
    pr = {
        "head": {"sha": "head", "ref": "governance/" + job["id"], "repo": {"full_name": github.repo}},
        "base": {"ref": "main"},
        "merged": True,
        "merge_commit_sha": "merge",
        "user": {"login": "portal-bot"},
    }
    reviews = [{"state": "APPROVED", "commit_id": "head", "user": {"login": "reviewer", "type": "User"}}]
    checks = [
        {"name": "Terraform remediation plan", "conclusion": "success", "app": {"slug": "github-actions"}}
    ]

    def request(method, path):
        if "/reviews?" in path:
            return reviews
        if "/check-runs?" in path:
            return {"check_runs": checks}
        if "/collaborators/" in path:
            return {"permission": "write"}
        return pr

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(
        github,
        "file",
        lambda *args: (json.dumps({**manifest(job), "plan_hash": digest(manifest(job))}), "file"),
    )
    assert github.verify(job)["reviewers"] == ["reviewer"]
    reviews[0]["commit_id"] = "old"
    with pytest.raises(ValueError, match="current human approval"):
        github.verify(job)
    reviews[0]["commit_id"] = "head"
    checks[0]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="successful remediation plan"):
        github.verify(job)
    pr["head"]["sha"] = "edited"
    with pytest.raises(ValueError, match="changed after"):
        github.verify(job)
