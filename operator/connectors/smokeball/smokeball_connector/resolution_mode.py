"""Whether the matter-resolution gate records or refuses, read off the seat's own
customer.yaml on every gated call.

    connectors:
      PracticeManagement:
        backend: mcp:smokeball
        matter_resolution_mode: observe   # or enforce

WHERE IT LIVES AND WHY. On the Smokeball connector's own entry, because it is a
property of this connector's write surface and of nothing else, and the
bootstrap validator already checks that entry per backend (it refuses the key
on any other backend, and any value but the two). The config applier treats
every ``connectors`` path as rebuild-class, so a change to it reaches a seat
with the image release that carries it: the switch to ``enforce`` is a
deliberate release, behind the busy gate, never a live edit.

THE DEFAULT. Absent (no file, no Smokeball connector, no key) is ``observe`` for
the release that introduces the gate: every call it would refuse is recorded
and dispatched, so a daily cycle shows every lane it would have stopped before
any lane is stopped. The ADR that records the gate names the release that flips
the default.

A PRESENT VALUE THAT IS NOT ONE OF THE TWO is ``enforce``, not the default. The
validator refuses it at boot, so it is reachable only by a hand edit on the
volume, and an unreadable switch must not hold the door open. Two Smokeball
connectors that disagree are ``enforce`` for the same reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from operator_connector_sdk.resolution import MODE_ENFORCE, MODE_OBSERVE, MODES

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML
from . import seat_config

MODE_KEY = "matter_resolution_mode"
BACKEND = "mcp:smokeball"
DEFAULT_MODE = MODE_OBSERVE


@dataclass(frozen=True)
class ResolutionMode:
    mode: str
    error: str | None = None


def parse_resolution_mode(data: object) -> ResolutionMode:
    """The mode a parsed customer.yaml authors for its Smokeball connector."""
    connectors = data.get("connectors") if isinstance(data, dict) else None
    if not isinstance(connectors, dict):
        return ResolutionMode(DEFAULT_MODE)
    authored = [
        conn.get(MODE_KEY)
        for conn in connectors.values()
        if isinstance(conn, dict) and str(conn.get("backend") or "") == BACKEND and conn.get(MODE_KEY) is not None
    ]
    if not authored:
        return ResolutionMode(DEFAULT_MODE)
    if any(value not in MODES for value in authored):
        return ResolutionMode(MODE_ENFORCE, error=f"{MODE_KEY} must be one of {list(MODES)}")
    if len(set(authored)) > 1:
        return ResolutionMode(MODE_ENFORCE, error=f"two Smokeball connectors author different {MODE_KEY} values")
    return ResolutionMode(authored[0])


def load_resolution_mode(path: str | None = None) -> ResolutionMode:
    """Read fresh on every call (no module-level state): a missing file is the
    default, an unparseable one enforces."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return ResolutionMode(DEFAULT_MODE)
    try:
        data = seat_config.parse(raw) or {}
    except Exception as exc:  # noqa: BLE001 - an unparseable config enforces, never raises into a write
        return ResolutionMode(MODE_ENFORCE, error=f"customer.yaml not parseable: {exc.__class__.__name__}")
    return parse_resolution_mode(data)


__all__ = ["BACKEND", "DEFAULT_MODE", "MODE_KEY", "ResolutionMode", "load_resolution_mode", "parse_resolution_mode"]
