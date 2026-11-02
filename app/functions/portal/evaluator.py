import json
from portal.aws import client
from portal.domain import evaluate, summaries, timestamp
from portal.store import get_store


def handler(event, context):
    payload = json.loads(client("s3").get_object(Bucket=event["bucket"], Key=event["key"])["Body"].read())
    store = get_store()
    account = payload["account"]
    # Use current ownership metadata if discovery or tags changed during a long scan.
    account = {**account, **(store.get("accounts", account["id"]) or {})}
    findings, result = evaluate(account, payload["observations"], store.all("waivers"))
    store.put("findings", account["id"], findings)
    store.put("deployments", account["id"], payload["deployments"])
    store.put("scores", account["id"], result)
    account.update(
        **result,
        **summaries(findings),
        deployment_summary=sorted(
            payload["deployments"],
            key=lambda d: (not d.get("is_baseline", False), d["drift_status"] != "DRIFTED", d["name"]),
        )[:50],
        deployment_drift_count=sum(d["drift_status"] == "DRIFTED" for d in payload["deployments"]),
        last_scanned_at=timestamp(),
        collection_errors=payload.get("collection_errors", []),
        sample_data=any(f.get("sample_data") for f in findings),
    )
    store.put("accounts", account["id"], account)
    if payload.get("snapshot"):
        from portal.history import snapshot

        snapshot(account, findings)
    client("cloudwatch").put_metric_data(
        Namespace="GovPortal",
        MetricData=[
            {"MetricName": "AccountsScanned", "Value": 1, "Unit": "Count"},
            {
                "MetricName": "UnclassifiedAccounts",
                "Value": int(account["environment"] == "unclassified"),
                "Unit": "Count",
            },
        ],
    )
    return {"account_id": account["id"], "scan_id": payload["scan_id"], **result}
