"""Small allowlisted AWS runbooks. Plans are inspected again before mutation."""

import json
import os
import re
import boto3
from botocore.exceptions import ClientError
from portal.aws import CONFIG, client
from portal.change_policy import approval_environment


def current_scope(account):
    if os.environ.get("PORTAL_MODE") == "demo" or os.environ.get("DEPLOYMENT_MODE") == "standalone":
        return approval_environment(account)
    org = client("organizations")
    names, child = [], account["id"]
    while True:
        parent = org.list_parents(ChildId=child)["Parents"][0]
        if parent["Type"] == "ROOT":
            break
        names.append(
            org.describe_organizational_unit(OrganizationalUnitId=parent["Id"])["OrganizationalUnit"]["Name"]
        )
        child = parent["Id"]
    return approval_environment({**account, "ou_path": "/".join(names)})


def session(account_id, write=False):
    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        if account_id != os.environ["STANDALONE_ACCOUNT_ID"]:
            raise ValueError("A standalone pilot can only change its own account")
        return boto3.Session()
    name = (
        os.environ.get("REMEDIATION_MEMBER_ROLE", "GovPortalRemediation")
        if write
        else os.environ.get("MEMBER_ROLE_NAME", "GovPortalReadOnly")
    )
    credentials = client("sts").assume_role(
        RoleArn=f"arn:aws:iam::{account_id}:role/{name}",
        RoleSessionName="gov-remediation",
        DurationSeconds=900,
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def resource_name(target):
    if target["action"] == "s3_bucket_versioning":
        match = re.fullmatch(r"arn:aws:s3:::([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])", target["resource"])
        if not match:
            raise ValueError("Versioning requires an exact S3 bucket ARN")
        return match[1]
    return target["account_id"]


def inspect(target, sdk=None):
    sdk = sdk or session(target["account_id"])
    action, region = target["action"], target["region"]
    if action == "s3_account_public_access":
        try:
            state = sdk.client("s3control", region_name=region, config=CONFIG).get_public_access_block(
                AccountId=target["account_id"]
            )["PublicAccessBlockConfiguration"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchPublicAccessBlockConfiguration":
                raise
            state = {key: False for key in target["desired"]}
        return state, False
    if action == "ebs_default_encryption":
        state = sdk.client("ec2", region_name=region, config=CONFIG).get_ebs_encryption_by_default()
        return {"EbsEncryptionByDefault": state["EbsEncryptionByDefault"]}, False
    if action == "s3_bucket_versioning":
        bucket = resource_name(target)
        s3 = sdk.client("s3", region_name=region, config=CONFIG)
        acl = s3.get_bucket_acl(Bucket=bucket, ExpectedBucketOwner=target["account_id"])
        if not acl.get("Owner", {}).get("ID"):
            raise ValueError("Bucket ownership could not be verified")
        try:
            tags = s3.get_bucket_tagging(Bucket=bucket, ExpectedBucketOwner=target["account_id"])["TagSet"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchTagSet":
                raise
            tags = []
        tags = {t["Key"].lower(): t["Value"].lower() for t in tags}
        managed = tags.get("managedby") in {"terraform", "cloudformation"} or any(
            k.startswith("aws:cloudformation:") for k in tags
        )
        state = s3.get_bucket_versioning(Bucket=bucket, ExpectedBucketOwner=target["account_id"])
        if state.get("MFADelete") == "Enabled":
            raise ValueError("Buckets with MFA Delete require an owner-managed change")
        return {"Status": state.get("Status", "Suspended")}, managed
    raise ValueError("No executable runbook for this finding")


def apply(target):
    if os.environ.get("REMEDIATION_ENABLED") != "true":
        raise ValueError("Live remediation is disabled in Terraform configuration")
    sdk = session(target["account_id"], write=True)
    if target["action"] == "s3_account_public_access":
        sdk.client("s3control", region_name=target["region"], config=CONFIG).put_public_access_block(
            AccountId=target["account_id"], PublicAccessBlockConfiguration=target["desired"]
        )
    elif target["action"] == "ebs_default_encryption":
        sdk.client("ec2", region_name=target["region"], config=CONFIG).enable_ebs_encryption_by_default()
    elif target["action"] == "s3_bucket_versioning":
        sdk.client("s3", region_name=target["region"], config=CONFIG).put_bucket_versioning(
            Bucket=resource_name(target),
            ExpectedBucketOwner=target["account_id"],
            VersioningConfiguration=target["desired"],
        )
    else:
        raise ValueError("Unsupported action")
    print(
        json.dumps(
            {
                "event": "remediation.applied",
                "account_id": target["account_id"],
                "action": target["action"],
                "resource": target["resource"],
            }
        )
    )
