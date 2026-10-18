"""A space-mode seat's two people-only sends (ADR 0091, space_people.py).

* the ``space`` lane reaches exactly the seat's ``people[].email`` and nobody
  else on the roster, and reaches nobody on a seat with no ``people``;
* a SHARED reply copies the other people already on a joint thread, and is
  refused, whole, when any condition fails. A stranger on the thread is never
  copied; a seat without ``people`` never copies anyone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import participant_fence as pf
from workspace_broker.agentmail_ops import AgentMailOps, AgentMailRefused
from workspace_broker.space_people import LANE_SPACE, people_emails

SEAT = "captain-mark"
SEAT_INBOX = "captain-mark@agentmail.to"
P1, P2 = "pat@example.com", "sam@example.com"
STRANGER = "neighbor@example.org"

COUPLE_YAML = f"""
people:
  - id: p1
    name: Pat One
    telegram_id: ''
    email: {P1}
  - id: p2
    name: Sam Two
    telegram_id: ''
    email: {P2}
spaces:
  shared_telegram_chat: ''
scope:
  inbound_allow_from:
    - {P1}
    - {P2}
    - scott@smd.services
  admins:
    - scott@smd.services
"""

SOLO_YAML = f"""
scope:
  inbound_allow_from:
    - {P1}
    - {P2}
    - scott@smd.services
