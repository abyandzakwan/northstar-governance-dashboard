import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "portal_config", Path(__file__).parents[1] / "ci/portal_config.py"
)
portal_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal_config)

CONFIG = {"stage": "central", "account_id": "123456789012", "region": "ap-southeast-1"}


def test_actions_configuration_can_be_materialized_without_local_tfvars(tmp_path):
    path = portal_config.write_config(json.dumps(CONFIG), "central", "ap-southeast-1", tmp_path)
    assert json.loads(path.read_text()) == CONFIG
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        json.dumps({**CONFIG, "account_id": "YOUR_ACCOUNT_ID"}),
        json.dumps({**CONFIG, "stage": "prod"}),
        json.dumps({**CONFIG, "region": "us-east-1"}),
    ],
)
def test_missing_or_misdirected_configuration_stops_before_terraform(raw, tmp_path):
    with pytest.raises(ValueError):
        portal_config.write_config(raw, "central", "ap-southeast-1", tmp_path)
    assert not list(tmp_path.iterdir())
