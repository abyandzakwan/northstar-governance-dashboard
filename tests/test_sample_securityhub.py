import pytest
from botocore.exceptions import ClientError
from portal import collector
from portal.change_policy import validate_target
from portal.domain import evaluate


def unavailable(account_id):
    raise ClientError({"Error": {"Code": "InvalidAccessException", "Message": "Not enabled"}}, "GetFindings")


def configure(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("SECURITYHUB_SAMPLE_FALLBACK", "true")
    monkeypatch.setenv("REMEDIATION_ENABLED", "false")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")


def test_unavailable_hub_produces_labelled_samples_that_cannot_be_remediated(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(collector, "securityhub_evidence", unavailable)
    evidence = collector.collect_securityhub("519012038215")
    account = {"id": "519012038215", "environment": "testing"}
    findings, _ = evaluate(account, evidence)
    samples = [f for f in findings if f.get("sample_data")]
    assert samples and {"PASS", "FAIL"} <= {f["status"] for f in samples}
    assert all(f["source"] == "securityhub-sample" and "SAMPLE ONLY" in f["detail"] for f in samples)
    for finding in samples:
        with pytest.raises(ValueError, match="Sample findings"):
            validate_target(account, finding)


def test_real_findings_take_precedence_and_disabled_fallback_preserves_error(monkeypatch):
    configure(monkeypatch)
    real = [{"rule_id": "ROOT_MFA_ENABLED", "status": "FAIL", "resource": "real"}]
    monkeypatch.setattr(collector, "securityhub_evidence", lambda account: real)
    assert collector.collect_securityhub("519012038215") == real
    monkeypatch.setenv("SECURITYHUB_SAMPLE_FALLBACK", "false")
    monkeypatch.setattr(collector, "securityhub_evidence", unavailable)
    with pytest.raises(ClientError):
        collector.collect_securityhub("519012038215")


@pytest.mark.parametrize("key,value", [("DEPLOYMENT_MODE", "organization"), ("REMEDIATION_ENABLED", "true")])
def test_sample_fallback_rejects_organization_mode_and_enabled_remediation(monkeypatch, key, value):
    configure(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="standalone mode"):
        collector.collect_securityhub("519012038215")
