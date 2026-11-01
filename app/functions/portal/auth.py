"""Authorization is based only on API Gateway-verified JWT claims, never request headers."""

import json
import re
import os
from dataclasses import dataclass


class AccessDenied(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    groups: tuple[str, ...]

    @property
    def admin(self):
        return "gov-portal-admins" in self.groups

    @property
    def teams(self):
        return [g.removeprefix("gov-portal-team-") for g in self.groups if g.startswith("gov-portal-team-")]

    def can_read(self, account):
        return self.admin or "gov-portal-viewers" in self.groups or account["owner_team"] in self.teams

    def can_waive(self, account):
        from portal.change_policy import approval_environment

        return self.admin or (
            approval_environment(account) != "production" and account["owner_team"] in self.teams
        )


def parse_groups(value):
    if isinstance(value, list):
        groups = value
    else:
        try:
            groups = json.loads(value)
        except (ValueError, TypeError):
            groups = re.split(r"[,\s]+", str(value).strip("[]"))
    return tuple(
        g.strip().strip('"')
        for g in groups
        if isinstance(g, str)
        and re.fullmatch(r"gov-portal-(admins|viewers|team-[a-zA-Z0-9_-]+)", g.strip().strip('"'))
    )


def from_event(event):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    principal = Principal(claims.get("sub", ""), parse_groups(claims.get("cognito:groups", [])))
    if not principal.subject or not principal.groups:
        raise AccessDenied("An authorized portal group is required")
    return principal


def token_handler(event, context):
    # Map only signed SAML groups. The SPA's OAuth scopes exclude aws.cognito.signin.user.admin,
    # and the pool WAF blocks UpdateUserAttributes, so users cannot rewrite the mapped attribute.
    if os.environ.get("AUTH_MODE", "saml") == "cognito":
        # Native users receive only groups assigned by a Cognito administrator.
        groups = parse_groups(event["request"].get("groupConfiguration", {}).get("groupsToOverride", []))
    else:
        groups = parse_groups(event["request"]["userAttributes"].get("custom:groups", ""))
    event.setdefault("response", {})["claimsOverrideDetails"] = {
        "groupOverrideDetails": {"groupsToOverride": list(groups)}
    }
    return event
