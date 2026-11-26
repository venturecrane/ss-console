"""A reply that left a firm person waiting is owed work SMD learns of in the
same step (2026-11-25). Every value is invented.

The incident: an attorney asked for a document, the render tool stopped twice,
and the Operator replied "Let me look into that. I'll reply in this thread."
The REPLY_SENT row was stamped ``could_not: looking_into``, but only the
broker's own verbs wrote ``smd_owed`` rows, so nothing listed the request as
owed and nobody acted for 19 hours. Driven here through the reply plugin's real
send path and one real broker (``Broker.handle``, its tables).
"""

from __future__ import annotations

import sys
from pathlib import Path

from shared import smd_owed as owed_client
from tests.test_reply import (  # noqa: F401 - relay_mod is a fixture
    _draft,
    _record_origin,
    relay_mod,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import smd_owed  # noqa: E402 - after the operator tree is on the path
from workspace_broker.tests import test_readback_bindings as broker_kit  # noqa: E402 - the same

LOOK_INTO = "Thanks Greg.\n\nLet me look into that. I'll reply in this thread."


def _wired(monkeypatch, tmp_path):
    broker = broker_kit._broker(tmp_path)
    monkeypatch.setattr(
        owed_client, "_request", lambda payload: broker_kit._call(broker, **payload)
    )
    return broker


def _open(broker) -> list[dict]:
    return smd_owed.open_items(broker.audit_db_path)["owed"]


def test_a_looking_into_reply_writes_one_owed_item_for_its_request(
    relay_mod,  # noqa: F811 - the imported fixture
    monkeypatch,
    tmp_path,
) -> None:
    """FALSIFIER: drop the owe_reply call in ``_emit_reply_event`` and the
    broker holds no item, which is the 19-hour silence."""
    mod, d1, sent = relay_mod
    broker = _wired(monkeypatch, tmp_path)
    _record_origin(sender="greg@whitfield.example", message_id="msg_in", inbox_id="inbox_x")
    mod.on_post_tool_call(
        tool_name="agentmail:create_draft",
        args=_draft(["greg@whitfield.example"], text=LOOK_INTO),
        session_id="s1",
    )
    assert len(sent) == 1
    (row,) = _open(broker)
    assert (row["kind"], row["request_ref"], row["refusal_code"]) == (
        "reply_not_fulfilled",
        "msg_in",
        "looking_into",
    )
    assert "Greg" not in row["detail"]


def test_an_ordinary_reply_owes_nothing(relay_mod, monkeypatch, tmp_path) -> None:  # noqa: F811 - the imported fixture
    mod, _d1, sent = relay_mod
    broker = _wired(monkeypatch, tmp_path)
    _record_origin(sender="greg@whitfield.example", message_id="msg_in", inbox_id="inbox_x")
    mod.on_post_tool_call(
        tool_name="agentmail:create_draft",
        args=_draft(["greg@whitfield.example"]),
        session_id="s1",
    )
    assert len(sent) == 1 and _open(broker) == []


def test_a_waiting_reply_to_smd_itself_owes_nothing(monkeypatch, tmp_path) -> None:
    """SMD writing in to the seat is not a firm person waiting (the triage's
    own test of SMD's addresses)."""
    broker = _wired(monkeypatch, tmp_path)
    md = {
        "could_not": "looking_into",
        "in_reply_to": "<CAx@mail.example>",
        "recipient": "team@smd.services",
    }
    assert owed_client.owe_reply("REPLY_SENT", md) is False and _open(broker) == []
    assert owed_client.owe_reply("REPLY_SENT", {**md, "recipient": "pat@firm.example"}) is True
    assert [r["kind"] for r in _open(broker)] == ["reply_not_fulfilled"]


def test_a_broker_that_cannot_be_reached_never_breaks_the_send(monkeypatch) -> None:
    def down(_payload):
        raise OSError("socket closed")

    md = {
        "could_not": "cannot_attach",
        "in_reply_to": "<CAy@mail.example>",
        "recipient": "pat@firm.example",
    }
    assert owed_client.owe_reply("REPLY_SENT", md, send=down) is False
    assert owed_client.owe_reply("REPLY_SENT", {"in_reply_to": "<CAy@mail.example>"}) is False
    assert owed_client.owe_reply("REPLY_SENT", None) is False
    # A held reply never went out: nothing is owed for it here.
    assert (
        owed_client.owe_reply("REPLY_HELD", md, send=lambda p: {"ok": True, "smd_told": True})
        is False
    )
