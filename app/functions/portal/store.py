"""Small storage interface with a persistent local implementation and a DynamoDB adapter."""

import json
import os
from hashlib import sha256
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from threading import RLock


class LocalStore:
    def __init__(self, path=None, seed=None):
        self.path = Path(path) if path else None
        self.lock = RLock()
        self.data = (
            json.loads(self.path.read_text()) if self.path and self.path.exists() else deepcopy(seed or {})
        )

    def all(self, kind):
        with self.lock:
            return deepcopy(list(self.data.get(kind, {}).values()))

    def get(self, kind, key):
        with self.lock:
            return deepcopy(self.data.get(kind, {}).get(key))

    def put(self, kind, key, value):
        with self.lock:
            self.data.setdefault(kind, {})[key] = deepcopy(value)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.data))
                temporary.replace(self.path)

    def compare_put(self, kind, key, value, revision=None):
        with self.lock:
            current = self.get(kind, key)
            if (current is None) != (revision is None) or (current and current.get("revision") != revision):
                return False
            self.put(kind, key, value)
            return True

    def acquire_lock(self, key, owner, expires):
        import time

        with self.lock:
            current = self.get("locks", key)
            if current and current["expires"] > time.time():
                return False
            self.put("locks", key, {"owner": owner, "expires": expires})
            return True

    def release_lock(self, key, owner):
        with self.lock:
            if (self.get("locks", key) or {}).get("owner") == owner:
                self.put("locks", key, {"owner": owner, "expires": 0})

    def findings(self, account_id):
        return self.get("findings", account_id) or []

    def close_account(self, account_id, metadata):
        self.put("accounts", account_id, metadata)

    def deployments(self, account_id):
        return self.get("deployments", account_id) or []


def clean(value):
    return json.loads(json.dumps(value, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)))


class DynamoStore:
    def __init__(self):
        from portal.aws import client, resource

        self.table = resource("dynamodb").Table(os.environ["TABLE_NAME"])
        self.s3 = client("s3")

    def _pages(self, method, **kwargs):
        items = []
        while True:
            page = getattr(self.table, method)(**kwargs)
            items.extend(page.get("Items", []))
            if not page.get("LastEvaluatedKey"):
                return clean(items)
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def all(self, kind):
        from boto3.dynamodb.conditions import Attr

        return [r["data"] for r in self._pages("scan", FilterExpression=Attr("kind").eq(kind))]

    def get(self, kind, key):
        return clean(self.table.get_item(Key=self.key(kind, key), ConsistentRead=True).get("Item", {})).get(
            "data"
        )

    @staticmethod
    def key(kind, key):
        if kind in {"accounts", "scores"}:
            return {"PK": f"ACCOUNT#{key}", "SK": "META" if kind == "accounts" else "SCORE"}
        return {"PK": f"{kind.upper()}#{key}", "SK": "META"}

    def put(self, kind, key, value):
        if kind in {"findings", "deployments"}:
            from boto3.dynamodb.conditions import Key

            prefix = "RULE#" if kind == "findings" else "DEPLOY#"
            old = self._pages(
                "query", KeyConditionExpression=Key("PK").eq(f"ACCOUNT#{key}") & Key("SK").begins_with(prefix)
            )
            with self.table.batch_writer() as batch:
                for item in old:
                    batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
                for item in value:
                    identifier = (
                        f"{item['rule_id']}#{sha256((item['resource'] + item.get('region', '')).encode()).hexdigest()}"
                        if kind == "findings"
                        else item["name"] + "#" + item.get("region", "")
                    )
                    row = {"PK": f"ACCOUNT#{key}", "SK": prefix + identifier, "kind": kind, "data": item}
                    if kind == "findings":
                        row.update(GSI2PK=f"RULE#{item['rule_id']}", GSI2SK=f"STATUS#{item['status']}#{key}")
                    batch.put_item(Item=json.loads(json.dumps(row), parse_float=Decimal))
            return
        row = {**self.key(kind, key), "kind": kind, "data": value}
        if kind == "accounts":
            row.update(
                GSI1PK=f"ENV#{value['environment']}",
                GSI1SK=f"SCORE#{value.get('score_pct', 0):05.1f}#{key}",
                GSI3PK=f"TEAM#{value['owner_team']}",
                GSI3SK=f"ACCOUNT#{key}",
            )
        if value.get("ttl"):
            row["ttl"] = value["ttl"]
        self.table.put_item(Item=json.loads(json.dumps(row), parse_float=Decimal))

    def compare_put(self, kind, key, value, revision=None):
        from botocore.exceptions import ClientError

        row = {**self.key(kind, key), "kind": kind, "data": value}
        options = (
            {"ConditionExpression": "attribute_not_exists(PK)"}
            if revision is None
            else {
                "ConditionExpression": "#data.#revision = :revision",
                "ExpressionAttributeNames": {"#data": "data", "#revision": "revision"},
                "ExpressionAttributeValues": {":revision": revision},
            }
        )
        try:
            self.table.put_item(Item=json.loads(json.dumps(row), parse_float=Decimal), **options)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return False

    def acquire_lock(self, key, owner, expires):
        import time
        from botocore.exceptions import ClientError

        try:
            self.table.put_item(
                Item={
                    **self.key("locks", key),
                    "kind": "locks",
                    "ttl": expires,
                    "data": {"owner": owner, "expires": expires},
                },
                ConditionExpression="attribute_not_exists(PK) OR #d.#e <= :now",
                ExpressionAttributeNames={"#d": "data", "#e": "expires"},
                ExpressionAttributeValues={":now": int(time.time())},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return False

    def release_lock(self, key, owner):
        from botocore.exceptions import ClientError

        try:
            self.table.delete_item(
                Key=self.key("locks", key),
                ConditionExpression="#d.#o = :owner",
                ExpressionAttributeNames={"#d": "data", "#o": "owner"},
                ExpressionAttributeValues={":owner": owner},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def findings(self, account_id):
        return self._account_items(account_id, "RULE#")

    def close_account(self, account_id, metadata):
        from boto3.dynamodb.conditions import Key

        items = self._pages("query", KeyConditionExpression=Key("PK").eq(f"ACCOUNT#{account_id}"))
        with self.table.batch_writer() as batch:
            for row in items:
                row["ttl"] = metadata["ttl"]
                batch.put_item(Item=json.loads(json.dumps(row), parse_float=Decimal))
        self.put("accounts", account_id, metadata)

    def deployments(self, account_id):
        return self._account_items(account_id, "DEPLOY#")

    def _account_items(self, account_id, prefix):
        from boto3.dynamodb.conditions import Key

        return [
            r["data"]
            for r in self._pages(
                "query",
                ConsistentRead=True,
                KeyConditionExpression=Key("PK").eq(f"ACCOUNT#{account_id}") & Key("SK").begins_with(prefix),
            )
        ]


def get_store():
    if os.environ.get("PORTAL_MODE") == "demo":
        from portal.demo import seed

        return LocalStore(os.environ.get("LOCAL_STATE", ".local/state.json"), seed())
    return DynamoStore()
