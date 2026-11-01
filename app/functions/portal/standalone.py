"""Same-account collection adapters for a standalone test deployment."""

import json
import os
from portal.aws import client, pages


def regions():
    return json.loads(os.environ.get("COLLECTION_REGIONS", "[]")) or [os.environ["AWS_REGION"]]


def config_evidence():
    observations = []
    mapping = json.loads(os.environ.get("CONFIG_RULE_MAPPING", "{}"))
    for region in regions():
        config = client("config", region_name=region)
        for name, rule_id in mapping.items():
            for row in pages(
                config,
                "get_compliance_details_by_config_rule",
                "EvaluationResults",
                ConfigRuleName=name,
                ComplianceTypes=["COMPLIANT", "NON_COMPLIANT"],
            ):
                qualifier = row["EvaluationResultIdentifier"]["EvaluationResultQualifier"]
                observations.append(
                    {
                        "rule_id": rule_id,
                        "resource": qualifier["ResourceId"],
                        "region": region,
                        "status": {"COMPLIANT": "PASS", "NON_COMPLIANT": "FAIL"}.get(
                            row["ComplianceType"], "UNKNOWN"
                        ),
                        "detail": row.get("Annotation", "AWS Config evaluation"),
                        "evidence_source": "config",
                    }
                )
    return observations


def inventory():
    rows = []
    for region in regions():
        rows.extend(
            json.loads(row)
            for row in pages(
                client("config", region_name=region),
                "select_resource_config",
                "Results",
                Expression="SELECT arn, awsRegion, resourceType, tags WHERE resourceType IN ('AWS::EC2::Instance','AWS::EC2::Volume','AWS::RDS::DBInstance','AWS::DynamoDB::Table','AWS::EFS::FileSystem')",
            )
        )
    return rows
