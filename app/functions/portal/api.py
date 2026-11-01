import base64
import json
import logging
from portal.auth import AccessDenied, from_event
from portal.service import NotFound, route
from portal.store import get_store

logger = logging.getLogger(__name__)


def handler(event, context):
    headers = {
        "content-type": "application/json",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
    }
    try:
        principal = from_event(event)
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        if len(raw) > 32768:
            raise ValueError("Request body too large")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("Request body must be an object")
        status, payload = route(
            get_store(),
            principal,
            event["requestContext"]["http"]["method"],
            event["rawPath"],
            event.get("queryStringParameters"),
            body,
        )
    except AccessDenied as exc:
        status, payload = 403, {"error": str(exc)}
    except NotFound as exc:
        status, payload = 404, {"error": str(exc)}
    except (ValueError, KeyError, TypeError) as exc:
        status, payload = 400, {"error": str(exc)}
    except Exception:
        logger.exception("API failure", extra={"request_id": getattr(context, "aws_request_id", "unknown")})
        status, payload = (
            500,
            {
                "error": "Request failed. Contact the platform team with the request ID.",
                "request_id": getattr(context, "aws_request_id", "unknown"),
            },
        )
    return {"statusCode": status, "headers": headers, "body": json.dumps(payload)}
