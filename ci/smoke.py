"""Verify anonymous API rejection, IAM-authorized API reads and a completed sandbox rescan."""

import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import uuid4
import boto3

parser = argparse.ArgumentParser()
parser.add_argument("--stage", required=True, choices=["central", "dev", "testing", "prod"])
args = parser.parse_args()
output = json.loads(Path("build/outputs.json").read_text())
try:
    with urlopen(output["frontend_config"]["apiUrl"] + "/environments", timeout=15):
        raise RuntimeError("API unexpectedly allowed an anonymous request")
except HTTPError as exc:
    if exc.code != 401:
        raise
event = {
    "rawPath": "/environments",
    "requestContext": {
        "http": {"method": "GET"},
        "authorizer": {"jwt": {"claims": {"sub": "ci-smoke", "cognito:groups": ["gov-portal-admins"]}}},
    },
}
# This is a direct, IAM-authorized Lambda integration check; it does not claim to exercise SAML login.
response = boto3.client("lambda").invoke(
    FunctionName=output["api_function"]["name"] + ":live", Payload=json.dumps(event).encode()
)
result = json.loads(response["Payload"].read())
assert result["statusCode"] == 200, result
assert len(json.loads(result["body"])["items"]) == 4
if args.stage != "prod":
    states = boto3.client("stepfunctions")
    started = time.time()
    run = states.start_execution(
        stateMachineArn=output["state_machine_arn"], name="smoke-" + str(uuid4()), input="{}"
    )
    for _ in range(60):
        state = states.describe_execution(executionArn=run["executionArn"])["status"]
        if state == "SUCCEEDED":
            break
        if state != "RUNNING":
            raise RuntimeError(f"Sandbox scan failed: {state}")
        time.sleep(8)
    else:
        raise TimeoutError("Sandbox scan exceeded eight minutes")
    table = boto3.resource("dynamodb").Table(output["state_table"])
    items, page = [], {}
    while True:
        page = table.scan(
            **({"ExclusiveStartKey": page["LastEvaluatedKey"]} if page.get("LastEvaluatedKey") else {})
        )
        items.extend(page["Items"])
        if not page.get("LastEvaluatedKey"):
            break
    from datetime import datetime

    fresh = [
        i
        for i in items
        if i.get("kind") == "accounts"
        and i["data"].get("last_scanned_at")
        and datetime.fromisoformat(i["data"]["last_scanned_at"]).timestamp() >= started
    ]
    assert fresh, "No newly scanned sandbox account was written to DynamoDB"
    depth = boto3.client("sqs").get_queue_attributes(
        QueueUrl=output["dlq_url"],
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    assert all(int(v) == 0 for v in depth.values()), depth
print(f"Smoke checks passed: {args.stage}")
