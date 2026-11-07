"""The overlay's guard helper (tests/guard_needs.py): a missing need fails, and
skips only under the explicit switch, which names itself."""

from __future__ import annotations

import pytest

from . import guard_needs as gn


def test_a_missing_need_fails_with_how_to_provide_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gn.SWITCH, raising=False)
    with pytest.raises(pytest.fail.Exception) as raised:
        gn.require(None, "Hermes core (tools.registry) is not importable", gn.HERMES_PROVIDE)
    message = str(raised.value)
    assert "HERMES_AGENT_SRC" in message and "HERMES_UPSTREAM_SHA" in message
    assert "\n" not in message


def test_the_switch_skips_and_names_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(gn.SWITCH, "1")
    with pytest.raises(pytest.skip.Exception) as raised:
        gn.require(None, "x is missing", "install x")
    assert f"{gn.SWITCH}=1" in str(raised.value)


def test_a_present_need_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gn.SWITCH, raising=False)
    assert gn.require(object(), "x is missing", "install x") is None


def test_the_switch_name_is_the_substrate_trees() -> None:
    """One switch name across trees, so one grep finds every way to skip a guard."""
    assert gn.SWITCH == "SMD_ALLOW_GUARD_SKIPS"
