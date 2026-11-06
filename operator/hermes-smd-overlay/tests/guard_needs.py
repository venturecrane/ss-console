"""A guard that cannot run fails, and says how to make it run (overlay tree).

The same rule as operator/bin/tests/guard_needs.py, kept as this tree's own few
lines because the overlay is a separate test root with its own environment. A
skip is reported as a non-failure, so a guard that skips certifies something
it never looked at. When a guard's need is missing it FAILS with one line
naming the need and how to provide it; it skips only under the explicit
SMD_ALLOW_GUARD_SKIPS=1 switch, and the reason names that switch.
"""

from __future__ import annotations

import os
from types import ModuleType

import pytest

SWITCH = "SMD_ALLOW_GUARD_SKIPS"

#: How the overlay job provides the pinned Hermes source the real-registry
#: contracts import.
HERMES_PROVIDE = (
    "check out NousResearch/hermes-agent at ARG HERMES_UPSTREAM_SHA in operator/templates/Dockerfile "
    "and set HERMES_AGENT_SRC to it (the overlay job in .github/workflows/operator-substrate.yml does this)"
)


def require(present: object, missing: str, provide: str) -> None:
    """Fail (or, under the switch, skip) unless `present` is truthy."""
    if present:
        return
    if os.environ.get(SWITCH) == "1":
        pytest.skip(f"{missing}: skipped because {SWITCH}=1 is set")
    pytest.fail(
        f"{missing}; provide it: {provide} (or set {SWITCH}=1 to skip on purpose)", pytrace=False
    )


def hermes_tools_registry(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The pinned Hermes core's tools.registry, from HERMES_AGENT_SRC when set."""
    src = os.environ.get("HERMES_AGENT_SRC")
    if src:
        monkeypatch.syspath_prepend(src)
    try:
        from tools import registry as module
    except ImportError:
        module = None
    require(module, "Hermes core (tools.registry) is not importable", HERMES_PROVIDE)
    assert module is not None
    return module
