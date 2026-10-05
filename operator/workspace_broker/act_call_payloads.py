"""Every act whose payload rides on the withheld CALL, in one table.

Two acts have that shape today: the calendar deletion (``act_event_set``,
DESTRUCTIVE) and the vendor's records order (``act_records_order``,
COMMITMENT). ``establishment_acts`` proposes and commits both the same way,
through the same row, tag, TTL and ledger types; what differs per act is only
how its payload is validated, how its line is rendered, which exposure class a
seat must author at ``confirm``, and which bounded counts the ledger keeps.
This table holds exactly those four things, so a third act is one entry here
rather than another ``if tool ==`` in the lifecycle.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import act_event_set, act_records_order
from .establishment_validation import EstablishmentValidationError, _hash_text


@dataclass(frozen=True)
class CallPayloadAct:
    exposure_class: str
    require: Callable[[Any], dict[str, Any]]
    readback: Callable[[dict[str, Any]], str]
    proposed_metadata: Callable[[dict[str, Any]], dict[str, Any]]
    committed_metadata: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _event_count(payload: dict[str, Any]) -> dict[str, Any]:
    return {"event_count": len(payload.get("events") or [])}


ACTS: dict[str, CallPayloadAct] = {
    act_event_set.DELETE_EVENTS_TOOL: CallPayloadAct(
        exposure_class=act_event_set.CALL_PAYLOAD_ACTS[act_event_set.DELETE_EVENTS_TOOL],
        require=act_event_set.require_event_set,
        readback=act_event_set.event_set_readback,
        proposed_metadata=_event_count,
        committed_metadata=lambda payload, outcome: {
            **_event_count(payload),
            "outcome_counts": act_event_set.outcome_counts(outcome),
        },
    ),
    act_records_order.PLACE_ORDER_TOOL: CallPayloadAct(
        exposure_class=act_records_order.CALL_PAYLOAD_ACTS[act_records_order.PLACE_ORDER_TOOL],
        require=act_records_order.require_order,
        readback=act_records_order.order_readback,
        proposed_metadata=act_records_order.proposed_metadata,
        committed_metadata=act_records_order.committed_metadata,
    ),
}

#: Tool -> the exposure class it is withheld under (the union of both modules).
CALL_PAYLOAD_ACTS: dict[str, str] = {tool: act.exposure_class for tool, act in ACTS.items()}


def require_exposure(data: dict[str, Any], tool: str) -> None:
    """Refuse unless SOME persona authors ``<class>: confirm`` for this act."""
    klass = ACTS[tool].exposure_class
    for persona in data.get("personas") or []:
        exposure = ((persona or {}).get("entitlements") or {}).get("exposure") if isinstance(persona, dict) else None
        if isinstance(exposure, dict) and exposure.get(klass) == "confirm":
            return
    raise EstablishmentValidationError(
        f"this seat authors no persona with exposure.{klass} set to 'confirm', so {tool} "
        "cannot be proposed to anybody. Authoring it is a config change the firm agrees to, "
        "not something this turn can do"
    )


def payload_digest(payload: dict[str, Any]) -> str:
    return _hash_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = ["ACTS", "CALL_PAYLOAD_ACTS", "CallPayloadAct", "payload_digest", "require_exposure"]
