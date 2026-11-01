from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import pytest
from portal.change_policy import digest
from portal.domain import evaluate, summaries
from portal.securityhub import standard_id
from portal.service import route
from portal.store import LocalStore
from portal.auth import Principal

spec = importlib.util.spec_from_file_location(
    "gitops", Path(__file__).parents[1] / "ci/terraform_remediation.py"
)
gitops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gitops)


def test_only_existing_bound_resources_can_be_updated():
    targets = [{"workspace": {"resource_address": "aws_s3_bucket_versioning.pilot"}}]
    change = {"address": "aws_s3_bucket_versioning.pilot", "change": {"actions": ["update"]}}
    gitops.constrained_plan({"resource_changes": [change]}, targets)
    for action in [["create"], ["delete"], ["delete", "create"]]:
        with pytest.raises(ValueError):
            gitops.constrained_plan(
                {"resource_changes": [{**change, "change": {"actions": action}}]}, targets
            )
    with pytest.raises(ValueError):
        gitops.constrained_plan({"resource_changes": [{**change, "address": "aws_iam_role.admin"}]}, targets)


def test_patch_validation_rejects_additional_files_and_variable_edits():
    payload = {
        "request_id": "request",
        "environment": "dev",
        "driver": "terraform",
        "targets": [
            {
                "workspace": {
                    "directory": "accounts/dev/123/region",
                    "variable_file": "accounts/dev/123/region/terraform.tfvars.json",
                    "file_sha": "original",
                    "variable_path": ["versioning"],
                    "desired": True,
                }
            }
        ],
    }
    filename = payload["targets"][0]["workspace"]["variable_file"]
    manifest_path = ".governance/changes/dev/request.json"
    values = {filename: {"versioning": True}, manifest_path: {**payload, "plan_hash": digest(payload)}}
    changed = [{"filename": name, "status": "modified"} for name in values]

    class GitHub:
        def file(self, path, ref):
            if ref == "base":
                return json.dumps({"versioning": False}), "original"
            return json.dumps(values[path]), "patched"

        def request(self, method, path):
            return changed

    pr = {"number": 1, "base": {"sha": "base"}, "head": {"sha": "head"}}
    assert gitops.validate_patch(GitHub(), pr, payload)[filename] == {"versioning": True}
    changed.append({"filename": ".github/workflows/evil.yml", "status": "added"})
    with pytest.raises(ValueError, match="outside"):
        gitops.validate_patch(GitHub(), pr, payload)
    changed.pop()
    values[filename]["arbitrary"] = "change"
    with pytest.raises(ValueError, match="differ"):
        gitops.validate_patch(GitHub(), pr, payload)


def test_source_tree_hash_detects_module_changes_but_not_binary_plan_files(tmp_path):
    (tmp_path / "main.tf").write_text("reviewed source")
    original = gitops.tree_hash(tmp_path)
    (tmp_path / "build").mkdir()
    (tmp_path / "build/plan").write_text("private plan")
    assert gitops.tree_hash(tmp_path) == original
    (tmp_path / "main.tf").write_text("changed module")
    assert gitops.tree_hash(tmp_path) != original


def test_unmapped_securityhub_controls_and_actual_standards_are_retained():
    arn = "arn:aws:securityhub:ap-southeast-1::standards/aws-foundational-security-best-practices/v/1.0.0"
    framework = standard_id(arn)
    assert framework == standard_id(arn.replace("ap-southeast-1", "us-east-1"))
    account = {"id": "123456789012", "environment": "dev", "owner_team": "platform"}
    observation = {
        "rule_id": "SECURITY_HUB::S3.14",
        "resource": "arn:aws:s3:::pilot",
        "region": "ap-southeast-1",
        "title": "Bucket versioning",
        "aws_severity": "LOW",
        "status": "FAIL",
        "frameworks": {framework: ["S3.14"]},
    }
    findings, score = evaluate(account, [observation])
    finding = next(f for f in findings if f["rule_id"] == observation["rule_id"])
    assert finding["source"] == "securityhub" and finding["status"] == "FAIL"
    assert finding["frameworks"] == observation["frameworks"]
    definition = {"id": framework, "source": "securityhub", "name": "FSBP", "edition": "1.0.0"}
    data = {
        "accounts": {account["id"]: {**account, **score, **summaries(findings)}},
        "standards": {framework: definition},
    }
    _, report = route(
        LocalStore(seed=deepcopy(data)),
        Principal("viewer", ("gov-portal-viewers",)),
        "GET",
        "/frameworks/" + framework,
    )
    assert report["controls"][0]["control"] == "S3.14"
    assert report["accounts_in_scope"] == 1


def test_intervening_aws_drift_invalidates_the_saved_change_shape():
    targets = [{"workspace": {"resource_address": "aws_s3_bucket_versioning.pilot"}}]
    reviewed = [
        {
            "address": "aws_s3_bucket_versioning.pilot",
            "change": {
                "actions": ["update"],
                "before": {"status": "Suspended"},
                "after": {"status": "Enabled"},
            },
        }
    ]
    signature = digest(reviewed)
    gitops.verify_drift(signature, {"resource_changes": reviewed}, targets)
    changed = deepcopy(reviewed)
    changed[0]["change"]["before"]["status"] = "Unexpected"
    with pytest.raises(ValueError, match="drifted since review"):
        gitops.verify_drift(signature, {"resource_changes": changed}, targets)


def test_aws_provider_credentials_and_account_cannot_escape_the_target_scope():
    provider = {
        "full_name": "registry.terraform.io/hashicorp/aws",
        "expressions": {
            "profile": {"constant_value": "governance-target"},
            "allowed_account_ids": {"references": ["var.account_id"]},
        },
    }
    plan = {
        "variables": {"account_id": {"value": "123456789012"}},
        "configuration": {"provider_config": {"aws": provider}},
    }
    targets = [{"account_id": "123456789012"}]
    gitops.validate_provider_scope(plan, targets)
    with pytest.raises(ValueError, match="match the requested account"):
        gitops.validate_provider_scope(plan, [{"account_id": "999999999999"}])
    provider["expressions"]["profile"]["constant_value"] = "default"
    with pytest.raises(ValueError, match="fixed governance-target"):
        gitops.validate_provider_scope(plan, targets)
