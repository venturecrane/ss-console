"""The Smokeball connector's half of the SDK resolution gate.

The SDK (``operator_connector_sdk.resolution``) owns the door: it pops a gated
tool's ``matter_resolution``, reads the target matter, and decides. It knows
nothing about Smokeball, so this module hands it the four things only this
connector has:

* the token store: ``resolution_token.claim`` / ``restore``, and
  ``ResolutionRefused`` (whose ``code`` is the reason);
* the mode, read fresh off the seat's customer.yaml on every call
  (``resolution_mode``);
* where an observe-mode refusal is recorded: a ``would_refuse`` row on the
  seat's audit ledger, through the same broker verb every Smokeball write is
  recorded with (``write_record.record_resolution_observation``);
* the sentence for a call that named no matter.

Installed once, from ``attachment_tools.register``, the registrar ``server.py``
already calls. The gate is read at call time, so the tools registered in
``server.py`` before it are covered exactly like the ones after.
"""

from __future__ import annotations

from typing import Any

from operator_connector_sdk.resolution import ResolutionGate

from . import resolution_token
from .resolution_mode import load_resolution_mode
from .write_record import record_resolution_observation


def _mode() -> str:
    return load_resolution_mode().mode


def _missing_matter(argument: str) -> str:
    return (
        f"no {argument} was given, so no matter resolution can open this write; call "
        f"{resolution_token.RESOLVERS} and pass the matter id and matter_resolution it returned "
        "(nothing was created)"
    )


def _restore(held: object) -> None:
    if isinstance(held, resolution_token.Claim):
        resolution_token.restore(held)


def gate() -> ResolutionGate:
    """The gate over this process's token store."""
    return ResolutionGate(
        claim=resolution_token.claim,
        restore=_restore,
        refusal=resolution_token.ResolutionRefused,
        mode=_mode,
        observe=record_resolution_observation,
        missing_matter=_missing_matter,
    )


def install(server: Any) -> None:
    """Install the gate on the connector's server."""
    server.install_resolution_gate(gate())


__all__ = ["gate", "install"]
