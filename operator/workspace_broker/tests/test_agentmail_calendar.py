"""A calendar invitation on agentmail_send: validated, built and audited here.

The guarantees, each with a test that fails if it regresses:

* the broker, never the caller, decides the part: filename, content type and
  disposition are fixed, and a caller's ``attachments`` is still refused;
* every ATTENDEE is a to/cc recipient, so the recipient fence and the
  participant fence that govern the message govern everyone it invites;
* the ORGANIZER is this seat's own inbox;
* nothing in the invitation reaches a person the message does not (no EMAIL
  alarm, no alarm attendee, no ATTACH);
* the CONFIRM_SEND row carries the sha256 of the exact bytes sent, computed
  broker-side, and a caller's claimed sha cannot replace it;
* a send without ``calendar`` is byte-identical to before.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from workspace_broker import agentmail_calendar
from workspace_broker.agentmail_ops import AgentMailOps, AgentMailRefused
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
SEAT = "couples-demo"
SEAT_INBOX = "couples-demo@agentmail.to"
P1 = "alex@example.com"
P2 = "sam@example.com"

#: A space-mode seat's shape (ss-console#3264): both people on the reply
#: roster, and the calendar skill opted into the roster lane.
SEAT_YAML = f"""
connectors:
  Email:
    adapter: agentmail
    enabled: true
    inbox_address: {SEAT_INBOX}
scope:
  inbound_allow_from:
    - {P1}
    - {P2}
    - scott@smd.services
  admins:
    - scott@smd.services
personas:
  - slug: couples
    skills:
      - name: calendar
        enabled: true
        settings:
          recipients: roster
