"""AWS evidence collector. Never changes member resources or Control Tower stacks."""

from portal.securityhub import standard_id

import json
import os
from fnmatch import fnmatchcase
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from portal.aws import CONFIG, client, pages
from portal.domain import catalog, timestamp


def securityhub_evidence(account_id):
    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        from portal.standalone import regions

        return [
            finding
            for region in regions()
            for finding in securityhub_region(account_id, client("securityhub", region_name=region))
        ]
    return securityhub_region(account_id, client("securityhub"))


def securityhub_region(account_id, hub):
    rules = {r["check"]: r["id"] for r in catalog()["rules"] if r["source"] == "securityhub"}
    observations = []
    for finding in pages(
        hub,
        "get_findings",
        "Findings",
        Filters={
            "AwsAccountId": [{"Value": account_id, "Comparison": "EQUALS"}],
            "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
        },
    ):
        control = finding.get("Compliance", {}).get("SecurityControlId")
        if not control:
            control = finding.get("ProductFields", {}).get("ControlId")
        if not control:
            continue
        status = {"PASSED": "PASS", "FAILED": "FAIL", "NOT_AVAILABLE": "UNKNOWN"}.get(
            finding.get("Compliance", {}).get("Status"), "UNKNOWN"
        )
        for resource in finding.get("Resources", []):
            observations.append(
                {
                    "rule_id": rules.get(control, "SECURITY_HUB::" + control),
                    "resource": resource["Id"],
                    "status": status,
                    "region": resource.get("Region", finding.get("Region", "")),
                    "detail": finding.get("Description", ""),
                    "evidence_source": "securityhub",
                    "security_control_id": control,
                    "finding_id": finding["Id"],
                    "product_arn": finding["ProductArn"],
                    "title": finding.get("Title", control),
                    "aws_severity": finding.get("Severity", {}).get("Label", "MEDIUM"),
                    "frameworks": {
                        standard_id(s["StandardsId"]): [control]
                        for s in finding.get("Compliance", {}).get("AssociatedStandards", [])
                    },
                }
            )
    return observations


def config_evidence(account_id):
    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        from portal.standalone import config_evidence as local_evidence

        return local_evidence()
    config = client("config")
    aggregator = os.environ["CONFIG_AGGREGATOR"]
    mapping = json.loads(os.environ.get("CONFIG_RULE_MAPPING", "{}"))
    results = []
    if not mapping:
        return results
    for row in pages(
        config,
        "describe_aggregate_compliance_by_config_rules",
        "AggregateComplianceByConfigRules",
        ConfigurationAggregatorName=aggregator,
        Filters={"AccountId": account_id},
    ):
        rule_id = mapping.get(row["ConfigRuleName"])
        if not rule_id:
            continue
        for item in pages(
            config,
            "get_aggregate_compliance_details_by_config_rule",
            "AggregateEvaluationResults",
            ConfigurationAggregatorName=aggregator,
            ConfigRuleName=row["ConfigRuleName"],
            AccountId=account_id,
            AwsRegion=row["AwsRegion"],
            ComplianceType="NON_COMPLIANT",
        ):
            qualifier = item["EvaluationResultIdentifier"]["EvaluationResultQualifier"]
            results.append(
                {
                    "rule_id": rule_id,
                    "resource": qualifier["ResourceId"],
                    "status": "FAIL",
                    "region": row["AwsRegion"],
                    "detail": item.get("Annotation", "Config noncompliance"),
                    "evidence_source": "config",
                }
            )
        if row["Compliance"]["ComplianceType"] == "COMPLIANT":
            results.append(
                {
                    "rule_id": rule_id,
                    "resource": account_id,
                    "status": "PASS",
                    "region": row["AwsRegion"],
                    "evidence_source": "config",
                }
            )
    return results


