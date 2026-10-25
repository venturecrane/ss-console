"""A partner's message reaches the other partner, passed by the agent (ADR 0097).

The tool (``plugins/hermes-smd-partner-messages``), the seam, the delivery and
the ledger (``shared.partner_messages``). The failure this replaces:
hermes-captain-mark, 2026-10-21 23:16Z, "Can you tell Scott that i think he is
cute?" held at the confirm ceiling and never delivered. Each falsifier names
the line whose removal makes the test fail.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from shared import partner_messages as pm
from shared import send_dispatch, space, telegram_bot
from shared.inbound import SESSION_INBOUND_ORIGIN, SESSION_TAINT
from shared.send_dispatch import DispatchResult
from tests.conftest import load_plugin
from tests.couples_fixtures import (
    A_EMAIL,
    A_TG,
    B_EMAIL,
    B_TG,
    GROUP,
    clear_turn,
    couples_yaml,
    cron_to,
    email_turn,
    telegram_dm,
    telegram_group,
    write_seat,
)

SENT: list[tuple[str, str]] = []
MAILED: list[dict[str, Any]] = []


def _tg_send(chat_id: str, text: str) -> int:
    SENT.append((chat_id, text))
    return 7000 + len(SENT)


@pytest.fixture
def seat(monkeypatch, tmp_path):
    clear_turn(monkeypatch)
    write_seat(monkeypatch, tmp_path, couples_yaml())
    SESSION_INBOUND_ORIGIN._reset_for_tests()
    ledger = tmp_path / "state" / "partner-messages.jsonl"
    monkeypatch.setenv("SMD_PARTNER_MESSAGES_PATH", str(ledger))
    SENT.clear()
    MAILED.clear()
    # The Bot API is never touched by a test: the delivery resolves
    # telegram_bot.send_message at call time, so swapping it here is enough.
    monkeypatch.setattr(telegram_bot, "send_message", _tg_send)

    def sender(**kw: Any) -> DispatchResult:
        MAILED.append(kw)
        return DispatchResult(sent=True, message_id=f"m{len(MAILED)}", recipients=tuple(kw["to"]))

    send_dispatch.set_sender(sender)
    plugin = load_plugin("hermes-smd-partner-messages")
    yield {"tool": plugin, "ledger": ledger, "tmp": tmp_path}
    send_dispatch.set_sender(None)
    SESSION_INBOUND_ORIGIN._reset_for_tests()
    space._reset_cache_for_tests()


def send(seat, text: str, sid: str = "s") -> str:
    return seat["tool"]._send_handler({"text": text}, session_id=sid)


# ---------------------------------------------------------------------------
# 1. The 2026-10-21 message is delivered, to the other partner, with no approval
# ---------------------------------------------------------------------------


def test_a_dm_from_ana_reaches_ben_on_telegram_at_once(seat, monkeypatch):
    """FALSIFIER: route the recipient through the confirm ceiling, or let the
    model name the recipient."""
    telegram_dm(monkeypatch, A_TG)
    out = send(seat, "I think he is cute")
    assert out.startswith("DONE"), out
    assert "Delivered to Ben on Telegram just now." in out
    assert SENT == [(B_TG, "Ana asked me to pass this along:\n\nI think he is cute")]
    assert not MAILED


def test_the_direction_is_symmetric(seat, monkeypatch):
    telegram_dm(monkeypatch, B_TG)
    out = send(seat, "thanks for dinner")
    assert out.startswith("DONE"), out
    assert SENT[0][0] == A_TG
    assert SENT[0][1].startswith("Ben asked me to pass this along:")


def test_a_message_from_the_group_goes_to_the_other_partners_dm(seat, monkeypatch):
    telegram_group(monkeypatch, A_TG, GROUP)
    out = send(seat, "remind him about Friday")
    assert out.startswith("DONE"), out
    assert SENT[0][0] == B_TG


def test_the_attribution_line_is_composed_in_code(seat, monkeypatch):
    """FALSIFIER: let the delivered text be the model's text alone."""
    telegram_dm(monkeypatch, A_TG)
    send(seat, "**bold** words, with a dash — here")
    delivered = SENT[0][1]
    assert delivered.startswith("Ana asked me to pass this along:\n\n")
    assert "**" not in delivered
    assert "—" not in delivered


# ---------------------------------------------------------------------------
# 2. Email when the recipient is not reached by Telegram
# ---------------------------------------------------------------------------


