"""Trusted-main GitOps runner: constrained variable patches, reviewed binary plans, IAM callbacks.

Install this repository's portal package/requirements in ns-infrastructure-terraform.
Never execute Terraform or scripts from a pull request checkout.
"""

import argparse
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import boto3
from portal.change_policy import digest, set_variable, safe_repo_path
from portal.remediation_github import GitHub


class WorkflowGitHub(GitHub):
    def __init__(self):
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.token = os.environ["GH_TOKEN"]
        self.branch = "main"


def callback(payload, validate=False):
    function = os.environ["REMEDIATION_VALIDATE_FUNCTION" if validate else "REMEDIATION_CALLBACK_FUNCTION"]
    if not validate:
        payload["workflow_run_id"] = os.environ["GITHUB_RUN_ID"]
    response = boto3.client("lambda").invoke(FunctionName=function, Payload=json.dumps(payload).encode())
    result = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        raise ValueError(result.get("errorMessage", "Governance callback rejected the request"))
    return result


def tree_hash(root):
    items = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(p in {".git", ".terraform", "build", "node_modules", "__pycache__"} for p in relative.parts):
            continue
        if relative.parts[:2] == (".governance", "changes"):
            continue
        if path.is_symlink():
            raise ValueError("Symlinks are not supported in governed workspaces")
        if path.is_file():
            items.append([relative.as_posix(), sha256(path.read_bytes()).hexdigest()])
    return digest(items)


def source_tree(ref, destination):
    archive = subprocess.check_output(["git", "archive", ref])
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        files.extractall(destination, filter="data")


def validate_patch(github, pr, payload):
    """Only server-bound, typed existing variables and one immutable manifest may change."""
    expected = {}
    original_shas = {}
    for target in payload["targets"]:
        binding = target.get("workspace")
        if not binding:
            if payload["driver"] == "terraform":
                raise ValueError("Terraform target has no workspace binding")
            continue
        filename = safe_repo_path(binding["variable_file"])
        directory = safe_repo_path(binding["directory"])
        if not filename.startswith(directory + "/"):
            raise ValueError("Variable file is outside the approved workspace")
        if filename not in expected:
            raw, sha = github.file(filename, pr["base"]["sha"])
            if sha != binding["file_sha"]:
                raise ValueError("Base variables changed; prepare a new dashboard request")
            expected[filename] = json.loads(raw)
            original_shas[filename] = sha
        set_variable(expected[filename], binding["variable_path"], binding["desired"])
    manifest_path = f".governance/changes/{payload['environment']}/{payload['request_id']}.json"
    expected[manifest_path] = {**payload, "plan_hash": digest(payload)}
    changed = []
    for page in range(1, 31):
        files = github.request("GET", f"/pulls/{pr['number']}/files?per_page=100&page={page}")
        changed.extend(files)
        if len(files) < 100:
            break
    expected_changed = {manifest_path}
    for filename in expected:
        if filename == manifest_path:
            continue
        _, head_file_sha = github.file(filename, pr["head"]["sha"])
        if head_file_sha != original_shas[filename]:
            expected_changed.add(filename)
    if {f["filename"] for f in changed} != expected_changed or any(
        f["status"] not in {"added", "modified"} for f in changed
    ):
        raise ValueError("PR includes files outside the immutable governance plan")
    for filename, value in expected.items():
        raw, _ = github.file(filename, pr["head"]["sha"])
        if json.loads(raw) != value:
            raise ValueError("PR variable contents differ from the dashboard plan")
    return expected


def constrained_plan(plan, targets):
    permitted = {t["workspace"]["resource_address"] for t in targets}
    for change in plan.get("resource_changes", []):
        actions = change["change"]["actions"]
        if actions == ["no-op"] or change.get("mode") == "data":
            continue
        if change["address"] not in permitted or actions != ["update"]:
            raise ValueError(f"Out-of-scope or destructive Terraform change: {change['address']}")


def validate_provider_scope(plan, targets):
    account_ids = {t["account_id"] for t in targets}
    if len(account_ids) != 1:
        raise ValueError("One account per workspace is required")
    account_id = next(iter(account_ids))
    providers = plan.get("configuration", {}).get("provider_config", {})
    aws = [p for p in providers.values() if p.get("full_name") == "registry.terraform.io/hashicorp/aws"]
    if not aws:
        raise ValueError("No reviewed AWS provider configuration found")
    for provider in aws:
        expressions = provider.get("expressions", {})
        if expressions.get("profile", {}).get("constant_value") != "governance-target":
            raise ValueError("AWS providers must use the fixed governance-target profile")
        allowed = expressions.get("allowed_account_ids", {})
        constant = allowed.get("constant_value")
        from_variable = (
            allowed.get("references") == ["var.account_id"]
            and plan.get("variables", {}).get("account_id", {}).get("value") == account_id
        )
        if constant != [account_id] and not from_variable:
            raise ValueError("AWS provider allowed_account_ids must match the requested account")