"""


def ics(
    *,
    method: str = "REQUEST",
    organizer: str = SEAT_INBOX,
    attendees: tuple[str, ...] = (P1, P2),
    sequence: str = "0",
    extra_event: tuple[str, ...] = (),
    extra_calendar: tuple[str, ...] = (),
    alarm: tuple[str, ...] = ("ACTION:DISPLAY", "TRIGGER:-PT2H", "DESCRIPTION:Reminder"),
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//SMD Services//Hosted Agent//EN",
        "VERSION:2.0",
        f"METHOD:{method}",
        *extra_calendar,
        "BEGIN:VEVENT",
        "UID:3f1c8f2e-1111-4a5b-9c0d-000000000001@smd.services",
        f"SEQUENCE:{sequence}",
        "DTSTAMP:20261017T120000Z",
        "DTSTART:20261025T013000Z",
        "DTEND:20261025T033000Z",
        "SUMMARY:Date night",
        f'ORGANIZER;CN="Couples Agent: test":mailto:{organizer}',
        *[f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{a}" for a in attendees],
        *extra_event,
    ]
    if alarm:
        lines += ["BEGIN:VALARM", *alarm, "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def payload(text: str | None = None, method: str = "REQUEST", **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"to": [P1, P2], "subject": "Invitation: Date night", "text": "See you there."}
    body.update(fields)
    body["calendar"] = {"method": method, "ics": text if text is not None else ics(method=method)}
    return body


class FakeHTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode()) if request.data else None
        self.calls.append((request.method, request.full_url, body))
        if "/messages/send" in request.full_url:
            out: dict[str, Any] = {"message_id": "msg_invite", "thread_id": "thr_1"}
        else:
            out = {"inboxes": [{"inbox_id": SEAT_INBOX}]}
        return _Response(json.dumps(out))

    def posts(self) -> list[dict]:
        return [c[2] or {} for c in self.calls if c[0] == "POST"]


class _Response:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class RecordingLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row: dict) -> str:
        self.rows.append(row)
        return f"row-{len(self.rows)}"


def _ops(tmp_path: Path, http: FakeHTTP) -> AgentMailOps:
    customer = tmp_path / "customer.yaml"
    customer.write_text(SEAT_YAML)
    credential = tmp_path / "agentmail.json"
    credential.write_text("am_test_key")
    return AgentMailOps(credential, customer, SEAT, opener=http)


def _broker(tmp_path: Path, http: FakeHTTP) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = SEAT
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = RecordingLedger()
    broker.agentmail = _ops(tmp_path, http)
    broker.customer_path = broker.agentmail._customer_path
    return broker


def _meta(broker: Broker, index: int = -1) -> dict:
    return json.loads(broker.ledger.rows[index]["metadata"])


# -- the part the broker builds ---------------------------------------------


def test_an_invitation_becomes_one_broker_built_calendar_part(tmp_path: Path) -> None:
    http = FakeHTTP()
    text = ics()
    result = _ops(tmp_path, http).send(payload(text))
    (post,) = http.posts()
    (part,) = post["attachments"]
    assert part == {
        "filename": "invite.ics",
        "content_type": "text/calendar; method=REQUEST; charset=UTF-8",
        "content_disposition": "attachment",
        "content": base64.b64encode(text.encode()).decode(),
    }
    assert "calendar" not in post, "the field itself never reaches the vendor"
    assert result["attachment_sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_a_cancel_carries_method_cancel_in_its_content_type(tmp_path: Path) -> None:
    http = FakeHTTP()
    _ops(tmp_path, http).send(payload(method="CANCEL"))
    assert http.posts()[0]["attachments"][0]["content_type"] == "text/calendar; method=CANCEL; charset=UTF-8"


def test_a_caller_attachments_list_is_still_refused_beside_a_calendar(tmp_path: Path) -> None:
    http = FakeHTTP()
    smuggled = {"filename": "x.pdf", "content_type": "application/pdf", "content": "JVBERg=="}
    with pytest.raises(AgentMailRefused, match="^attachment refused: "):
        _ops(tmp_path, http).send(payload(attachments=[smuggled]))
    assert http.posts() == []


def test_a_send_without_calendar_is_unchanged(tmp_path: Path) -> None:
    """Falsifier for the tests above: no field, no part, no stamp."""
    http = FakeHTTP()
    result = _ops(tmp_path, http).send({"to": [P1], "text": "hello"})
    assert "attachments" not in http.posts()[0]
    assert "attachment_sha256" not in result


def test_a_reply_never_carries_an_invitation(tmp_path: Path) -> None:
    http = FakeHTTP()
    with pytest.raises(AgentMailRefused, match="^calendar refused: "):
        _ops(tmp_path, http).reply({"message_id": "m1", "text": "x", "calendar": {"method": "REQUEST", "ics": ics()}})
    assert http.posts() == []


# -- what the invitation may say --------------------------------------------


@pytest.mark.parametrize(
    ("why", "body"),
    [
        ("an attendee the message is not addressed to", payload(ics(attendees=(P1, "stranger@example.com")))),
        ("an attendee only on bcc", payload(ics(attendees=(P1, P2)), to=[P1], bcc=[P2])),
        ("no attendee at all", payload(ics(attendees=()))),
        ("a forged organizer", payload(ics(organizer="someone-else@agentmail.to"))),
        ("a METHOD that disagrees with the declared one", payload(ics(method="CANCEL"), method="REQUEST")),
        ("an unknown method", {"to": [P1], "calendar": {"method": "PUBLISH", "ics": ics(method="PUBLISH")}}),
        ("an email alarm", payload(ics(alarm=("ACTION:EMAIL", "TRIGGER:-PT2H", "SUMMARY:x")))),
        (
            "an alarm attendee",
            payload(ics(alarm=("ACTION:DISPLAY", "TRIGGER:-PT2H", "DESCRIPTION:x", "ATTENDEE:mailto:x@example.com"))),
        ),
        ("an ATTACH", payload(ics(extra_event=("ATTACH:https://example.com/a.pdf",)))),
        ("a second event", payload(ics(extra_calendar=("BEGIN:VEVENT", "UID:two", "END:VEVENT")))),
        ("a VTODO", payload(ics(extra_calendar=("BEGIN:VTODO", "UID:t", "END:VTODO")))),
        ("a negative sequence", payload(ics(sequence="-1"))),
        ("a non-numeric sequence", payload(ics(sequence="one"))),
        ("bare LF line endings", payload(ics().replace("\r\n", "\n"))),
        ("an unclosed calendar", payload(ics().replace("END:VCALENDAR\r\n", ""))),
        ("an extra key beside method and ics", {"to": [P1], "calendar": {"method": "REQUEST", "ics": ics(), "x": 1}}),
        ("an oversized calendar", payload(ics(extra_event=("DESCRIPTION:" + "a" * 70_000,)))),
        ("a non-mailto attendee", payload(ics().replace(f"mailto:{P2}", f"tel:{P2}"))),
    ],
)
def test_an_invitation_that_could_mislead_or_reach_past_the_message_is_refused(
    tmp_path: Path, why: str, body: dict[str, Any]
) -> None:
    http = FakeHTTP()
    with pytest.raises(AgentMailRefused, match="^calendar refused: "):
        _ops(tmp_path, http).send(body)
    assert http.posts() == [], why


def test_the_refusal_cases_are_each_one_change_from_a_valid_invitation(tmp_path: Path) -> None:
    """Falsifier for the table above: the base invitation it perturbs is valid,
    with a folded line and a quoted parameter holding a colon."""
    folded = ics(
        extra_event=(
            "DESCRIPTION:a long description that is folded",
            " onto a second line",
        )
    )
    http = FakeHTTP()
    _ops(tmp_path, http).send(payload(folded, cc=[]))
    assert len(http.posts()) == 1


def test_an_attendee_on_cc_is_addressed(tmp_path: Path) -> None:
    http = FakeHTTP()
    _ops(tmp_path, http).send(payload(to=[P1], cc=[P2]))
    assert len(http.posts()) == 1


def test_build_returns_the_event_identity() -> None:
    invitation = agentmail_calendar.build(payload(ics(sequence="3")), organizer=SEAT_INBOX)
    assert invitation is not None
    assert invitation.uid == "3f1c8f2e-1111-4a5b-9c0d-000000000001@smd.services"
    assert invitation.sequence == 3
    assert invitation.attendees == (P1, P2)


# -- the audit row -----------------------------------------------------------


def _send(broker: Broker, request: dict[str, Any]) -> dict[str, Any]:
    return broker.handle({"action": "agentmail_send", **request}, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def test_the_dispatched_row_carries_the_broker_computed_sha(tmp_path: Path) -> None:
    text = ics()
    broker = _broker(tmp_path, FakeHTTP())
    response = _send(broker, {"payload": payload(text), "lane": "skill:calendar"})
    assert response["ok"]
    meta = _meta(broker)
    assert broker.ledger.rows[-1]["action_type"] == "CONFIRM_SEND_DISPATCHED"
    assert meta["attachment_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert text not in json.dumps(broker.ledger.rows), "the invitation itself never reaches the ledger"


def test_a_caller_claimed_sha_cannot_replace_the_brokers(tmp_path: Path) -> None:
    text = ics()
    broker = _broker(tmp_path, FakeHTTP())
    _send(broker, {"payload": payload(text), "lane": "skill:calendar", "audit_extra": {"attachment_sha256": "0" * 64}})
    assert _meta(broker)["attachment_sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_a_refused_invitation_is_audited(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeHTTP())
    with pytest.raises(AgentMailRefused):
        _send(broker, {"payload": payload(ics(organizer="x@agentmail.to")), "lane": "skill:calendar"})
    assert broker.ledger.rows[-1]["action_type"] == "CONFIRM_SEND_FAILED"
    assert _meta(broker)["outcome"] == "refused"


# -- the participant fence (unchanged; this pins how invitations meet it) ----


def test_people_on_the_roster_lane_are_reachable_with_no_anchor(tmp_path: Path) -> None:
    """A space-mode seat's calendar skill authors ``recipients: roster``; its
    lane admits the seat's exact inbound_allow_from, so a Telegram-originated
    invitation (no email anchor) reaches both people."""
    http = FakeHTTP()
    broker = _broker(tmp_path, http)
    assert _send(broker, {"payload": payload(), "lane": "skill:calendar"})["ok"]
    assert len(http.posts()) == 1


def test_without_the_lane_the_participant_fence_refuses_the_same_invitation(tmp_path: Path) -> None:
    """Falsifier for the test above: the lane, not the invitation, is what admits
    the people; with no lane and no anchor the fence refuses before any send."""
    http = FakeHTTP()
    broker = _broker(tmp_path, http)
    with pytest.raises(AgentMailRefused, match="participant fence:"):
        _send(broker, {"payload": payload()})
    assert http.posts() == []


# -- the overlay's own invitations (MIRROR) -------------------------------------

#: COPIED from hermes-smd-overlay ``tests/fixtures/calendar/`` (the golden
#: invitations ``shared.ical`` renders, overlay#475). The overlay cannot run this
#: validator and this repo cannot run that renderer, so the bytes themselves are
#: the contract: when the overlay regenerates its goldens, copy them here in
#: the same change, or this proves an old renderer.
OVERLAY_GOLDEN = Path(__file__).parent / "fixtures" / "calendar"
OVERLAY_ORGANIZER = "couples-demo@agentmail.to"


@pytest.mark.parametrize("name", sorted(p.name for p in OVERLAY_GOLDEN.glob("*.ics")))
def test_every_invitation_the_overlay_renders_passes_this_validator(name: str) -> None:
    data = (OVERLAY_GOLDEN / name).read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), "the copy lost its CRLFs"
    text = data.decode("utf-8")
    method = "CANCEL" if "\r\nMETHOD:CANCEL\r\n" in text else "REQUEST"
    body = {"to": ["ana@example.com", "ben@example.com"], "calendar": {"method": method, "ics": text}}
    invitation = agentmail_calendar.build(body, organizer=OVERLAY_ORGANIZER)
    assert invitation is not None and invitation.sha256 == hashlib.sha256(data).hexdigest()


def test_the_mirror_is_not_empty() -> None:
    assert len(list(OVERLAY_GOLDEN.glob("*.ics"))) == 6
