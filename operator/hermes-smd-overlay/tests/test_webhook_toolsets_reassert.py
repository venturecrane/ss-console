"""bootstrap.webhook_toolsets puts the authored webhook surface back after `plugins enable`.

Hermes v0.22.5 promotion (ss-console#3564). ``hermes plugins enable`` appends
the enabled plugin's toolset to every saved ``platform_toolsets`` list
(``hermes_cli/plugins_cmd.py`` ``_toggle_plugin_toolset`` at v0.22.5), and
bootstrap.sh enables the overlay after translate on every boot. These tests
run translate for real, simulate the append, and hold the re-assertion to the
authored list, including a falsifier for each way it can be wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bootstrap import webhook_toolsets
from bootstrap.translate import translate_customer_yaml
from shared.webhook_read_surface import webhook_platform_toolsets
from tests.test_bootstrap_translate import VALID_YAML


def _customer() -> dict:
    """The translate suite's valid customer, given an inbound email route."""
    doc = yaml.safe_load(VALID_YAML)
    doc["personas"][0]["skills"][0]["initiation"]["webhook"] = True
    doc["connectors"]["Email"] = {
        "adapter": "agentmail",
        "backend": "mcp:agentmail",
        "enabled": True,
        "webhook_url": "https://hermes-smd.fly.dev/webhooks/agentmail",
    }
    doc["webhook_triggers"] = [
        {
            "source": "agentmail",
            "event_type": "message.received",
            "skill": "inbox-triage",
            "persona": "marcus",
        }
    ]
    return doc


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("WEBHOOK_SECRET_AGENTMAIL", "shh")
    customer_yaml = tmp_path / "customer.yaml"
    customer_yaml.write_text(yaml.safe_dump(_customer(), sort_keys=False), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    skills = tmp_path / "skills"
    (skills / "inbox-triage").mkdir(parents=True)
    (skills / "inbox-triage" / "SKILL.md").write_text("# inbox-triage skill\n", encoding="utf-8")
    translate_customer_yaml(
        customer_yaml_path=str(customer_yaml), hermes_home=str(home), skills_dir=str(skills)
    )
    return customer_yaml, home / "profiles" / "marcus" / "config.yaml"


def _webhook(config_path: Path):
    return (
        yaml.safe_load(config_path.read_text(encoding="utf-8")).get("platform_toolsets") or {}
    ).get("webhook")


def _append(config_path: Path, toolset: str) -> None:
    # What _toggle_plugin_toolset does to every saved list.
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["platform_toolsets"]["webhook"].append(toolset)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_translate_authors_the_list_the_reassertion_recomputes(seat) -> None:
    customer_yaml, config_path = seat
    authored = webhook_toolsets.authored_webhook_toolsets(yaml.safe_load(customer_yaml.read_text()))
    assert authored == webhook_platform_toolsets()
    assert _webhook(config_path) == authored
    assert webhook_toolsets.reassert(customer_yaml, config_path) == []


def test_a_toolset_plugins_enable_appended_is_removed(seat) -> None:
    # FALSIFIER: without the re-assertion the appended toolset stays on the
    # surface an untrusted inbound email turn is offered.
    customer_yaml, config_path = seat
    before = config_path.read_text(encoding="utf-8")
    _append(config_path, "smd_workspace")
    assert "smd_workspace" in _webhook(config_path)
    assert webhook_toolsets.main(["x", str(customer_yaml), str(config_path)]) == 0
    assert _webhook(config_path) == webhook_platform_toolsets()
    # Nothing else in the file moved.
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == yaml.safe_load(before)


def test_a_removed_authored_toolset_is_restored(seat) -> None:
    customer_yaml, config_path = seat
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["platform_toolsets"]["webhook"] = ["web"]
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert webhook_toolsets.reassert(customer_yaml, config_path) == []
    assert _webhook(config_path) == webhook_platform_toolsets()


def test_a_seat_with_no_webhook_route_must_carry_no_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WEBHOOK_SECRET_AGENTMAIL", raising=False)
    customer = {**_customer(), "webhook_triggers": []}
    customer_yaml = tmp_path / "customer.yaml"
    customer_yaml.write_text(yaml.safe_dump(customer), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"model": "x"}), encoding="utf-8")
    assert webhook_toolsets.reassert(customer_yaml, config_path) == []
    config_path.write_text(
        yaml.safe_dump({"platform_toolsets": {"webhook": ["file"]}}), encoding="utf-8"
    )
    assert webhook_toolsets.main(["x", str(customer_yaml), str(config_path)]) == 1


def test_a_write_that_does_not_land_fails_the_boot(seat, monkeypatch) -> None:
    customer_yaml, config_path = seat
    _append(config_path, "smd_workspace")
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: len(data))
    problems = webhook_toolsets.reassert(customer_yaml, config_path)
    assert problems and "after the rewrite" in problems[0]