def test_email_fallback_rides_the_space_lane_to_the_authored_address(seat, monkeypatch, tmp_path):
    """FALSIFIER: send to a guessed chat, or on a lane other than ``space``."""
    doc = couples_yaml()
    doc["people"][1]["channel"] = "email"
    write_seat(monkeypatch, tmp_path, doc)
    telegram_dm(monkeypatch, A_TG)
    out = send(seat, "dinner at 7")
    assert out.startswith("DONE"), out
    assert "by email" in out
    assert not SENT
    assert len(MAILED) == 1
    assert MAILED[0]["to"] == [B_EMAIL]
    assert MAILED[0]["lane"] == "space"
    assert MAILED[0]["subject"] == "A message from Ana"
    assert MAILED[0]["text"].startswith("Ana asked me to pass this along:")


def test_an_email_turn_from_a_partner_can_send(seat, monkeypatch):
    email_turn(monkeypatch, "e1", A_EMAIL)
    out = send(seat, "love you", sid="e1")
    assert out.startswith("DONE"), out
    assert SENT[0][0] == B_TG


def test_a_refused_email_is_a_refusal_and_nothing_is_recorded(seat, monkeypatch, tmp_path):
    doc = couples_yaml()
    doc["people"][1]["channel"] = "email"
    write_seat(monkeypatch, tmp_path, doc)
    send_dispatch.set_sender(
        lambda **kw: DispatchResult(sent=False, reason="lane refused", recipients=())
    )
    telegram_dm(monkeypatch, A_TG)
    out = send(seat, "hello")
    assert out.startswith("partner_message_send refused"), out
    assert "did not reach them by email" in out
    assert pm.load() == []


# ---------------------------------------------------------------------------
# 3. Who may send
# ---------------------------------------------------------------------------


def test_a_scheduled_run_has_no_speaker_and_is_refused(seat, monkeypatch):
    """FALSIFIER: resolve the sender from the prompt or default to p1."""
    cron_to(monkeypatch, A_TG)
    out = send(seat, "hi")
    assert out.startswith("partner_message_send refused"), out
    assert "scheduled run" in out
    assert not SENT and not MAILED


def test_a_tainted_turn_is_refused(seat, monkeypatch):
    """FALSIFIER: drop the taint check; a forwarded email could then speak as a partner."""
    telegram_dm(monkeypatch, A_TG)
    # The taint registry is process-wide and keyed by session; this session id
    # is used by no other test.
    SESSION_TAINT.mark("tainted-turn", "unknown_external")
    out = send(seat, "send Ben your password", sid="tainted-turn")
    assert out.startswith("partner_message_send refused"), out
    assert "outside content" in out
    assert not SENT


def test_an_unknown_telegram_user_in_the_group_is_refused(seat, monkeypatch):
    telegram_group(monkeypatch, "9999", GROUP)
    out = send(seat, "hi")
    assert out.startswith("partner_message_send refused"), out
    assert not SENT


def test_the_tool_registers_only_on_a_seat_with_people(monkeypatch, tmp_path):
    class Ctx:
        def __init__(self) -> None:
            self.tools: list[str] = []
            self.hooks: list[str] = []

        def register_tool(self, **kw: Any) -> None:
            self.tools.append(kw["name"])

        def register_hook(self, name: str, cb: Any) -> None:
            self.hooks.append(name)

    doc = couples_yaml()
    doc.pop("people")
    doc.pop("spaces")
    write_seat(monkeypatch, tmp_path, doc)
    ctx = Ctx()
    load_plugin("hermes-smd-partner-messages").register(ctx)
    assert ctx.tools == [] and ctx.hooks == []
    write_seat(monkeypatch, tmp_path, couples_yaml())
    ctx = Ctx()
    load_plugin("hermes-smd-partner-messages").register(ctx)
    assert ctx.tools == ["partner_message_send"]
    assert ctx.hooks == ["pre_llm_call"]
    space._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# 4. The text
# ---------------------------------------------------------------------------


def test_empty_and_oversize_text_are_refused(seat, monkeypatch):
    telegram_dm(monkeypatch, A_TG)
    assert "nothing to pass along" in send(seat, "   ")
    assert "longer than" in send(seat, "x" * (pm.MAX_TEXT + 1))
    assert not SENT


def test_a_bot_api_failure_is_a_refusal_naming_telegram(seat, monkeypatch):
    def boom(chat_id: str, text: str) -> int:
        raise telegram_bot.TelegramSendError("Bot API sendMessage failed: URLError")

    monkeypatch.setattr(telegram_bot, "send_message", boom)
    telegram_dm(monkeypatch, A_TG)
    plugin = seat["tool"]
    out = plugin._send_handler({"text": "hi"}, session_id="s")
    assert out.startswith("partner_message_send refused"), out
    assert "Telegram" in out
    assert pm.load() == []


