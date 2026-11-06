"""The helper that turns a guard's missing tool into a failure (bin/tests/guard_needs.py).

Ordered by which regression would bring the silent pass back:

1. A missing need with no switch must FAIL, and the message must carry the
   command that provides it. A skip here is the 2026-11-06 incident again.
2. No workflow may set the switch, or CI could skip every guard at once.
3. No workflow may set a baseline-regenerate variable: under one, a ratchet
   rewrites its baseline and skips, absorbing whatever grew.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bin.tests import guard_needs as gn

WORKFLOWS = gn.REPO / ".github" / "workflows"


def test_a_missing_need_fails_with_the_command_that_provides_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gn.SWITCH, raising=False)
    with pytest.raises(pytest.fail.Exception) as raised:
        gn.require(None, "ruff is not on PATH", gn.RUFF_INSTALL)
    message = str(raised.value)
    assert "ruff is not on PATH" in message
    assert "python3 -m pip install ruff==0.15.17" in message
    assert "\n" not in message


def test_the_switch_turns_the_failure_into_a_skip_that_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(gn.SWITCH, "1")
    with pytest.raises(pytest.skip.Exception) as raised:
        gn.require(None, "ruff is not on PATH", gn.RUFF_INSTALL)
    assert f"{gn.SWITCH}=1" in str(raised.value)


def test_any_other_switch_value_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(gn.SWITCH, "true")
    with pytest.raises(pytest.fail.Exception):
        gn.require(None, "ruff is not on PATH", gn.RUFF_INSTALL)


def test_a_present_need_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gn.SWITCH, raising=False)
    assert gn.require("/usr/bin/ruff", "ruff is not on PATH", gn.RUFF_INSTALL) is None


def test_a_missing_committed_path_names_the_restore_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gn.SWITCH, raising=False)
    gone = gn.REPO / "operator" / "no-such-corpus"
    with pytest.raises(pytest.fail.Exception) as raised:
        gn.require_path(gone)
    assert "git checkout origin/main -- operator/no-such-corpus" in str(raised.value)


def _workflow_text() -> dict[Path, str]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, f"no workflows found under {WORKFLOWS}"
    return {f: f.read_text(encoding="utf-8") for f in files}


def test_no_workflow_sets_the_skip_switch() -> None:
    setting = [f.name for f, text in _workflow_text().items() if gn.SWITCH in text]
    assert not setting, f"{gn.SWITCH} appears in {setting}; CI must never skip a guard"


def test_no_workflow_regenerates_a_baseline() -> None:
    pattern = re.compile(r"UPDATE_[A-Z_]*BASELINE")
    setting = [f.name for f, text in _workflow_text().items() if pattern.search(text)]
    assert not setting, f"a baseline-regenerate variable appears in {setting}; a ratchet would rewrite and skip"


def test_the_ruff_hint_matches_the_pin_ci_installs() -> None:
    substrate = (WORKFLOWS / "operator-substrate.yml").read_text(encoding="utf-8")
    pin = gn.RUFF_INSTALL.rsplit(" ", 1)[1]
    assert re.search(rf"pip install [^\n]*{re.escape(pin)}", substrate), f"the substrate job no longer installs {pin}"
