"""Materialize the non-secret portal inputs supplied through GitHub Actions variables."""

import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def write_config(raw, stage, region, directory):
    if stage not in {"central", "dev", "testing", "prod"}:
        raise ValueError("Invalid deployment stage")
    if not raw:
        raise ValueError("Set the PORTAL_TFVARS_JSON Actions variable before enabling AWS deployment")
    config = json.loads(raw)
    if not isinstance(config, dict):
        raise ValueError("PORTAL_TFVARS_JSON must be a JSON object")
    if config.get("stage") != stage:
        raise ValueError("Portal configuration stage must match the deployment stage")
    if not re.fullmatch(r"[0-9]{12}", str(config.get("account_id", ""))):
        raise ValueError("Set account_id to the testing account's 12-digit AWS account ID")
    if not region or config.get("region") != region:
        raise ValueError("Portal configuration region must match AWS_REGION")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stage}.tfvars.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    path.chmod(0o600)
    return path


if __name__ == "__main__":
    write_config(
        os.environ.get("PORTAL_TFVARS_JSON", ""),
        os.environ.get("STAGE", "central"),
        os.environ.get("AWS_REGION", ""),
        ROOT / "infra/envs",
    )
