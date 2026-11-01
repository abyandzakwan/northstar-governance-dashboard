"""Private monorepo integration. Tokens stay in Secrets Manager and process memory."""

import base64
from fnmatch import fnmatchcase
import json
import os
from urllib.request import Request, urlopen
from urllib.parse import quote
import yaml
from portal.aws import client
from portal.change_policy import digest, manifest, safe_repo_path, set_variable


class GitHub:
    def __init__(self):
        self.repo = os.environ.get("REMEDIATION_REPOSITORY", "")
        if len(self.repo.split("/")) != 2 or not all(self.repo.split("/")):
            raise ValueError("Configure owner/ns-infrastructure-terraform as REMEDIATION_REPOSITORY")
        raw = client("secretsmanager").get_secret_value(SecretId=os.environ["REMEDIATION_GITHUB_SECRET_ARN"])[
            "SecretString"
        ]
        self.token = json.loads(raw)["token"] if raw.startswith("{") else raw
        self.branch = os.environ.get("REMEDIATION_BASE_BRANCH", "main")
        if self.request("GET", "").get("private") is not True:
            raise ValueError("Remediation findings require a private GitHub repository")

    def request(self, method, path, body=None):
        request = Request(
            f"https://api.github.com/repos/{self.repo}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "gov-portal",
            },
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read() or "{}")

    def file(self, path, ref=None):
        response = self.request(
            "GET",
            f"/contents/{quote(safe_repo_path(path), safe='/')}?ref={quote(ref or self.branch, safe='')}",
        )
        return base64.b64decode(response["content"]).decode(), response["sha"]

    def binding(self, target):
        config, _ = self.file("governance/workspaces.yaml")
        for workspace in yaml.safe_load(config).get("workspaces", []):
            if (workspace["account_id"], workspace["region"], workspace["environment"]) != (
                target["account_id"],
                target["region"],
                target["environment"],
            ):
                continue
            binding = workspace.get("bindings", {}).get(target["runbook_id"]) or workspace.get(
                "bindings", {}
            ).get(target["rule_id"])
            if binding and fnmatchcase(target["resource"], binding.get("resource_pattern", "")):
                directory = safe_repo_path(workspace["directory"])
                variable_file = safe_repo_path(directory + "/" + workspace["variable_file"])
                raw, sha = self.file(variable_file)
                original = json.loads(raw)
                old_value = original
                for key in binding["variable_path"]:
                    old_value = old_value[key]
                patched = json.loads(raw)
                set_variable(patched, binding["variable_path"], binding["desired"])
                return {
                    "directory": directory,
                    "variable_file": variable_file,
                    "file_sha": sha,
                    "variable_path": binding["variable_path"],
                    "desired": binding["desired"],
                    "resource_address": binding["resource_address"],
                    "before": old_value,
                    "workspace_digest": None,
                }
        raise ValueError("No reviewed Terraform workspace binding exists for this resource and environment")

    def create_pr(self, job):
        ref = self.request("GET", "/git/ref/heads/" + quote(self.branch, safe=""))
        parent = ref["object"]["sha"]
        base = self.request("GET", "/git/commits/" + parent)
        files = {}
        for target in job["targets"]:
            binding = target.get("workspace")
            if not binding:
                continue
            path = binding["variable_file"]
            if path not in files:
                text, sha = self.file(path, parent)
                if sha != binding["file_sha"]:
                    raise ValueError("Terraform configuration changed since planning; create a new plan")
                files[path] = json.loads(text)
            set_variable(files[path], binding["variable_path"], binding["desired"])
        payload = {**manifest(job), "plan_hash": job["plan_hash"]}
        path = f".governance/changes/{job['environment']}/{job['id']}.json"
        files[path] = payload
        tree = []
        for filename, value in files.items():
            blob = self.request(
                "POST",
                "/git/blobs",
                {"content": json.dumps(value, sort_keys=True, indent=2) + "\n", "encoding": "utf-8"},
            )
            tree.append({"path": filename, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self.request("POST", "/git/trees", {"base_tree": base["tree"]["sha"], "tree": tree})
        commit = self.request(
            "POST",
            "/git/commits",
            {"message": f"Governance change {job['id']}", "tree": tree["sha"], "parents": [parent]},
        )
        branch = "governance/" + job["id"]
        self.request("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
        pr = self.request(
            "POST",
            "/pulls",
            {
                "head": branch,
                "base": self.branch,
                "title": f"[{job['environment']}] Governance remediation {job['id'][:8]}",
                "body": f"Change request `{job['id']}`\n\nPlan SHA-256: `{job['plan_hash']}`\n\nTargets: {len(job['targets'])}. Driver: {job['driver']}.\n\n"
                + (
                    "Production requires an approving human review before merge and apply."
                    if job["requires_approval"]
                    else "Dev/staging: passing automation checks may merge this request without human approval."
                ),
            },
        )
        return {
            "number": pr["number"],
            "url": pr["html_url"],
            "head_sha": commit["sha"],
            "base_sha": parent,
            "path": path,
            "repository": self.repo,
        }

    def verify(self, job, require_merged=True):
        ref = job.get("pull_request")
        if not ref or ref["repository"] != self.repo:
            raise ValueError("No matching production pull request")
        pr = self.request("GET", f"/pulls/{ref['number']}")
        if (
            pr["head"]["sha"] != ref["head_sha"]
            or pr["head"]["ref"] != "governance/" + job["id"]
            or pr["base"]["ref"] != self.branch
        ):
            raise ValueError("Pull request changed after the plan was created")
        if pr["head"]["repo"]["full_name"] != self.repo or (require_merged and not pr.get("merged")):
            raise ValueError("Pull request must be merged from the configured monorepo")
        raw, _ = self.file(ref["path"], pr["merge_commit_sha"] if require_merged else ref["head_sha"])
        payload = json.loads(raw)
        if payload.pop("plan_hash", None) != job["plan_hash"] or digest(payload) != job["plan_hash"]:
            raise ValueError("Reviewed manifest does not match the immutable remediation plan")
        if not require_merged:
            return pr
        checks = self.request("GET", f"/commits/{ref['head_sha']}/check-runs?per_page=100")["check_runs"]
        check_name = os.environ.get("REMEDIATION_PLAN_CHECK", "Terraform remediation plan")
        if not any(
            c["name"] == check_name
            and c["conclusion"] == "success"
            and c.get("app", {}).get("slug") == "github-actions"
            for c in checks
        ):
            raise ValueError("The approved head has no successful remediation plan check")
        if job["requires_approval"]:
            latest = {}
            for page in range(1, 21):
                reviews = self.request("GET", f"/pulls/{ref['number']}/reviews?per_page=100&page={page}")
                for review in reviews:
                    if review["state"] != "COMMENTED":
                        latest[review["user"]["login"]] = review
                if len(reviews) < 100:
                    break
            if any(r["state"] == "CHANGES_REQUESTED" for r in latest.values()):
                raise ValueError("A requested-change review remains unresolved")
            allowed = set(filter(None, os.environ.get("REMEDIATION_APPROVERS", "").split(",")))
            approved = []
            for name, review in latest.items():
                if (
                    review["state"] != "APPROVED"
                    or review["commit_id"] != ref["head_sha"]
                    or name == pr["user"]["login"]
                    or review["user"]["type"] == "Bot"
                    or (allowed and name not in allowed)
                ):
                    continue
                permission = self.request("GET", f"/collaborators/{quote(name, safe='')}/permission")[
                    "permission"
                ]
                if permission in {"admin", "maintain", "write"}:
                    approved.append(name)
            if not approved:
                raise ValueError(
                    "Production needs a current human approval from an authorized monorepo reviewer"
                )
            return {"head_sha": ref["head_sha"], "merge_sha": pr["merge_commit_sha"], "reviewers": approved}
        return {"head_sha": ref["head_sha"], "merge_sha": pr["merge_commit_sha"], "reviewers": []}
