import json
from datetime import datetime, timezone
import boto3
import pytest
from botocore.stub import Stubber
from portal import auth, collector, discovery, standalone
from portal.store import LocalStore


def test_standalone_discovery_never_calls_organizations(monkeypatch):
    store = LocalStore()
    written = {}

    class STS:
        def get_caller_identity(self):
            return {"Account": "111111111111"}

    class S3:
        def put_object(self, **kwargs):
            written.update(kwargs)

    def sdk(service):
        assert service in {"sts", "s3"}, "Standalone discovery must not use Organizations"
        return STS() if service == "sts" else S3()

    monkeypatch.setenv("DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("STANDALONE_ACCOUNT_ID", "111111111111")
    monkeypatch.setenv("HISTORY_BUCKET", "test-history")
    monkeypatch.setattr(discovery, "get_store", lambda: store)
    monkeypatch.setattr(discovery, "client", sdk)
    result = discovery.handler({}, None)
    assert result["accounts_total"] == 1
    account = json.loads(written["Body"])[0]["account"]
    assert account["id"] == "111111111111" and account["environment"] == "dev"
    assert account["discovery_source"] == "standalone" and account["tags"] == {}
    monkeypatch.setenv("STANDALONE_ACCOUNT_ID", "222222222222")
    with pytest.raises(ValueError, match="configured test account"):
        discovery.handler({}, None)


def test_native_login_only_uses_administrator_assigned_groups(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "cognito")
    event = {
        "request": {
            "userAttributes": {"custom:groups": "gov-portal-admins"},
            "groupConfiguration": {"groupsToOverride": ["gov-portal-viewers", "unrelated"]},
        }
    }
    response = auth.token_handler(event, None)
    assert response["response"]["claimsOverrideDetails"]["groupOverrideDetails"]["groupsToOverride"] == [
        "gov-portal-viewers"
    ]


def test_standalone_collector_uses_own_credentials_and_rejects_other_accounts(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("STANDALONE_ACCOUNT_ID", "111111111111")
    session = object()
    monkeypatch.setattr(boto3, "Session", lambda: session)
    assert collector.member_session("111111111111") is session
    with pytest.raises(ValueError, match="different account"):
        collector.member_session("222222222222")
    with pytest.raises(ValueError, match="configured test account"):
        collector.handler({"account": {"id": "222222222222"}}, None)


def test_local_config_reads_resource_evidence_without_an_aggregator(monkeypatch):
    config = boto3.client(
        "config", region_name="ap-southeast-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("COLLECTION_REGIONS", '["ap-southeast-1"]')
    monkeypatch.setenv("CONFIG_RULE_MAPPING", '{"s3-public-access": "S3_PUBLIC_ACCESS_BLOCKED"}')
    monkeypatch.setattr(standalone, "client", lambda service, **kwargs: config)
    with Stubber(config) as stub:
        stub.add_response(
            "get_compliance_details_by_config_rule",
            {
                "EvaluationResults": [
                    {
                        "EvaluationResultIdentifier": {
                            "EvaluationResultQualifier": {
                                "ConfigRuleName": "s3-public-access",
                                "ResourceType": "AWS::S3::Bucket",
                                "ResourceId": "sample",
                            },
                            "OrderingTimestamp": now,
                        },
                        "ComplianceType": "NON_COMPLIANT",
                        "ResultRecordedTime": now,
                        "ConfigRuleInvokedTime": now,
                    }
                ]
            },
            {"ConfigRuleName": "s3-public-access", "ComplianceTypes": ["COMPLIANT", "NON_COMPLIANT"]},
        )
        evidence = standalone.config_evidence()
        assert evidence[0]["status"] == "FAIL" and evidence[0]["resource"] == "sample"
        stub.assert_no_pending_responses()
