"""AWS-reported standards and control associations, without guessing CIS control numbers."""

from hashlib import sha256
import time
from portal.aws import client, pages

_cache = {}


def standard_id(value):
    value = value[value.index("standards/") :] if "standards/" in value else value
    return "aws-" + sha256(value.encode()).hexdigest()[:16]


def available_standards(region=None):
    if region in _cache and time.monotonic() - _cache[region][0] < 300:
        return _cache[region][1]
    hub = client("securityhub", **({"region_name": region} if region else {}))
    enabled = {r["StandardsArn"] for r in pages(hub, "get_enabled_standards", "StandardsSubscriptions")}
    rows = [
        {
            "id": standard_id(s["StandardsArn"]),
            "name": s["Name"],
            "edition": s["StandardsArn"].split("/v/")[-1],
            "arn": s["StandardsArn"],
            "source": "securityhub",
            "enabled_in_central_account": s["StandardsArn"] in enabled,
            "scope": "AWS Security Hub control evidence. Account scope comes from actual finding associations; control IDs are AWS unified security control IDs.",
        }
        for s in pages(hub, "describe_standards", "Standards")
    ]
    _cache[region] = time.monotonic(), rows
    return rows
