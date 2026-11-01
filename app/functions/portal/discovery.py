"""Discover all active accounts; tag overrides precede inherited OU paths."""

import json
import os
from datetime import timedelta
from uuid import uuid4
from portal.aws import client, pages
from portal.domain import evaluate, parse_time, resolve_environment, summaries, timestamp
from portal.store import get_store


def discover(org, target_ous=()):
    cache = {}

    def ancestry(child):
        if child in cache:
            return cache[child]
        parents = org.list_parents(ChildId=child)["Parents"]
        if not parents:
            return [], []
        parent = parents[0]
        if parent["Type"] == "ROOT":
            value = ([parent["Id"]], ["Root"])
        else:
            ids, names = ancestry(parent["Id"])
            name = org.describe_organizational_unit(OrganizationalUnitId=parent["Id"])["OrganizationalUnit"][
                "Name"
            ]
            value = (ids + [parent["Id"]], names + [name])
        cache[child] = value
        return value

    accounts = []
    for raw in pages(org, "list_accounts", "Accounts"):
        if raw.get("State", raw.get("Status")) != "ACTIVE":
            continue
        ids, names = ancestry(raw["Id"])
        if target_ous and not set(ids).intersection(target_ous):
            continue
        tags = {
            t["Key"]: t["Value"] for t in pages(org, "list_tags_for_resource", "Tags", ResourceId=raw["Id"])
        }
        path = "/".join(names)
        accounts.append(
            {
                "id": raw["Id"],
                "name": raw["Name"],
                "ou_path": path,
                "environment": resolve_environment(tags, path),
                "owner_team": tags.get("Owner", "unassigned"),
                "tags": tags,
                "discovered_at": timestamp(),
            }
        )
    return accounts


def handler(event, context):
    store = get_store()
    if os.environ.get("DEPLOYMENT_MODE") == "standalone":
        caller_id = client("sts").get_caller_identity()["Account"]
        if caller_id != os.environ["STANDALONE_ACCOUNT_ID"]:
            raise ValueError("Standalone discovery must run in the configured test account")
        environment = os.environ.get("STANDALONE_ENVIRONMENT", "dev")
        if environment not in {"dev", "testing", "production"}:
            raise ValueError("Invalid standalone governance profile")
        all_accounts = [
            {
                "id": caller_id,
                "name": os.environ.get("STANDALONE_ACCOUNT_NAME", "Test account"),
                "environment": environment,
                "owner_team": os.environ.get("STANDALONE_OWNER_TEAM", "platform"),
                "ou_path": "Standalone/" + environment.title(),
                "tags": {},
                "discovery_source": "standalone",
                "discovered_at": timestamp(),
            }
        ]
    else:
        all_accounts = discover(client("organizations"), json.loads(os.environ.get("TARGET_OU_IDS", "[]")))
    current = {a["id"] for a in all_accounts}
    # Closed/moved-out accounts retain metadata for 30 days and disappear from dashboard scope immediately.
    for previous in store.all("accounts"):
        if previous["id"] not in current:
            closed_at = previous.get("closed_at", timestamp())
            ttl = int((parse_time(closed_at) + timedelta(days=30)).timestamp())
            store.close_account(previous["id"], {**previous, "closed_at": closed_at, "ttl": ttl})
    selected = []
    scan_id = event.get("scan_id", str(uuid4()))
    for account in all_accounts:
        previous = store.get("accounts", account["id"]) or {}
        if not previous:
            unknown, result = evaluate(account, [])
            previous = {**result, **summaries(unknown)}
        account = {**previous, **account}
        account.pop("closed_at", None)
        account.pop("ttl", None)
        store.put("accounts", account["id"], account)
        if event.get("account_id") and account["id"] != event["account_id"]:
            continue
        if event.get("environment") and account["environment"] != event["environment"]:
            continue
        selected.append(
            {
                "account": account,
                "scan_id": scan_id,
                "retried": event.get("retried", False),
                "snapshot": not (event.get("account_id") or event.get("environment")),
            }
        )
    bucket = os.environ["HISTORY_BUCKET"]
    key = f"scan-input/{scan_id}.json"
    client("s3").put_object(
        Bucket=bucket, Key=key, Body=json.dumps(selected).encode(), ContentType="application/json"
    )
    return {"bucket": bucket, "key": key, "scan_id": scan_id, "accounts_total": len(selected)}