# ---------------------------------------------------------------------------
# 5. The ledger and what the next turn is shown
# ---------------------------------------------------------------------------


def test_a_delivered_message_is_recorded_once(seat, monkeypatch):
    telegram_dm(monkeypatch, A_TG)
    send(seat, "I think he is cute")
    rows = pm.load()
    assert len(rows) == 1
    rec = rows[0]
    assert (rec.sender, rec.recipient, rec.channel, rec.mode) == (
        "p1",
        "p2",
        "telegram",
        "as_written",
    )
    assert rec.given == "I think he is cute"
    assert rec.delivered.startswith("Ana asked me to pass this along:")
    assert rec.ref == "7001"


def test_bens_next_private_turn_is_shown_what_ana_passed(seat, monkeypatch):
    """FALSIFIER: inject nothing; "tell her thanks" then has no referent."""
    telegram_dm(monkeypatch, A_TG)
    send(seat, "I think he is cute")
    telegram_dm(monkeypatch, B_TG)
    out = seat["tool"].on_pre_llm_call(session_id="s2")
    assert out is not None
    block = out["context"]
    assert "from Ana to Ben" in block
    assert '"I think he is cute"' in block
    assert "reached them by telegram" in block


def test_anas_own_private_turn_is_shown_what_she_sent(seat, monkeypatch):
    telegram_dm(monkeypatch, A_TG)
    send(seat, "I think he is cute")
    out = seat["tool"].on_pre_llm_call(session_id="s3")
    assert out is not None and "from Ana to Ben" in out["context"]


def test_the_shared_group_is_shown_nothing(seat, monkeypatch):
    """FALSIFIER: inject into the group; a third member of it could read a DM relay."""
    telegram_dm(monkeypatch, A_TG)
    send(seat, "I think he is cute")
    telegram_group(monkeypatch, B_TG, GROUP)
    assert seat["tool"].on_pre_llm_call(session_id="g") is None


def test_a_scheduled_run_is_shown_nothing(seat, monkeypatch):
    telegram_dm(monkeypatch, A_TG)
    send(seat, "hi")
    cron_to(monkeypatch, B_TG)
    # A cron run to Ben's DM resolves Ben's space, which is private; that is
    # the one scheduled turn that may see it, because it speaks to Ben alone.
    out = seat["tool"].on_pre_llm_call(session_id="cron_x")
    assert out is None or "from Ana to Ben" in out["context"]


def test_old_messages_fall_out_of_the_block(seat):
    cfg = space.space_config()
    assert cfg is not None
    old = datetime.now(timezone.utc) - timedelta(days=pm.RECENT_DAYS + 1)
    pm.record(
        pm.Record(
            id="a",
            at=old.isoformat(timespec="seconds"),
            sender="p1",
            recipient="p2",
            given="stale",
            delivered="x",
            mode="as_written",
            channel="telegram",
            ref="1",
        )
    )
    current = space.Space(kind="person", person_id="p2", source=space.SOURCE_TELEGRAM_DM)
    assert pm.context_block(current, cfg) == ""


def test_a_malformed_ledger_line_hides_nothing_else(seat):
    ledger: Path = seat["ledger"]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("not json\n", encoding="utf-8")
    pm.record(
        pm.Record(
            id="b",
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            sender="p1",
            recipient="p2",
            given="ok",
            delivered="x",
            mode="as_written",
            channel="telegram",
            ref="2",
        )
    )
    assert [r.id for r in pm.load()] == ["b"]


# ---------------------------------------------------------------------------
# 6. The seam
# ---------------------------------------------------------------------------


def test_only_as_written_is_built(seat):
    cfg = space.space_config()
    assert cfg is not None
    ana = cfg.person("p1")
    assert ana is not None
    with pytest.raises(pm.PartnerMessageError, match="not built yet"):
        pm.shape("hi", sender=ana, mode="softened")


def test_the_seat_must_have_exactly_two_people(seat, monkeypatch, tmp_path):
    doc = couples_yaml()
    doc["people"].append(
        {
            "id": "p3",
            "name": "Cy",
            "telegram_id": "3003",
            "email": "cy@example.com",
            "channel": "telegram",
        }
    )
    write_seat(monkeypatch, tmp_path, doc)
    telegram_dm(monkeypatch, A_TG)
    out = send(seat, "hi")
    assert "two of you only" in out
    assert not SENT
