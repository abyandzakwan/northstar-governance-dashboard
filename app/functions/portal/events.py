import gzip
import json
import os
from uuid import uuid4
from portal.aws import client
from portal.domain import utcnow


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            account_id = message.get("account")
            if not account_id or len(account_id) != 12 or not account_id.isdigit():
                raise ValueError("Event has no valid source account")
            event_id = message.get("id", str(uuid4()))
            client("s3").put_object(
                Bucket=os.environ["HISTORY_BUCKET"],
                Key=f"events/dt={utcnow().date().isoformat()}/{event_id}.json.gz",
                Body=gzip.compress(json.dumps(message).encode()),
                ContentEncoding="gzip",
                ContentType="application/json",
            )
            try:
                client("stepfunctions").start_execution(
                    stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                    name=f"event-{event_id}",
                    input=json.dumps({"account_id": account_id, "scan_id": event_id}),
                )
            except client("stepfunctions").exceptions.ExecutionAlreadyExists:
                pass
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def retry_handler(event, context):
    """Hourly retry: a failed account gets exactly one additional execution, then remains visible in the DLQ."""
    sqs = client("sqs")
    url = os.environ["DLQ_URL"]
    for _ in range(50):
        if context and context.get_remaining_time_in_millis() < 10000:
            break
        messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=900).get(
            "Messages", []
        )
        if not messages:
            break
        for message in messages:
            payload = json.loads(message["Body"])
            if payload.get("retried"):
                continue
            account_id = payload.get("account_id") or payload.get("account", {}).get("id")
            if not account_id:
                continue
            scan_id = "retry-" + message["MessageId"]
            try:
                client("stepfunctions").start_execution(
                    stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                    name=scan_id,
                    input=json.dumps({"account_id": account_id, "scan_id": scan_id, "retried": True}),
                )
            except client("stepfunctions").exceptions.ExecutionAlreadyExists:
                pass
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