def verify_drift(saved_hash, fresh, targets):
    constrained_plan(fresh, targets)
    if digest(fresh.get("resource_changes", [])) != saved_hash:
        raise ValueError("AWS configuration drifted since review; prepare a new request")


def profile(account, operation):
    # Backend uses ambient central OIDC credentials; AWS provider uses this fixed profile.
    config = Path(tempfile.gettempdir()) / ("governance-aws-" + os.environ.get("GITHUB_RUN_ID", "local"))
    config.write_text(
        "[profile governance-target]\ncredential_process = "
        + sys.executable
        + " "
        + str(Path(__file__).resolve())
        + f" credentials --account {account} --operation {operation}\n"
    )
    os.environ["AWS_CONFIG_FILE"] = str(config)


def terraform(directory, *arguments):
    return subprocess.run(
        ["terraform", f"-chdir={directory}", *arguments], check=True, capture_output=True, text=True
    ).stdout


def initialize(directory, relative):
    terraform(
        directory,
        "init",
        "-input=false",
        "-lockfile=readonly",
        f"-backend-config=bucket={os.environ['TF_STATE_BUCKET']}",
        f"-backend-config=key={relative}.tfstate",
        f"-backend-config=region={os.environ['AWS_REGION']}",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["plan", "apply", "credentials"])
    parser.add_argument("--account")
    parser.add_argument("--operation", dest="credential_operation", choices=["plan", "apply"])
    args = parser.parse_args()
    if args.operation == "credentials":
        session = boto3.Session()
        if args.account == os.environ["CENTRAL_ACCOUNT_ID"]:
            credentials = session.get_credentials().get_frozen_credentials()
            print(
                json.dumps(
                    {
                        "Version": 1,
                        "AccessKeyId": credentials.access_key,
                        "SecretAccessKey": credentials.secret_key,
                        "SessionToken": credentials.token,
                    }
                )
            )
        else:
            role = "GovPortalReadOnly" if args.credential_operation == "plan" else "GovPortalRemediation"
            credentials = session.client("sts").assume_role(
                RoleArn=f"arn:aws:iam::{args.account}:role/{role}",
                RoleSessionName="governance-terraform",
                DurationSeconds=3600,
            )["Credentials"]
            print(
                json.dumps(
                    {
                        "Version": 1,
                        "AccessKeyId": credentials["AccessKeyId"],
                        "SecretAccessKey": credentials["SecretAccessKey"],
                        "SessionToken": credentials["SessionToken"],
                        "Expiration": credentials["Expiration"].isoformat(),
                    }
                )
            )
        return
    github = WorkflowGitHub()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    pr = github.request("GET", f"/pulls/{event['pull_request']['number']}")
    if (
        pr["head"]["repo"]["full_name"] != github.repo
        or pr["base"]["ref"] != "main"
        or not pr["head"]["ref"].startswith("governance/")
    ):
        raise ValueError("Only governance requests in the configured private monorepo are supported")
    request_id = pr["head"]["ref"].split("/", 1)[1]
    manifests = [
        f
        for f in github.request("GET", f"/pulls/{pr['number']}/files?per_page=100")
        if f["filename"].startswith(".governance/changes/")
    ]
    if len(manifests) != 1:
        raise ValueError("Exactly one governance manifest is required")
    raw, _ = github.file(manifests[0]["filename"], pr["head"]["sha"])
    payload = json.loads(raw)
    plan_hash = payload.pop("plan_hash")
    if payload["request_id"] != request_id or digest(payload) != plan_hash:
        raise ValueError("Invalid manifest hash")
    validated = callback(
        {"operation": "validate", "request_id": request_id, "plan_hash": plan_hash}, validate=True
    )
    if validated["manifest"] != payload:
        raise ValueError("Manifest differs from the central change ledger")
    # GitHub's base branch may advance; compare against the immutable creation parent.
    pr["base"]["sha"] = validated["base_sha"]
    patches = validate_patch(github, pr, payload)
    output = Path("build/remediation").resolve()
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / "metadata.json"
    if args.operation == "plan":
        metadata = {
            "request_id": request_id,
            "head_sha": pr["head"]["sha"],
            "plan_hash": plan_hash,
            "workspaces": [],
            "requires_approval": payload["requires_approval"],
        }
    else:
        metadata = json.loads(metadata_path.read_text())
        if any(
            metadata[k] != v
            for k, v in {
                "request_id": request_id,
                "head_sha": pr["head"]["sha"],
                "plan_hash": plan_hash,
            }.items()
        ):
            raise ValueError("Saved plan artifact does not match the approved head")
        callback({"operation": "approve", "request_id": request_id, "plan_hash": plan_hash})
    if payload["driver"] != "terraform":
        metadata_path.write_text(json.dumps(metadata))
        print("Validated manifest; execution is handled by the central Step Functions workflow after merge.")
        return
    groups = {}
    for target in payload["targets"]:
        groups.setdefault(target["workspace"]["directory"], []).append(target)
    outcomes = {}
    for index, (relative, targets) in enumerate(sorted(groups.items())):
        try:
            if len({t["account_id"] for t in targets}) != 1:
                raise ValueError("A workspace must target exactly one account")
            profile(targets[0]["account_id"], args.operation)
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source_tree(pr["base"]["sha"] if args.operation == "plan" else pr["merge_commit_sha"], root)
                if args.operation == "plan":
                    for filename, value in patches.items():
                        path = root / filename
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
                checksum = tree_hash(root)
                workspace = root / safe_repo_path(relative)
                binary = output / f"workspace-{index}.tfplan"
                if args.operation == "apply":
                    saved = metadata["workspaces"][index]
                    if (
                        saved["directory"] != relative
                        or saved["tree_hash"] != checksum
                        or saved["sha256"] != sha256(binary.read_bytes()).hexdigest()
                    ):
                        raise ValueError(
                            "Configuration changed since the reviewed plan; create a new request"
                        )
                initialize(workspace, relative)
                if args.operation == "plan":
                    terraform(workspace, "plan", "-input=false", f"-out={binary}")
                    plan = json.loads(terraform(workspace, "show", "-json", str(binary)))
                    constrained_plan(plan, targets)
                    validate_provider_scope(plan, targets)
                    (output / f"workspace-{index}.txt").write_text(
                        terraform(workspace, "show", "-no-color", str(binary))
                    )
                    metadata["workspaces"].append(
                        {
                            "directory": relative,
                            "tree_hash": checksum,
                            "sha256": sha256(binary.read_bytes()).hexdigest(),
                            "change_hash": digest(plan.get("resource_changes", [])),
                        }
                    )
                else:
                    # Detect post-review AWS drift, then apply only the original reviewed binary.
                    candidate = workspace / ".governance-validation.tfplan"
                    terraform(workspace, "plan", "-input=false", f"-out={candidate}")
                    fresh = json.loads(terraform(workspace, "show", "-json", str(candidate)))
                    verify_drift(saved["change_hash"], fresh, targets)
                    callback({"operation": "approve", "request_id": request_id, "plan_hash": plan_hash})
                    terraform(workspace, "apply", "-input=false", str(binary))
                    for target in targets:
                        outcomes[target["id"]] = {
                            "status": "SUCCEEDED",
                            "message": "Reviewed Terraform plan applied. Security Hub re-evaluation requested; finding status remains source-reported.",
                        }
        except Exception as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                (output / f"workspace-{index}-error.txt").write_text(
                    (exc.stdout or "") + "\n" + (exc.stderr or "")
                )
            else:
                (output / f"workspace-{index}-error.txt").write_text(str(exc))
            if args.operation == "plan":
                raise
            # Do not expose state/secrets from Terraform stdout/stderr to the dashboard.
            for target in targets:
                outcomes[target["id"]] = {
                    "status": "FAILED",
                    "message": f"Terraform execution failed ({type(exc).__name__}); inspect the restricted workflow logs and prepare a fresh plan.",
                }
    if args.operation == "plan":
        metadata_path.write_text(json.dumps(metadata, indent=2))
    else:
        callback(
            {"operation": "complete", "request_id": request_id, "plan_hash": plan_hash, "outcomes": outcomes}
        )
        if any(v["status"] == "FAILED" for v in outcomes.values()):
            raise RuntimeError(
                "One or more workspaces failed; the central dashboard has the per-target results"
            )


if __name__ == "__main__":
    main()
