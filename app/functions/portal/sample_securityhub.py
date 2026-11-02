"""Synthetic ASFF fixtures for an explicitly configured standalone test portal.

These records are never imported into AWS Security Hub or used for remediation.
"""

from portal.domain import catalog, timestamp


def sample_findings(account_id, region):
    now = timestamp()
    rows = []
    for index, rule in enumerate(r for r in catalog()["rules"] if r["source"] == "securityhub"):
        status = "FAILED" if index % 3 == 1 else "PASSED"
        rows.append(
            {
                "SchemaVersion": "2018-10-08",
                "Id": f"northstar-sample/{account_id}/{rule['check']}",
                "ProductArn": f"arn:aws:securityhub:{region}:{account_id}:product/{account_id}/default",
                "GeneratorId": "northstar-sample-only",
                "AwsAccountId": account_id,
                "Region": region,
                "Types": ["Software and Configuration Checks/Industry and Regulatory Standards"],
                "CreatedAt": now,
                "UpdatedAt": now,
                "Severity": {"Label": "HIGH" if status == "FAILED" else "INFORMATIONAL"},
                "Title": rule["title"],
                "Description": "SAMPLE ONLY: Synthetic Security Hub finding for dashboard preview. "
                "This is not evidence of the real account's compliance and cannot be remediated.",
                "Resources": [
                    {
                        "Type": "Other",
                        "Id": f"northstar-sample-only://{account_id}/{rule['check']}",
                        "Region": region,
                    }
                ],
                "Compliance": {"Status": status, "SecurityControlId": rule["check"]},
                "ProductFields": {"northstar/sample": "true"},
                "RecordState": "ACTIVE",
            }
        )
    return rows
