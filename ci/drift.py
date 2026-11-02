import json
import os
from pathlib import Path
import subprocess

stage = os.environ["STAGE"]
profile = os.environ.get("TFVARS_PROFILE", stage)
if profile not in {"central", "dev", "testing", "prod", "standalone"}:
    raise ValueError("Invalid Terraform profile")
output = Path("build/drift.txt")
with output.open("w") as stream:
    result = subprocess.run(
        [
            "terraform",
            "-chdir=infra/stacks/portal",
            "plan",
            "-input=false",
            "-no-color",
            "-detailed-exitcode",
            f"-var-file=../../envs/{profile}.tfvars.json",
            f"-var=artifact_bucket={os.environ['ARTIFACT_BUCKET']}",
            f"-var=artifact_version={os.environ['ARTIFACT_VERSION']}",
            f"-var=artifact_hash={os.environ['ARTIFACT_HASH']}",
        ],
        stdout=stream,
        stderr=subprocess.STDOUT,
    )
if result.returncode not in (0, 2):
    print(output.read_text())
    raise SystemExit(result.returncode)
title = f"Terraform drift detected: {stage}"
if result.returncode == 2:
    # Keep confidential plan details in the restricted workflow artifact, not in issue bodies.
    existing = json.loads(
        subprocess.check_output(
            ["gh", "issue", "list", "--state", "open", "--search", title, "--json", "title"]
        )
    )
    if not any(issue["title"] == title for issue in existing):
        body = Path("build/issue.md")
        body.write_text(
            f"The nightly Terraform plan detected drift in `{stage}`.\n\nReview the restricted workflow plan artifact: {os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}\n\nNo changes were applied.\n"
        )
        subprocess.run(["gh", "issue", "create", "--title", title, "--body-file", str(body)], check=True)
print("Drift detected" if result.returncode == 2 else "No drift")