def member_session(account_id):
    import boto3

    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        if account_id != os.environ["STANDALONE_ACCOUNT_ID"]:
            raise ValueError("Standalone collectors cannot scan a different account")
        return boto3.Session()

    assumed = client("sts").assume_role(
        RoleArn=f"arn:aws:iam::{account_id}:role/{os.environ.get('MEMBER_ROLE_NAME', 'GovPortalReadOnly')}",
        RoleSessionName="gov-portal-collector",
        DurationSeconds=900,
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=assumed["AccessKeyId"],
        aws_secret_access_key=assumed["SecretAccessKey"],
        aws_session_token=assumed["SessionToken"],
    )


def config_inventory(account_id):
    if len(account_id) != 12 or not account_id.isdigit():
        raise ValueError("Invalid account ID")
    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        from portal.standalone import inventory

        return inventory()
    return [
        json.loads(row)
        for row in pages(
            client("config"),
            "select_aggregate_resource_config",
            "Results",
            ConfigurationAggregatorName=os.environ["CONFIG_AGGREGATOR"],
            Expression=f"SELECT arn, awsRegion, resourceType, tags WHERE accountId = '{account_id}' AND resourceType IN ('AWS::EC2::Instance','AWS::EC2::Volume','AWS::RDS::DBInstance','AWS::DynamoDB::Table','AWS::EFS::FileSystem')",
        )
    ]


def backup_selection_matches(selection, arn, tags):
    """Match assignment configuration, not successful recovery-point coverage."""
    resources = selection.get("Resources", [])
    legacy = selection.get("ListOfTags", [])
    if any(fnmatchcase(arn, pattern) for pattern in selection.get("NotResources", [])):
        return False
    selected = any(fnmatchcase(arn, pattern) for pattern in resources) or any(
        tags.get(c["ConditionKey"].removeprefix("aws:ResourceTag/")) == c["ConditionValue"] for c in legacy
    )
    if (resources or legacy) and not selected:
        return False
    for operator, conditions in selection.get("Conditions", {}).items():
        if operator not in {"StringEquals", "StringNotEquals", "StringLike", "StringNotLike"}:
            raise ValueError("Unsupported AWS Backup condition")
        for condition in conditions:
            actual = tags.get(condition["ConditionKey"].removeprefix("aws:ResourceTag/"))
            if actual is None:
                return False
            expected = condition["ConditionValue"]
            matched = fnmatchcase(actual, expected) if "Like" in operator else actual == expected
            if matched if "Not" in operator else not matched:
                return False
    return True


