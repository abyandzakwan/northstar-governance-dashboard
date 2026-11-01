"""Shared SDK clients with bounded adaptive retries."""

import boto3
from botocore.config import Config

CONFIG = Config(retries={"mode": "adaptive", "max_attempts": 10}, connect_timeout=5, read_timeout=30)


def client(name, **kwargs):
    return boto3.client(name, config=CONFIG, **kwargs)


def resource(name, **kwargs):
    return boto3.resource(name, config=CONFIG, **kwargs)


def pages(service, operation, result_key, **kwargs):
    if service.can_paginate(operation):
        for page in service.get_paginator(operation).paginate(**kwargs):
            yield from page.get(result_key, [])
    else:
        while True:
            page = getattr(service, operation)(**kwargs)
            yield from page.get(result_key, [])
            token = page.get("NextToken") or page.get("nextToken")
            if not token:
                return
            kwargs["nextToken" if "nextToken" in page else "NextToken"] = token
