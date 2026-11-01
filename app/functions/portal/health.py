from datetime import timedelta
from portal.aws import client
from portal.domain import parse_time, utcnow
from portal.store import get_store


def handler(event, context):
    store = get_store()
    from portal.service import refresh_evaluation

    for waiver in store.all("waivers"):
        if (
            not waiver.get("expired_at")
            and not waiver.get("revoked_at")
            and parse_time(waiver["expires_at"]) <= utcnow()
        ):
            refresh_evaluation(store, waiver["account_id"])
            waiver["expired_at"] = utcnow().isoformat()
            store.put("waivers", waiver["id"], waiver)
    accounts = [a for a in store.all("accounts") if not a.get("closed_at")]
    stale = sum(
        not a.get("last_scanned_at") or utcnow() - parse_time(a["last_scanned_at"]) > timedelta(hours=26)
        for a in accounts
    )
    client("cloudwatch").put_metric_data(
        Namespace="GovPortal",
        MetricData=[
            {
                "MetricName": "StaleAccountPercent",
                "Value": 100 * stale / len(accounts) if accounts else 0,
                "Unit": "Percent",
            },
            {
                "MetricName": "UnclassifiedAccounts",
                "Value": sum(a["environment"] == "unclassified" for a in accounts),
                "Unit": "Count",
            },
        ],
    )
    return {"accounts": len(accounts), "stale": stale}
