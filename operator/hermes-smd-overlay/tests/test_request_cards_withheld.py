"""A seat can send SMD request cards with who and what happened, and no words.

WHY (ss-console #3946, critic issue 1). Any request card that is not a success
emails team@smd.services the person's request text and the reply text. On a
guide seat that is exactly the channel that would carry a representative's
private notes and leads to SMD. A seat that authors
``escalation.request_card_text: false`` posts every card with its word fields
emptied and ``text_withheld: true``; the console honours it independently
(``tests/operator-request-card-withheld.test.ts``). A seat that authors nothing
posts exactly what it posted before.

Driven through the real heartbeat leg (``send_due_cards``) over a real request
index and audit ledger. FALSIFIER: on main the leg posted the words whatever the
seat authored, so the first test fails there.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from shared import request_cards as rc
from shared import request_index as ri
from tests.test_request_cards import NOW, _epoch, _Ledger, _Poster

MARK = "CANARY-CARD-q7m3"


def _seat(tmp_path: Path, monkeypatch, escalation: dict) -> None:
    doc = {
        "customer_id": "guide-test",
        "escalation": {"red_flag_recipients": ["a@b.example"], **escalation},
    }
    path = tmp_path / "customer.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _post_one(tmp_path: Path) -> list[dict]:
    ledger = _Ledger(tmp_path / "audit.db")
    ri.record_inbound(
        vendor_message_id="m-withheld",
        sender="rep-a@example.test",
        subject=f"Acme {MARK}",
        body=f"Please keep this: {MARK}",
        received_at=_epoch(60),
    )
    poster = _Poster()
    rc.send_due_cards(
        slug="guide-test",
        key="k",
        url=rc.card_url("https://smd.services/api/internal/heartbeat"),
        audit_db_path=str(ledger.path),
        post_fn=poster,
        store=rc.CardStore(),
        now_fn=lambda: NOW.timestamp(),
        monotonic_fn=lambda: 0.0,
    )
    return [body for _url, _headers, body, _timeout in poster.calls]


def test_a_seat_that_withholds_words_posts_who_and_outcome_only(tmp_path, monkeypatch) -> None:
    _seat(tmp_path, monkeypatch, {"request_card_text": False})
    cards = _post_one(tmp_path)
    assert cards, "the no-reply card should have been posted"
    for card in cards:
        assert MARK not in json.dumps(card)
        assert card["text_withheld"] is True
        assert card["who"] == "rep-a@example.test"
        assert card["kind"] == "no_reply"
        assert card["subject"] == rc.WITHHELD_SUBJECT


def test_a_seat_that_authors_nothing_posts_what_it_always_did(tmp_path, monkeypatch) -> None:
    _seat(tmp_path, monkeypatch, {})
    cards = _post_one(tmp_path)
    assert cards and MARK in json.dumps(cards[0])
    assert "text_withheld" not in cards[0]


def test_an_unreadable_config_keeps_todays_cards(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "missing.yaml"))
    cards = _post_one(tmp_path)
    assert cards and MARK in json.dumps(cards[0])


def test_the_seats_own_marker_subject_is_kept() -> None:
    card = {"subject": "(no request on record)", "reply_text": MARK, "who": "x"}
    out = rc.withhold_text(card)
    assert out["subject"] == "(no request on record)"
    assert out["reply_text"] is None and MARK not in json.dumps(out)