def collect_member(account):
    session = member_session(account["id"])
    observations, deployments, errors = [], [], []
    required_tags = {"Owner", "CostCenter", "Service"}
    try:
        inventory = config_inventory(account["id"])
    except ClientError as exc:
        inventory = None
        errors.append({"source": "config_inventory", "error": exc.response["Error"]["Code"]})
    observations.append(
        {
            "rule_id": "REQUIRED_TAGS",
            "resource": account["id"],
            "status": "PASS" if required_tags <= {k for k, v in account["tags"].items() if v} else "FAIL",
            "detail": "Required account tags: Owner, CostCenter, Service",
        }
    )
    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        observations[-1].update(
            status="NOT_APPLICABLE",
            detail="Organization account tags are not available in standalone mode. Resource tags are checked from Config inventory.",
        )
    # StackSet instance versions and rollout state are read centrally, as delegated administrator.
    baseline = os.environ.get("BASELINE_STACKSET_NAME", "")
    if baseline and os.environ.get("DEPLOYMENT_MODE") != "standalone":
        cf = client("cloudformation")
        instances = list(
            pages(
                cf,
                "list_stack_instances",
                "Summaries",
                StackSetName=baseline,
                StackInstanceAccount=account["id"],
                CallAs="DELEGATED_ADMIN",
            )
        )
        grace = catalog()["profiles"][account["environment"]]["baseline_grace_days"]
        released = os.environ.get("BASELINE_RELEASED_AT", "")
        age = (
            (datetime.now(timezone.utc) - datetime.fromisoformat(released.replace("Z", "+00:00"))).days
            if released
            else None
        )
        for instance in instances:
            status = instance["Status"]
            deployments.append(
                {
                    "name": f"{baseline}/{instance['Region']}",
                    "is_baseline": True,
                    "version": os.environ.get("BASELINE_VERSION", "unknown")
                    if status == "CURRENT"
                    else "outdated",
                    "status": status,
                    "drift_status": instance.get("DriftStatus", "NOT_CHECKED"),
                    "deployed_at": None,
                    "region": instance["Region"],
                    "pipeline_run_url": None,
                }
            )
        observations.append(
            {
                "rule_id": "BASELINE_CURRENT",
                "resource": baseline,
                "status": "UNKNOWN"
                if not instances or (age is None and any(i["Status"] != "CURRENT" for i in instances))
                else "FAIL"
                if any(i["Status"] != "CURRENT" for i in instances) and age > grace
                else "PASS",
                "detail": f"Baseline grace window: {grace} days; release age: {age}",
            }
        )
    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        observations.append(
            {
                "rule_id": "BASELINE_CURRENT",
                "resource": account["id"],
                "status": "NOT_APPLICABLE",
                "detail": "Organization StackSet baselines are not used by the standalone profile.",
            }
        )
    regions = json.loads(os.environ.get("COLLECTION_REGIONS", "[]")) or [
        r["RegionName"]
        for r in session.client("ec2", config=CONFIG).describe_regions(AllRegions=False)["Regions"]
    ]
    for region in regions:
        try:
            cf = session.client("cloudformation", region_name=region, config=CONFIG)
            for stack in pages(cf, "describe_stacks", "Stacks"):
                deployments.append(
                    {
                        "name": stack["StackName"],
                        "version": next(
                            (
                                p["ParameterValue"]
                                for p in stack.get("Parameters", [])
                                if p["ParameterKey"] == "BaselineVersion"
                            ),
                            "unknown",
                        ),
                        "status": stack["StackStatus"],
                        "drift_status": stack.get("DriftInformation", {}).get(
                            "StackDriftStatus", "NOT_CHECKED"
                        ),
                        "deployed_at": (stack.get("LastUpdatedTime") or stack["CreationTime"]).isoformat(),
                        "region": region,
                        "pipeline_run_url": None,
                    }
                )
            backup = session.client("backup", region_name=region, config=CONFIG)
            # Membership evidence comes from actual selections, not merely the existence of a plan.
            selections = []
            for plan in pages(backup, "list_backup_plans", "BackupPlansList"):
                for selection in pages(
                    backup,
                    "list_backup_selections",
                    "BackupSelectionsList",
                    BackupPlanId=plan["BackupPlanId"],
                ):
                    selections.append(
                        backup.get_backup_selection(
                            BackupPlanId=plan["BackupPlanId"], SelectionId=selection["SelectionId"]
                        )["BackupSelection"]
                    )
            regional = [r for r in inventory or [] if r.get("awsRegion") == region and r.get("arn")]
            if inventory is None or not regional:
                observations.append(
                    {
                        "rule_id": "BACKUP_PLAN_ATTACHED",
                        "resource": f"{account['id']}:{region}",
                        "region": region,
                        "status": "UNKNOWN" if inventory is None else "NOT_APPLICABLE",
                        "detail": "Inventory unavailable"
                        if inventory is None
                        else "No supported backup resources in Config inventory for this Region",
                    }
                )
            for resource in regional:
                raw_tags = resource.get("tags", [])
                tags = (
                    raw_tags
                    if isinstance(raw_tags, dict)
                    else {t.get("key", t.get("Key")): t.get("value", t.get("Value")) for t in raw_tags}
                )
                observations.append(
                    {
                        "rule_id": "BACKUP_PLAN_ATTACHED",
                        "resource": resource["arn"],
                        "region": region,
                        "status": "PASS"
                        if any(backup_selection_matches(s, resource["arn"], tags) for s in selections)
                        else "FAIL",
                        "detail": "Resource matched against plan assignments, exclusions and tag conditions. Recovery-point health is separate evidence.",
                    }
                )
                observations.append(
                    {
                        "rule_id": "REQUIRED_TAGS",
                        "resource": resource["arn"],
                        "region": region,
                        "status": "PASS" if required_tags <= {k for k, v in tags.items() if v} else "FAIL",
                    }
                )
        except ClientError as exc:
            errors.append({"region": region, "error": exc.response["Error"]["Code"]})
            observations.append(
                {
                    "rule_id": "BACKUP_PLAN_ATTACHED",
                    "resource": f"{account['id']}:{region}",
                    "region": region,
                    "status": "UNKNOWN",
                    "detail": str(exc),
                }
            )
    s3 = session.client("s3", config=CONFIG)
    buckets = list(pages(s3, "list_buckets", "Buckets"))
    for bucket in buckets:
        try:
            config = s3.get_bucket_encryption(Bucket=bucket["Name"])["ServerSideEncryptionConfiguration"]
            kms = all(
                r["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] in {"aws:kms", "aws:kms:dsse"}
                for r in config["Rules"]
            )
            observations.append(
                {
                    "rule_id": "S3_KMS_ENCRYPTED",
                    "resource": f"arn:aws:s3:::{bucket['Name']}",
                    "status": "PASS" if kms else "FAIL",
                }
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            observations.append(
                {
                    "rule_id": "S3_KMS_ENCRYPTED",
                    "resource": f"arn:aws:s3:::{bucket['Name']}",
                    "status": "FAIL"
                    if code == "ServerSideEncryptionConfigurationNotFoundError"
                    else "UNKNOWN",
                    "detail": code,
                }
            )
    if not buckets:
        observations.append(
            {"rule_id": "S3_KMS_ENCRYPTED", "resource": account["id"], "status": "NOT_APPLICABLE"}
        )
    return observations, deployments, errors


def handler(event, context):
    account = event["account"]
    if (
        os.environ.get("DEPLOYMENT_MODE") == "standalone"
        and account["id"] != os.environ["STANDALONE_ACCOUNT_ID"]
    ):
        raise ValueError("Standalone collectors can only scan the configured test account")
    evidence, errors = [], []
    try:
        from portal.securityhub import available_standards
        from portal.store import get_store

        for standard in available_standards():
            get_store().put("standards", standard["id"], standard)
    except ClientError as exc:
        errors.append({"source": "securityhub_standards", "error": exc.response["Error"]["Code"]})
    for source in (securityhub_evidence, config_evidence):
        try:
            evidence.extend(source(account["id"]))
        except ClientError as exc:
            errors.append({"source": source.__name__, "error": exc.response["Error"]["Code"]})
    member, deployments, member_errors = collect_member(account)
    evidence.extend(member)
    errors.extend(member_errors)
    # Merge duplicated standards/aggregator evidence conservatively: failure and unknown win.
    merged = {}
    rank = {"FAIL": 3, "UNKNOWN": 2, "PASS": 1, "NOT_APPLICABLE": 0}
    for item in evidence:
        key = item["rule_id"], item["resource"], item.get("region", "")
        if key not in merged or rank[item["status"]] > rank[merged[key]["status"]]:
            merged[key] = item
    key = f"collection/{event['scan_id']}/{account['id']}.json"
    payload = {
        **event,
        "observations": list(merged.values()),
        "deployments": deployments,
        "collection_errors": errors,
    }
    client("s3").put_object(Bucket=os.environ["HISTORY_BUCKET"], Key=key, Body=json.dumps(payload).encode())
    print(
        json.dumps(
            {
                "account_id": account["id"],
                "scan_id": event["scan_id"],
                "evidence_count": len(merged),
                "errors": errors,
                "at": timestamp(),
            }
        )
    )
    return {
        "bucket": os.environ["HISTORY_BUCKET"],
        "key": key,
        "account_id": account["id"],
        "scan_id": event["scan_id"],
        "retried": event.get("retried", False),
    }
