"""The seat-layout migration changed no path the overlay resolves (W2-W4).

Each default below was a string spelled in its module until it was pointed at
the generated ``shared.seat_layout``. ``fixtures/seat_layout_golden.json`` holds
the value each one had BEFORE the change, captured by importing the modules at
the pre-migration commit, and this test asserts every one still resolves to
exactly that string. A yaml edit that moves one of these paths fails here first,
which is the point: a moved seat path is a decision with a seat-side rollout.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import ModuleType

import pytest

import bootstrap.cli
import config_applier.__main__
import establish_intake.__main__
import firm_inputs_applier.applier
import shared.customer_config
import shared.fleet_facts
import shared.oauth_callback
import shared.policy_profile
import shared.review_job
import shared.scheduler_check
import shared.selftest_check
import skills_applier.__main__
import spec_applier.__main__
from tests.conftest import load_plugin

#: Imported by name (never from a string), one per golden module.
MODULES: dict[str, ModuleType] = {
    m.__name__: m
    for m in (
        bootstrap.cli,
        config_applier.__main__,
        establish_intake.__main__,
        firm_inputs_applier.applier,
        shared.customer_config,
        shared.fleet_facts,
        shared.oauth_callback,
        shared.policy_profile,
        shared.review_job,
        shared.scheduler_check,
        shared.selftest_check,
        skills_applier.__main__,
        spec_applier.__main__,
    )
}

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "seat_layout_golden.json").read_text("utf-8")
)

_ATTRS = sorted(k for k in GOLDEN if ":" in k and "(" not in k and not k.startswith("plugins/"))
_PLUGINS = sorted(k for k in GOLDEN if k.startswith("plugins/"))


@pytest.mark.parametrize("key", _ATTRS)
def test_a_module_default_is_the_literal_it_replaced(key: str) -> None:
    module, attr = key.split(":")
    assert str(getattr(MODULES[module], attr)) == GOLDEN[key]


@pytest.mark.parametrize("key", _PLUGINS)
def test_a_plugin_default_is_the_literal_it_replaced(key: str) -> None:
    plugin, attr = key.removeprefix("plugins/").split(":")
    assert getattr(load_plugin(plugin), attr) == GOLDEN[key]


def test_the_config_applier_default_volume_is_the_literal_it_replaced() -> None:
    from config_applier import applier

    default = inspect.signature(applier.apply).parameters["volume_path"].default
    assert str(default) == GOLDEN["config_applier.applier:apply(volume_path=)"]


def test_the_connect_reconcile_launches_the_same_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import oauth_callback

    seen: list[list[str]] = []
    monkeypatch.setattr(oauth_callback.subprocess, "Popen", lambda argv, *a, **k: seen.append(argv))
    oauth_callback._trigger_webhook_reconcile({})
    assert seen == [GOLDEN["shared.oauth_callback:_trigger_webhook_reconcile({}) argv"]]


def test_the_golden_covers_every_migrated_default() -> None:
    # 13 module constants, 2 plugin constants, 1 parameter default, 1 inline default.
    assert len(GOLDEN) == 17
    assert sorted(MODULES) == sorted({k.split(":")[0] for k in _ATTRS})
