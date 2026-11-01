"""Immutable artifact upload and post-apply publication; never runs terraform apply itself."""

import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["upload", "publish"])
parser.add_argument("--stage", required=True, choices=["central", "dev", "testing", "prod"])
args = parser.parse_args()
s3 = boto3.client("s3")
manifest = json.loads((ROOT / "build/manifest.json").read_text())
for name, checksum in manifest.items():
    if hashlib.sha256((ROOT / "build" / name).read_bytes()).hexdigest() != checksum["sha256"]:
        raise RuntimeError(f"Artifact verification failed: {name}")
if args.action == "upload":
    for name in manifest:
        key = f"{os.environ['GITHUB_SHA']}/{name}"
        try:
            s3.put_object(
                Bucket=os.environ["ARTIFACT_BUCKET"],
                Key=key,
                Body=(ROOT / "build" / name).read_bytes(),
                IfNoneMatch="*",
                Metadata={"sha256": manifest[name]["sha256"]},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"PreconditionFailed", "ConditionalRequestConflict"}:
                raise
            existing = s3.get_object(Bucket=os.environ["ARTIFACT_BUCKET"], Key=key)["Body"].read()
            if hashlib.sha256(existing).hexdigest() != manifest[name]["sha256"]:
                raise RuntimeError("Refusing to replace an existing release artifact") from exc
else:
    output = json.loads(
        subprocess.check_output(["terraform", "-chdir=infra/stacks/portal", "output", "-json"], cwd=ROOT)
    )
    values = {k: v["value"] for k, v in output.items()}
    (ROOT / "build/outputs.json").write_text(json.dumps(values))
    function = values["api_function"]
    current = boto3.client("lambda").get_alias(FunctionName=function["name"], Name="live")["FunctionVersion"]
    if current != function["version"]:
        appspec = {
            "version": 0.0,
            "Resources": [
                {
                    "PortalAPI": {
                        "Type": "AWS::Lambda::Function",
                        "Properties": {
                            "Name": function["name"],
                            "Alias": "live",
                            "CurrentVersion": current,
                            "TargetVersion": function["version"],
                        },
                    }
                }
            ],
        }
        deploy = boto3.client("codedeploy")
        deployment = deploy.create_deployment(
            applicationName=values["codedeploy_app"],
            deploymentGroupName=values["codedeploy_group"],
            deploymentConfigName="CodeDeployDefault.LambdaCanary10Percent10Minutes"
            if args.stage in {"central", "prod"}
            else "CodeDeployDefault.LambdaAllAtOnce",
            revision={"revisionType": "AppSpecContent", "appSpecContent": {"content": json.dumps(appspec)}},
        )["deploymentId"]
        for _ in range(120):
            result = deploy.get_deployment(deploymentId=deployment)["deploymentInfo"]
            if result["status"] == "Succeeded":
                break
            if result["status"] in {"Failed", "Stopped"}:
                raise RuntimeError(
                    f"CodeDeploy {result['status']}: {deployment}. Inspect automatic rollback."
                )
            time.sleep(10)
        else:
            raise TimeoutError(f"CodeDeploy still running: {deployment}")
    with tempfile.TemporaryDirectory() as directory:
        with tarfile.open(ROOT / "build/frontend.tar.gz") as archive:
            archive.extractall(directory, filter="data")
        frontend = Path(directory) / "frontend"
        # Upload content-addressed assets first; publish index/config last to avoid partial releases.
        for path in sorted(frontend.rglob("*"), key=lambda p: p.name == "index.html"):
            if path.is_file():
                s3.put_object(
                    Bucket=values["frontend_bucket"],
                    Key=path.relative_to(frontend).as_posix(),
                    Body=path.read_bytes(),
                    ContentType=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    CacheControl="no-cache"
                    if path.name == "index.html"
                    else "public,max-age=31536000,immutable",
                )
        s3.put_object(
            Bucket=values["frontend_bucket"],
            Key="config.json",
            Body=json.dumps(values["frontend_config"]).encode(),
            ContentType="application/json",
            CacheControl="no-store",
        )
    boto3.client("cloudfront").create_invalidation(
        DistributionId=values["distribution_id"],
        InvalidationBatch={
            "Paths": {"Quantity": 2, "Items": ["/index.html", "/config.json"]},
            "CallerReference": f"{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}",
        },
    )
    print(values["portal_url"])
