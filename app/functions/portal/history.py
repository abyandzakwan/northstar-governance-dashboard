from hashlib import sha256
import io
import json
import os
from portal.aws import client
from portal.domain import timestamp, utcnow, parse_time


def snapshot(account, findings):
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [
        {
            "account_id": account["id"],
            "owner_team": account["owner_team"],
            "account_score": float(account["score_pct"]),
            "rule_id": f["rule_id"],
            "resource": f["resource"],
            "status": f["status"],
            "severity": f["severity"],
            "evaluated_at": f["evaluated_at"],
            "waiver_id": f.get("waiver_id") or "",
            "frameworks": json.dumps(f["frameworks"]),
        }
        for f in findings
    ]
    output = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), output, compression="snappy")
    key = f"snapshots/dt={utcnow().date().isoformat()}/environment={account['environment']}/{account['id']}.parquet"
    client("s3").put_object(Bucket=os.environ["HISTORY_BUCKET"], Key=key, Body=output.getvalue())
    # Trends read a compact daily account series, independent of resource finding volume.
    compact = io.BytesIO()
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "account_id": account["id"],
                    "owner_team": account["owner_team"],
                    "account_score": float(account["score_pct"]),
                }
            ]
        ),
        compact,
        compression="snappy",
    )
    score_key = f"daily-scores/dt={utcnow().date().isoformat()}/environment={account['environment']}/{account['id']}.parquet"
    client("s3").put_object(Bucket=os.environ["HISTORY_BUCKET"], Key=score_key, Body=compact.getvalue())


def trends(store, principal, accounts, env, start, end):
    if not accounts:
        return 200, {"items": []}
    account_ids = sorted(a["id"] for a in accounts)
    if not all(len(a) == 12 and a.isdigit() for a in account_ids):
        raise ValueError("Invalid account IDs in data store")
    key = sha256(json.dumps([principal.subject, account_ids, env, str(start), str(end)]).encode()).hexdigest()
    cached = store.get("cache", key)
    if cached and cached.get("failed"):
        cached = None
    athena = client("athena")
    if (
        cached
        and cached.get("items") is not None
        and utcnow().timestamp() - parse_time(cached["created_at"]).timestamp() < 3600
    ):
        return 200, {"items": cached["items"]}
    if not cached or cached.get("items") is not None:
        ids_sql = ",".join(f"'{a}'" for a in account_ids)
        env_sql = f" AND environment = '{env}'" if env != "all" else ""
        sql = (
            "SELECT dt, ROUND(AVG(account_score), 1) AS score_pct FROM ("
            "SELECT dt, account_id, MAX(account_score) AS account_score FROM daily_scores "
            f"WHERE dt BETWEEN '{start}' AND '{end}' AND account_id IN ({ids_sql}){env_sql} "
            "GROUP BY dt, account_id) GROUP BY dt ORDER BY dt"
        )
        query = athena.start_query_execution(
            QueryString=sql,
            WorkGroup=os.environ["ATHENA_WORKGROUP"],
            QueryExecutionContext={"Database": os.environ["GLUE_DATABASE"]},
        )["QueryExecutionId"]
        cached = {"query_id": query, "created_at": timestamp(), "ttl": int(utcnow().timestamp()) + 86400}
        store.put("cache", key, cached)
    state = athena.get_query_execution(QueryExecutionId=cached["query_id"])["QueryExecution"]["Status"][
        "State"
    ]
    if state in {"FAILED", "CANCELLED"}:
        store.put("cache", key, {**cached, "items": None, "failed": True})
        raise RuntimeError("Historical query failed; inspect Athena query " + cached["query_id"])
    if state != "SUCCEEDED":
        return 202, {"items": [], "status": "pending", "retry_after": 2}
    rows, token = [], None
    while True:
        response = athena.get_query_results(
            QueryExecutionId=cached["query_id"], **({"NextToken": token} if token else {})
        )
        for row in response["ResultSet"]["Rows"]:
            data = [v.get("VarCharValue", "") for v in row["Data"]]
            if data[0] != "dt":
                rows.append({"date": data[0], "score_pct": float(data[1])})
        token = response.get("NextToken")
        if not token:
            break
    store.put("cache", key, {**cached, "items": rows})
    return 200, {"items": rows}
