"""Optional production release notification; credentials remain in Secrets Manager."""

import json
import os
from urllib.request import Request, urlopen
import boto3

secret = os.environ.get("RELEASE_SLACK_SECRET_ARN")
if secret:
    webhook = boto3.client("secretsmanager").get_secret_value(SecretId=secret)["SecretString"]
    if not webhook.startswith("https://hooks.slack.com/services/"):
        raise ValueError("Release notification secret must contain a Slack webhook URL")
    text = f"Governance portal production release: {os.environ['GITHUB_SHA']}\n{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    with urlopen(
        Request(
            webhook, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"}
        ),
        timeout=10,
    ) as response:
        response.read()
else:
    print("No release notification webhook configured")
