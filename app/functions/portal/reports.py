import json
import os
from urllib.request import Request, urlopen
from portal.aws import client
from portal.domain import timestamp
from portal.service import export_bytes
from portal.store import get_store


def handler(event, context):
    store = get_store()
    if event.get("action") == "weekly":
        settings = store.get("settings", "weekly") or {}
        if not settings.get("enabled"):
            return {"status": "disabled"}
        accounts = [a for a in store.all("accounts") if not a.get("closed_at")]
        summary = {
            "classification": "Confidential",
            "accounts": len(accounts),
            "healthy": sum(a.get("healthy", False) for a in accounts),
            "critical_findings": sum(a.get("critical_count", 0) for a in accounts),
            "at": timestamp(),
        }
        if settings["channel"] == "slack":
            secret_id = os.environ.get("SLACK_SECRET_ARN")
            if not secret_id:
                raise ValueError("Configure SLACK_SECRET_ARN before enabling Slack summaries")
            webhook = client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"]
            if not webhook.startswith("https://hooks.slack.com/services/"):
                raise ValueError("Slack secret must contain a Slack webhook URL")
            request = Request(
                webhook,
                data=json.dumps(
                    {"text": "Weekly governance summary (Confidential)\n" + json.dumps(summary)}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                response.read()
        else:
            client("sns").publish(
                TopicArn=os.environ["SUMMARY_TOPIC_ARN"],
                Subject="Weekly AWS governance summary - Confidential",
                Message=json.dumps(summary, indent=2),
            )
        return summary
    report = store.get("reports", event["report_id"])
    if not report:
        raise ValueError("Report not found")
    try:
        data = export_bytes(store, report["account_ids"], report["format"])
        client("s3").put_object(
            Bucket=os.environ["EXPORT_BUCKET"],
            Key=f"exports/{report['id']}.{report['format']}",
            Body=data,
            ContentType="text/csv" if report["format"] == "csv" else "application/json",
            Metadata={"classification": "Confidential"},
        )
        report.update(status="ready", completed_at=timestamp())
    except Exception:
        report.update(status="failed", completed_at=timestamp())
        raise
    finally:
        store.put("reports", report["id"], report)
    return {"id": report["id"], "status": report["status"]}