"""


# ------------------------------------------------------------------ lane


def _facts(tmp_path: Path, text: str) -> pf.SeatFacts:
    path = tmp_path / "customer.yaml"
    path.write_text(text)
    return pf.seat_facts(path)


def _no_lookup(_anchor):
    raise AssertionError("the space lane must decide without reading a mailbox")


def test_the_space_lane_reaches_exactly_the_people(tmp_path: Path) -> None:
    facts = _facts(tmp_path, COUPLE_YAML)
    assert facts.lanes[LANE_SPACE] == frozenset({P1, P2})
    pf.check_send(facts, [P1, P2], lane=LANE_SPACE, anchor=None, resolve=_no_lookup)
    pf.check_send(facts, [P2], lane=LANE_SPACE, anchor=None, resolve=_no_lookup)


def test_the_space_lane_does_not_reach_the_rest_of_the_roster(tmp_path: Path) -> None:
    # scott@smd.services is SMD, which the fence never gates; a firm-class
    # roster member who is not a person is the falsifier.
    text = COUPLE_YAML.replace(
        "    - scott@smd.services\n  admins", "    - scott@smd.services\n    - nanny@example.net\n  admins"
    )
    facts = _facts(tmp_path, text)
    with pytest.raises(pf.FenceRefused, match="nanny@example.net"):
        pf.check_send(facts, [P1, "nanny@example.net"], lane=LANE_SPACE, anchor=None, resolve=_no_lookup)


def test_the_space_lane_reaches_nobody_without_people(tmp_path: Path) -> None:
    facts = _facts(tmp_path, SOLO_YAML)
    assert facts.lanes[LANE_SPACE] == frozenset()
    with pytest.raises(pf.FenceRefused):
        pf.check_send(facts, [P1], lane=LANE_SPACE, anchor=None, resolve=_no_lookup)


def test_a_caller_may_name_the_space_lane() -> None:
    assert pf.parse_lane(LANE_SPACE) == LANE_SPACE


def test_people_emails_skips_unusable_entries() -> None:
    data = {"people": [{"email": f"Pat <{P1.upper()}>"}, {"email": ""}, "x", {"email": P1}, {"email": P2}]}
    assert people_emails(data) == (P1, P2)
    assert people_emails({}) == ()


# --------------------------------------------------------- shared reply


class FakeHTTP:
    def __init__(self, source: dict[str, Any]) -> None:
        self.source = source
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode()) if request.data else None
        self.calls.append((request.method, request.full_url, body))
        if "/reply" in request.full_url:
            payload: dict[str, Any] = {"message_id": "msg_reply"}
        elif "/messages/m1" in request.full_url:
            payload = self.source
        else:
            payload = {"inboxes": [{"inbox_id": SEAT_INBOX}]}
        return _Response(json.dumps(payload))

    def posted(self) -> list[dict | None]:
        return [b for m, _u, b in self.calls if m == "POST"]


class _Response:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _ops(tmp_path: Path, http: FakeHTTP, text: str = COUPLE_YAML) -> AgentMailOps:
    customer = tmp_path / "customer.yaml"
    customer.write_text(text)
    credential = tmp_path / "agentmail.json"
    credential.write_text("am_test_key")
    return AgentMailOps(credential, customer, SEAT, opener=http)


def _joint(sender: str = P1, to=(SEAT_INBOX, P2), cc=()) -> dict[str, Any]:
    return {"from": sender, "to": list(to), "cc": list(cc)}


def test_a_shared_reply_copies_the_other_person_on_a_joint_thread(tmp_path: Path) -> None:
    http = FakeHTTP(_joint())
    result = _ops(tmp_path, http).reply({"message_id": "m1", "text": "hi both", "shared": True})
    assert http.posted() == [{"text": "hi both", "cc": [P2]}]
    assert result["recipients"] == [P1, P2]


def test_the_other_person_on_cc_counts_as_on_the_thread(tmp_path: Path) -> None:
    http = FakeHTTP(_joint(sender=P2, to=(SEAT_INBOX,), cc=(P1,)))
    _ops(tmp_path, http).reply({"message_id": "m1", "text": "x", "shared": True})
    assert http.posted() == [{"text": "x", "cc": [P1]}]


def test_a_stranger_on_the_thread_is_never_copied(tmp_path: Path) -> None:
    http = FakeHTTP(_joint(to=(SEAT_INBOX, P2, STRANGER), cc=("another@example.org",)))
    _ops(tmp_path, http).reply({"message_id": "m1", "text": "x", "shared": True})
    (posted,) = http.posted()
    assert posted["cc"] == [P2]
    assert STRANGER not in json.dumps(posted)


def test_a_shared_reply_to_a_thread_missing_the_other_person_is_refused(tmp_path: Path) -> None:
    http = FakeHTTP(_joint(to=(SEAT_INBOX,)))
    with pytest.raises(AgentMailRefused, match="every person on the thread"):
        _ops(tmp_path, http).reply({"message_id": "m1", "text": "x", "shared": True})
    assert http.posted() == []


def test_a_shared_reply_from_a_non_person_is_refused(tmp_path: Path) -> None:
    http = FakeHTTP(_joint(sender="scott@smd.services", to=(SEAT_INBOX, P1, P2)))
    with pytest.raises(AgentMailRefused, match="one of the seat's people"):
        _ops(tmp_path, http).reply({"message_id": "m1", "text": "x", "shared": True})
    assert http.posted() == []


def test_a_shared_reply_on_a_seat_without_people_is_refused(tmp_path: Path) -> None:
    http = FakeHTTP(_joint())
    with pytest.raises(AgentMailRefused, match="two or more people"):
        _ops(tmp_path, http, SOLO_YAML).reply({"message_id": "m1", "text": "x", "shared": True})
    assert http.posted() == []


@pytest.mark.parametrize("flag", ["true", 1, "yes"])
def test_only_a_literal_true_asks_for_a_shared_reply(tmp_path: Path, flag: Any) -> None:
    http = FakeHTTP(_joint())
    with pytest.raises(AgentMailRefused, match="shared must be true"):
        _ops(tmp_path, http).reply({"message_id": "m1", "text": "x", "shared": flag})


@pytest.mark.parametrize("payload_extra", [{}, {"shared": False}])
def test_an_ordinary_reply_is_unchanged(tmp_path: Path, payload_extra: dict) -> None:
    http = FakeHTTP(_joint())
    result = _ops(tmp_path, http).reply({"message_id": "m1", "text": "x", **payload_extra})
    assert http.posted() == [{"text": "x"}]
    assert result["recipients"] == [P1]


def test_a_shared_reply_in_the_conversation_is_fenced_on_the_whole_thread(tmp_path: Path) -> None:
    """A shared reply to a later email in the anchor's conversation reaches that
    email's To/Cc too, so the fence checks all of them, like a Graph reply."""
    facts = _facts(
        tmp_path, COUPLE_YAML.replace(f"    - {P2}\n    - scott", f"    - {P2}\n    - nanny@example.net\n    - scott")
    )
    home = pf.Participants(message_id="m0", sender=P1, to=(SEAT_INBOX, P2), cc=(), conversation_id="c1")
    later = pf.Participants(
        message_id="m1", sender=P2, to=(SEAT_INBOX, P1, "nanny@example.net"), cc=(), conversation_id="c1"
    )

    def resolve(anchor: pf.Anchor) -> pf.Participants:
        return home if anchor.ident == "m0" else later

    anchor = pf.Anchor("agentmail_message", "m0")
    # an ordinary AgentMail reply reaches only the later email's sender: allowed
    pf.check_reply(facts, "m1", anchor=anchor, resolve=resolve, target_kind="agentmail_message")
    # a shared one would reach the nanny, who was not on the request: refused
    with pytest.raises(pf.FenceRefused, match="nanny@example.net"):
        pf.check_reply(facts, "m1", anchor=anchor, resolve=resolve, target_kind="agentmail_message", copies_thread=True)
