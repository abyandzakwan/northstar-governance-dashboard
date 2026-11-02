from copy import deepcopy
from types import SimpleNamespace

from boto3.dynamodb.table import BatchWriter
from botocore.exceptions import ClientError
import pytest

from portal.store import DynamoStore


class ValidatingBatchClient:
    """Exercise the SDK buffer with DynamoDB's real unique-key constraint."""

    def __init__(self):
        self.rows = {}

    def batch_write_item(self, RequestItems):
        requests = RequestItems["state"]
        keys = [
            (item["PK"], item["SK"])
            for request in requests
            for item in [request.get("PutRequest", {}).get("Item") or request["DeleteRequest"]["Key"]]
        ]
        if len(keys) != len(set(keys)):
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": "Provided list of item keys contains duplicates",
                    }
                },
                "BatchWriteItem",
            )
        for request, key in zip(requests, keys):
            if "PutRequest" in request:
                self.rows[key] = deepcopy(request["PutRequest"]["Item"])
            else:
                self.rows.pop(key, None)
        return {"UnprocessedItems": {}}


@pytest.mark.parametrize("kind", ["findings", "deployments"])
def test_repeated_collection_replaces_retained_items_and_removes_stale_items(kind):
    client = ValidatingBatchClient()
    store = DynamoStore.__new__(DynamoStore)
    store.table = SimpleNamespace(batch_writer=lambda **kwargs: BatchWriter("state", client, **kwargs))
    store._pages = lambda *args, **kwargs: list(client.rows.values())
    if kind == "findings":
        first = [
            {
                "rule_id": "ROOT_MFA_ENABLED",
                "resource": resource,
                "region": "ap-southeast-1",
                "status": "FAIL",
            }
            for resource in ["stale", "retained"]
        ]
    else:
        first = [
            {"name": name, "region": "ap-southeast-1", "status": "OUTDATED"} for name in ["stale", "retained"]
        ]
    store.put(kind, "519012038215", first)
    replacement = [{**first[1], "status": "PASS"}]
    store.put(kind, "519012038215", replacement)
    assert len(client.rows) == 1
    assert next(iter(client.rows.values()))["data"] == replacement[0]
    store.put(kind, "519012038215", replacement)
    assert len(client.rows) == 1
