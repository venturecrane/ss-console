"""One listed sender gets at most ``sender_enrollment.daily_limit`` emails a day answered.

WHY (ss-console #3946, gate 16). The seat-wide inbound limit counts every
delivery before the sender is known, so one representative practising all
afternoon starves everyone else and every email is a paid model turn. On a
guide seat that authors ``daily_limit``, the webhook router (where the sender
is verified) drops a listed non-administrator's email past the limit before
any model turn, writes the rows the hourly triage reads, and sends that sender
ONE fixed notice a day. Driven through the router's real
``on_pre_gateway_dispatch`` with signed deliveries, real sender
authentication and the real enrolled list; only the broker transmit is stubbed.

FALSIFIER: on main the router had no limit, so the third email from the same
person is dispatched and the first test fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from shared import agentmail_broker
from shared import sender_enrollment as se
from tests.test_inbound import _clear_pending, _load_router_with_table, _signed_kwargs  # noqa: F401
from tests.test_sender_enrollment import PASS, _write_enrolled

REP = "rep@outside.example"
ADMIN = "scott@smd.services"


def _guide(limit: int | None) -> str:
    block: dict = {"enabled": True}
    if limit is not None:
        block["daily_limit"] = limit
    return yaml.safe_dump(
        {
            "customer_id": "smd-guide",
            "seat": {"kind": "internal", "product": "operator", "role": "guide"},
            "sender_enrollment": block,
            "scope": {"inbound_allow_from": [ADMIN], "admins": [ADMIN]},
            "webhook_triggers": [
                {
                    "source": "agentmail",
                    "event_type": "message.received",
                    "skill": "product-guide",
                    "persona": "guide",
                }
            ],
        }
    )


def _mail(sender: str, mid: str, *, results: dict | None = None) -> dict:
    return {
        "source": "agentmail",
        "event_type": "message.received",
        "data": {
            "inbox_id": "smd-guide@agentmail.to",
            "message_id": mid,
            "from": sender,
            "subject": "practice",
            "text": "One more round please.",
            "authentication_results": PASS if results is None else results,
        },
    }


@pytest.fixture
def router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    enrolled = tmp_path / "enrolled.json"
    monkeypatch.setenv(se.ENROLLED_ENV, str(enrolled))
    _write_enrolled(enrolled, {"address": REP, "label": "x", "code_id": "c_1"})
    monkeypatch.setenv("SMD_SENDER_DAILY_DB_PATH", str(tmp_path / "sender_daily.db"))
    mod, fake = _load_router_with_table(tmp_path, monkeypatch)
    replies: list[tuple[str, str]] = []

    def send_reply(message_id, text="", html="", **_kw):
        replies.append((message_id, text))
        return "out-1"

    monkeypatch.setattr(agentmail_broker, "send_reply", send_reply)

    def author(limit: int | None) -> None:
        (tmp_path / "customer.yaml").write_text(_guide(limit))

    author(2)
    return mod, fake, replies, author


def _dispatch(mod, payload: dict, n: int):
    return mod.on_pre_gateway_dispatch(**_signed_kwargs(payload, session_id="", event_id=f"e{n}"))


def _rows(fake, action: str) -> list[dict]:
    out = []
    for _sql, params in fake.calls:
        if params[2] != action:
            continue
        meta = next((json.loads(p) for p in params if isinstance(p, str) and p.startswith("{")), {})
        out.append(meta)
    return out


def test_past_the_limit_the_email_is_not_run_and_the_sender_is_told_once(router) -> None:
    mod, fake, replies, _author = router
    verdicts = [_dispatch(mod, _mail(REP, f"m{i}"), i) for i in range(4)]
    assert verdicts[0] != {"action": "skip"} and verdicts[1] != {"action": "skip"}
    assert verdicts[2] == {"action": "skip"} and verdicts[3] == {"action": "skip"}
    assert replies == [("m2", mod.daily_limit.NOTICE)], "one notice, on the first email over"
    held = _rows(fake, "REPLY_HELD")
    assert [h["message_id"] for h in held] == ["m2", "m3"]
    assert {h["reason"] for h in held} == {"sender_daily_limit"}
    received = [
        r for r in _rows(fake, "INBOUND_RECEIVED") if r.get("vendor_message_id") in ("m2", "m3")
    ]
    assert len(received) == 2 and {r["trust_class"] for r in received} == {"internal"}


def test_administrators_are_never_limited(router) -> None:
    mod, _fake, replies, _author = router
    for i in range(5):
        assert _dispatch(mod, _mail(ADMIN, f"a{i}"), 10 + i) != {"action": "skip"}
    assert replies == []


def test_a_seat_that_authors_no_limit_runs_every_email(router) -> None:
    mod, fake, replies, author = router
    author(None)
    for i in range(5):
        assert _dispatch(mod, _mail(REP, f"n{i}"), 20 + i) != {"action": "skip"}
    assert replies == [] and _rows(fake, "REPLY_HELD") == []


def test_an_unauthenticated_from_is_not_counted_as_the_person(router) -> None:
    mod, _fake, replies, _author = router
    for i in range(4):
        _dispatch(mod, _mail(REP, f"u{i}", results={}), 30 + i)
    # The real person's quota is untouched by forged mail.
    assert _dispatch(mod, _mail(REP, "real-1"), 40) != {"action": "skip"}
    assert replies == []


def test_the_notice_promises_nothing_but_tomorrow(router) -> None:
    mod = router[0]
    notice = mod.daily_limit.NOTICE
    assert "$" not in notice and "—" not in notice
    assert not any(ch.isdigit() for ch in notice)
