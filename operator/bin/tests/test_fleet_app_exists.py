"""``app_exists`` asks the app itself, so it works under CI's app-scoped tokens.

Every CI image build died on the org-scoped ``fly apps list`` (``unauthorized``)
under the app-scoped ``FLY_REGISTRY_TOKEN``.
"""

from __future__ import annotations

import pytest

from bin.lib.fleet.fly import Fly, FlyTokens
from bin.lib.fleet.runner import Result, ToolingError


class _Runner:
    def __init__(self, *results: Result) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        self.calls.append(list(argv))
        return self.results.pop(0)


def _fly(runner: _Runner, scoped: bool) -> Fly:
    env = {"GITHUB_ACTIONS": "true", "FLY_REGISTRY_TOKEN": "r", "FLY_DEPLOY_TOKEN__SEAT": "s"} if scoped else {}
    return Fly(runner, "/repo", FlyTokens(env))  # type: ignore[arg-type]


def test_app_exists_asks_the_app_not_the_org() -> None:
    """FALSIFIER: restore ``fly apps list`` and the argv assertion fails."""
    runner = _Runner(Result(0, "[]", ""))
    assert _fly(runner, scoped=True).app_exists("hermes-fleet") is True
    assert runner.calls[0][:4] == ["fly", "machines", "list", "-a"]


def test_not_found_is_absent_only_under_an_ambient_login() -> None:
    """Under a scoped token "not found" can mean "not yours"; reading it as absent
    would call an existing seat new. FALSIFIER: drop ``not self.tokens.scoped``."""
    not_found = Result(1, "", "Error: failed to list VMs: app not found")
    assert _fly(_Runner(not_found), scoped=False).app_exists("hermes-x") is False
    with pytest.raises(ToolingError):
        _fly(_Runner(not_found), scoped=True).app_exists("hermes-seat")


def test_unauthorized_is_never_an_answer() -> None:
    with pytest.raises(ToolingError, match="unauthorized"):
        _fly(_Runner(Result(1, "", "Error: unauthorized")), scoped=False).app_exists("hermes-x")
