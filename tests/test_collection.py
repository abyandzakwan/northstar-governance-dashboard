from datetime import datetime, timezone
import io
import json
import boto3
from botocore.stub import Stubber
from portal.aws import pages
from portal.discovery import discover


def test_organizations_pagination_tags_and_parent_paths():
    org = boto3.client(
        "organizations", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    with Stubber(org) as stub:
        stub.add_response("list_accounts", {"Accounts": [], "NextToken": "next"}, {})
        stub.add_response(
            "list_accounts",
            {"Accounts": [{"Id": "123456789012", "Name": "payments", "Status": "ACTIVE"}]},
            {"NextToken": "next"},
        )
        stub.add_response(
            "list_parents",
            {"Parents": [{"Id": "ou-abcd-production", "Type": "ORGANIZATIONAL_UNIT"}]},
            {"ChildId": "123456789012"},
        )
        stub.add_response(
            "list_parents", {"Parents": [{"Id": "r-abcd", "Type": "ROOT"}]}, {"ChildId": "ou-abcd-production"}
        )
        stub.add_response(
            "describe_organizational_unit",
            {"OrganizationalUnit": {"Id": "ou-abcd-production", "Name": "Production"}},
            {"OrganizationalUnitId": "ou-abcd-production"},
        )
        stub.add_response(
            "list_tags_for_resource",
            {"Tags": [{"Key": "Environment", "Value": "testing"}, {"Key": "Owner", "Value": "payments"}]},
            {"ResourceId": "123456789012"},
        )
        accounts = discover(org, ["ou-abcd-production"])
        assert accounts[0]["environment"] == "testing"
        assert accounts[0]["ou_path"] == "Root/Production"
        assert accounts[0]["owner_team"] == "payments"
        stub.assert_no_pending_responses()


def test_manual_pagination_continues_through_empty_pages():
    class Service:
        def can_paginate(self, operation):
            return False

        def read(self, **kwargs):
            return {"Rows": [], "NextToken": "next"} if not kwargs else {"Rows": ["evidence"]}

    assert list(pages(Service(), "read", "Rows")) == ["evidence"]


def test_backup_assignment_honors_exclusions_and_and_conditions():
    from portal.collector import backup_selection_matches

    arn = "arn:aws:rds:ap-southeast-1:123456789012:db:payments"
    selection = {
        "Resources": ["arn:aws:rds:*"],
        "Conditions": {
            "StringEquals": [{"ConditionKey": "aws:ResourceTag/backup", "ConditionValue": "true"}],
            "StringNotLike": [{"ConditionKey": "aws:ResourceTag/stage", "ConditionValue": "dev*"}],
        },
    }
    assert backup_selection_matches(selection, arn, {"backup": "true", "stage": "production"})
    assert not backup_selection_matches(selection, arn, {"backup": "false", "stage": "production"})
    assert not backup_selection_matches(selection, arn, {"backup": "true", "stage": "development"})
    assert not backup_selection_matches(
        {**selection, "NotResources": [arn]}, arn, {"backup": "true", "stage": "production"}
    )


def test_snapshot_is_real_parquet_and_has_stable_daily_account_path(monkeypatch):
    from portal import history
    import pyarrow.parquet as pq

    captured = []

    class S3:
        def put_object(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setenv("HISTORY_BUCKET", "private-evidence")
    monkeypatch.setattr(history, "client", lambda service: S3())
    monkeypatch.setattr(history, "utcnow", lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
    history.snapshot(
        {"id": "123456789012", "environment": "production", "owner_team": "payments", "score_pct": 95},
        [
            {
                "rule_id": "S3_PUBLIC_ACCESS_BLOCKED",
                "resource": "arn:aws:s3:::sample",
                "status": "ACCEPTED_RISK",
                "severity": "critical",
                "evaluated_at": "2026-11-01T00:00:00+00:00",
                "waiver_id": "w1",
                "frameworks": {"cis-aws": ["2.1.4"]},
            }
        ],
    )
    assert captured[0]["Key"] == "snapshots/dt=2026-11-01/environment=production/123456789012.parquet"
    rows = pq.read_table(io.BytesIO(captured[0]["Body"])).to_pylist()
    assert rows[0]["account_score"] == 95
    assert rows[0]["waiver_id"] == "w1"
    assert json.loads(rows[0]["frameworks"])["cis-aws"] == ["2.1.4"]
    assert captured[1]["Key"].startswith("daily-scores/")
    assert pq.read_table(io.BytesIO(captured[1]["Body"])).to_pylist()[0]["account_score"] == 95
