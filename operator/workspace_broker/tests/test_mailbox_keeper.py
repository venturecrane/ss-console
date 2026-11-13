"""The mailbox keeper (mailbox_keeper.py, mailbox_keeper_verbs.py), end to end.

Every test drives the REAL verb through the real verb table on a real
``Broker`` (built as test_agentmail_send.py builds it): the real
``WorkspaceOperations`` over a fake Gmail service, and the real ``AgentMailOps``
over a fake HTTP opener, so the digest goes through the same fenced, audited
transmit the seat uses. Only the vendors are fake.

The fixtures are the shapes the plan names, each built the way the real sender
builds it: the alert and recovery HTML exactly as
``workers/fleet-alerts/src/alert-words.ts`` ``alertHtml`` writes it, the
recovery's In-Reply-To an ``<smd-alert.<uuid>@smd.services>`` id, and the
delivered Message-ID rewritten by SES (so nothing may key on it).

THE FALSIFIER: across every fixture, no open alert is ever trashed
(``test_no_open_alert_is_ever_trashed``), and a read that fails trashes and
sends nothing.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import workspace_broker.operations as ops_mod
from workspace_broker import mailbox_keeper as mk
from workspace_broker import mailbox_keeper_verbs as mkv
from workspace_broker.agentmail_ops import AgentMailOps
from workspace_broker.operations import WorkspaceOperations
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
SEAT = "smd-office"
SEAT_INBOX = "smd-office@agentmail.to"
MAILBOX = "smdurgan@smdurgan.com"
OWNERS = "smdurgan@smdurgan.com,scott@smd.services"
RECIPIENT = "scott@smd.services"
ALERT_FROM = "SMD Services Ops <team@smd.services>"
GOOD_AUTH = "mx.google.com; dkim=pass header.i=@smd.services header.s=resend header.b=AbC; spf=pass"
NO_DKIM = "mx.google.com; dkim=none; spf=softfail smtp.mailfrom=evil.example"
UUID1 = "11111111-2222-4333-8444-555555555555"
UUID2 = "66666666-7777-4888-9999-aaaaaaaaaaaa"
NOW_MS = int(time.time() * 1000)
HOUR = 3_600_000

SEAT_YAML = f"""
schema_version: 1
connectors:
  Email:
    adapter: agentmail
    enabled: true
    inbox_address: {SEAT_INBOX}
google_auth:
  mode: dwd
  subject: {MAILBOX}
  scopes: [https://www.googleapis.com/auth/gmail.modify]
  managed_mailboxes:
    - address: {MAILBOX}
      send_as: [scott@smd.services, smdurgan@smdurgan.com]
scope:
  inbound_allow_from: [scott@smd.services, smdurgan@smdurgan.com]
  admins: [scott@smd.services]
personas:
  - name: smd-operations
    skills:
      - name: mailbox-keeper
        version: pending
        initiation: {{manual: false, scheduled: true, webhook: false}}
        enabled: true
        settings:
          mailbox: {MAILBOX}
          owner_addresses: '{OWNERS}'
          digest_recipient: {RECIPIENT}
          needs_you_after_hours: 24
          window_days: 14
    cron:
      - skill: mailbox-keeper
        schedule: '47 6,12 * * *'
        pre_run: pre_run.py
        wake_policy: pre_run_decides
"""


# ------------------------------------------------------------------ fixtures


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _alert_html(happened: str, todo: str, detail: str) -> str:
    # alertHtml in workers/fleet-alerts/src/alert-words.ts, byte for byte in shape.
    return (
        f"<p><strong>WHAT HAPPENED:</strong> {happened}</p>"
        f"<p><strong>WHAT YOU NEED TO DO:</strong> {todo}</p>"
        "<hr><p><strong>DETAILS, for the agent who picks this up</strong><br>"
        f"Seat: ashton-price<br>Condition: heartbeat red<br>Detail: {detail}</p>"
    )


def msg(
    mid: str,
    thread: str,
    age_hours: float,
    sender: str,
    subject: str,
    *,
    html: str | None = None,
    text: str | None = None,
    labels: tuple[str, ...] = ("INBOX",),
    in_reply_to: str = "",
    auth: str = GOOD_AUTH,
    headers: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    hdrs = [
        {"name": "From", "value": sender},
        {"name": "Subject", "value": subject},
        # SES rewrites the alert's own Message-ID on delivery: never keyed on.
        {"name": "Message-ID", "value": f"<0100{mid}@email.amazonses.com>"},
        {"name": "Authentication-Results", "value": auth},
    ]
    if in_reply_to:
        hdrs += [{"name": "In-Reply-To", "value": in_reply_to}, {"name": "References", "value": in_reply_to}]
    hdrs += [{"name": k, "value": v} for k, v in headers]
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
    return {
        "id": mid,
        "threadId": thread,
        "labelIds": list(labels),
        "internalDate": str(NOW_MS - int(age_hours * HOUR)),
        "payload": {"mimeType": "multipart/alternative", "headers": hdrs, "parts": parts},
    }


ALERT_SUBJECT = "[SMD Ops] ashton-price: the Operator may be down"


def _opened(mid: str, thread: str, age: float, **kw: Any) -> dict[str, Any]:
    return msg(
        mid, thread, age, ALERT_FROM, ALERT_SUBJECT, html=_alert_html("The Operator may be down.", "Look.", "x"), **kw
    )


def _recovered(mid: str, thread: str, age: float, uuid: str = UUID1, **kw: Any) -> dict[str, Any]:
    return msg(
        mid,
        thread,
        age,
        ALERT_FROM,
        f"Re: {ALERT_SUBJECT}",
        html=_alert_html("This has cleared.", "Nothing.", "x"),
        in_reply_to=f"<smd-alert.{uuid}@smd.services>",
        **kw,
    )


def recovery_pair() -> dict[str, Any]:
    return {"id": "t-settled", "messages": [_opened("a1", "t-settled", 30), _recovered("a2", "t-settled", 29)]}


def reminder_with_recovery_phrase() -> dict[str, Any]:
    reminder = msg(
        "r2",
        "t-reminder",
        5,
        ALERT_FROM,
        f"Re: {ALERT_SUBJECT}",
        html=_alert_html("Still not fixed. The Operator may be down.", "Look.", "WHAT HAPPENED: This has cleared."),
        in_reply_to=f"<smd-alert.{UUID1}@smd.services>",
    )
    return {"id": "t-reminder", "messages": [_opened("r1", "t-reminder", 30), reminder]}


def spoofed_recovery() -> dict[str, Any]:
    return {
        "id": "t-spoof",
        "messages": [_opened("s1", "t-spoof", 30, auth=NO_DKIM), _recovered("s2", "t-spoof", 29, auth=NO_DKIM)],
    }


def lookalike_dkim_recovery() -> dict[str, Any]:
    bad = "mx.google.com; dkim=pass header.i=@smd.services.evil.example"
    return {
        "id": "t-lookalike",
        "messages": [_opened("l1", "t-lookalike", 30, auth=bad), _recovered("l2", "t-lookalike", 29, auth=bad)],
    }


def owner_replied_alert() -> dict[str, Any]:
    reply = msg(
        "o3",
        "t-owner",
        28,
        "Scott Durgan <scott@smd.services>",
        f"Re: {ALERT_SUBJECT}",
        text="On it.",
        labels=("SENT",),
    )
    return {"id": "t-owner", "messages": [_opened("o1", "t-owner", 30), reply, _recovered("o2", "t-owner", 27)]}


def seat_sent() -> dict[str, Any]:
    return {
        "id": "t-seat",
        "messages": [msg("e1", "t-seat", 30, f"SMD Operations <{SEAT_INBOX}>", "Your list", text="x")],
    }


def newsletter() -> dict[str, Any]:
    return {
        "id": "t-news",
        "messages": [
            msg(
                "n1",
                "t-news",
                40,
                "News <news@vendor.example>",
                "Weekly",
                text="x",
                headers=(("List-Unsubscribe", "<mailto:u@v.example>"),),
            )
        ],
    }


def replied_thread() -> dict[str, Any]:
    return {
        "id": "t-replied",
        "messages": [
            msg("p1", "t-replied", 50, "Pat Client <pat@client.example>", "Question", text="Can you call?"),
            msg("p2", "t-replied", 49, "Scott <smdurgan@smdurgan.com>", "Re: Question", text="Yes.", labels=("SENT",)),
        ],
    }


def waiting_person() -> dict[str, Any]:
    return {
        "id": "t-wait",
        "messages": [msg("w1", "t-wait", 30, "Dana Prospect <dana@prospect.example>", "Proposal question", text="Hi")],
    }


def too_new_person() -> dict[str, Any]:
    return {"id": "t-new", "messages": [msg("y1", "t-new", 2, "Lee <lee@prospect.example>", "Hello", text="Hi")]}


def unrecovered_alert() -> dict[str, Any]:
    return {"id": "t-open", "messages": [_opened("u1", "t-open", 10)]}


def send_refused_alert() -> dict[str, Any]:
    subject = "[SMD Ops] ashton-price: a scheduled message did not go out"
    return {
        "id": "t-refused",
        "messages": [
            msg(
                "f1",
                "t-refused",
                10,
                ALERT_FROM,
                subject,
                html="<p><strong>WHAT HAPPENED:</strong> A scheduled routine...</p>",
            )
        ],
    }


def reopened_after_recovery() -> dict[str, Any]:
    # Two incidents in one thread: the first cleared, the second is open.
    return {
        "id": "t-reopen",
        "messages": [_opened("z1", "t-reopen", 30), _recovered("z2", "t-reopen", 29), _opened("z3", "t-reopen", 5)],
    }


ALL_FIXTURES = [
    recovery_pair,
    reminder_with_recovery_phrase,
    spoofed_recovery,
    lookalike_dkim_recovery,
    owner_replied_alert,
    seat_sent,
    newsletter,
    replied_thread,
    waiting_person,
    too_new_person,
    unrecovered_alert,
    send_refused_alert,
    reopened_after_recovery,
]
OPEN_ALERT_THREADS = {"t-reminder", "t-spoof", "t-lookalike", "t-owner", "t-open", "t-refused", "t-reopen"}


# ------------------------------------------------------------------ fakes


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeGmail:
    """The slice of the Gmail v1 client the broker calls, over fixed threads."""

    def __init__(self, threads: list[dict[str, Any]], *, page_size: int = 2, fail_on: str = "") -> None:
        self.by_id = {t["id"]: t for t in threads}
        self.page_size = page_size
        self.fail_on = fail_on
        self.trashed: list[str] = []
        self.list_calls: list[dict[str, Any]] = []

    def users(self):
        return self

    def messages(self):
        return _Messages(self)

    def threads(self):
        return _Threads(self)


class _Messages:
    def __init__(self, g: FakeGmail) -> None:
        self.g = g

    def list(self, **kw):
        self.g.list_calls.append(kw)
        if self.g.fail_on == "list":
            raise OSError("gmail down")
        ids = [{"id": m["id"], "threadId": t["id"]} for t in self.g.by_id.values() for m in t["messages"]]
        start = int(kw.get("pageToken") or 0)
        page = ids[start : start + self.g.page_size]
        nxt = start + self.g.page_size
        body: dict[str, Any] = {"messages": page}
        if nxt < len(ids):
            body["nextPageToken"] = str(nxt)
        return _Call(lambda: body)


class _Threads:
    def __init__(self, g: FakeGmail) -> None:
        self.g = g

    def get(self, *, userId, id, format):
        if self.g.fail_on == "get":
            raise OSError("thread read failed")
        return _Call(lambda: json.loads(json.dumps(self.g.by_id[id])))

    def trash(self, *, userId, id):
        self.g.trashed.append(id)
        return _Call(lambda: {"id": id, "messages": self.g.by_id[id]["messages"]})


class FakeHTTP:
    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode()) if request.data else None
        if "/messages/send" in request.full_url:
            self.sends.append(body)
            payload: dict[str, Any] = {"message_id": f"msg_{len(self.sends)}", "thread_id": "th_1"}
        else:
            payload = {"inboxes": [{"inbox_id": SEAT_INBOX}]}
        return _Response(json.dumps(payload))


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
        self.rows: list[dict[str, Any]] = []

    def append(self, row: dict[str, Any]) -> str:
        self.rows.append(row)
        return f"row-{len(self.rows)}"


def _broker(tmp_path: Path, monkeypatch, gmail: FakeGmail, http: FakeHTTP, yaml_text: str = SEAT_YAML) -> Broker:
    customer = tmp_path / "customer.yaml"
    customer.write_text(yaml_text)
    credential = tmp_path / "agentmail.json"
    credential.write_text("am_test_key")
    monkeypatch.setattr(ops_mod, "service", lambda *a, **k: gmail)
    monkeypatch.setenv(mkv.STATE_DIR_ENV, str(tmp_path / "keeper"))
    broker = Broker.__new__(Broker)
    broker.customer_slug = SEAT
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = RecordingLedger()
    broker.customer_path = customer
    broker.operations = WorkspaceOperations(tmp_path / "google.json", customer)
    broker.agentmail = AgentMailOps(credential, customer, SEAT, opener=http)
    return broker


def _run(broker: Broker, mode: str = "scheduled", uid: int = AGENT_UID) -> dict[str, Any]:
    return broker.handle({"action": "mailbox_keeper_run", "mode": mode}, peer_pid=GATEWAY_PID + 7, peer_uid=uid)


def _all() -> list[dict[str, Any]]:
    return [f() for f in ALL_FIXTURES]


# ------------------------------------------------------------------ rules


def _settings(**kw: Any) -> mk.Settings:
    return mk.Settings(mailbox=MAILBOX, owners=frozenset(OWNERS.split(",")), seat_inbox=SEAT_INBOX, **kw)


def _plan(threads: list[dict[str, Any]], **kw: Any) -> mk.Plan:
    return mk.plan(threads, _settings(**kw), NOW_MS)


def test_a_recovery_pair_is_settled() -> None:
    assert _plan([recovery_pair()]).settled == ["t-settled"]


def test_a_reminder_whose_detail_carries_the_recovery_phrase_is_not_settled() -> None:
    plan = _plan([reminder_with_recovery_phrase()])
    assert plan.settled == []
    assert [a.thread_id for a in plan.open_alerts] == ["t-reminder"]


def test_a_recovery_without_dkim_pass_is_not_settled_and_is_marked_unverified() -> None:
    plan = _plan([spoofed_recovery(), lookalike_dkim_recovery()])
    assert plan.settled == []
    assert {a.thread_id: a.verified for a in plan.open_alerts} == {"t-spoof": False, "t-lookalike": False}


def test_a_thread_the_owner_replied_in_is_listed_not_trashed() -> None:
    plan = _plan([owner_replied_alert()])
    assert plan.settled == []
    assert [a.thread_id for a in plan.open_alerts] == ["t-owner"]


def test_seat_sent_newsletter_replied_and_too_new_are_ignored() -> None:
    plan = _plan([seat_sent(), newsletter(), replied_thread(), too_new_person()])
    assert (plan.settled, plan.open_alerts, plan.never_clears, plan.waiting) == ([], [], [], [])


def test_a_person_waiting_is_listed() -> None:
    plan = _plan([waiting_person()])
    assert [(w.thread_id, w.message_id, w.sender) for w in plan.waiting] == [("t-wait", "w1", "dana@prospect.example")]


def test_an_unrecovered_alert_and_a_reopened_thread_are_open() -> None:
    plan = _plan([unrecovered_alert(), reopened_after_recovery()])
    assert plan.settled == []
    assert {a.thread_id for a in plan.open_alerts} == {"t-open", "t-reopen"}


def test_a_send_refused_alert_never_clears() -> None:
    plan = _plan([send_refused_alert()])
    assert [a.thread_id for a in plan.never_clears] == ["t-refused"]
    assert plan.settled == []


def test_the_state_header_is_required_only_when_authored() -> None:
    assert _plan([recovery_pair()], require_alert_state_header=True).settled == []
    stamped = recovery_pair()
    stamped["messages"][1]["payload"]["headers"].append({"name": "X-SMD-Alert-State", "value": "resolved"})
    assert _plan([stamped], require_alert_state_header=True).settled == ["t-settled"]


def test_the_recovery_paragraph_is_read_from_the_html_the_worker_sends() -> None:
    """The worker's side is pinned by running it: workers/fleet-alerts/src/
    index.test.ts asserts a recovery email's html opens with exactly this
    paragraph. Here: that paragraph, as the worker writes it, parses to it."""
    html = _alert_html("This has cleared.", "Nothing.", "Detail: WHAT HAPPENED: anything")
    assert mk.first_paragraph({"mimeType": "text/html", "body": {"data": _b64(html)}}) == mk.RECOVERY_PARAGRAPH
    plain = "WHAT HAPPENED: This has cleared.\n\nWHAT YOU NEED TO DO: Nothing."
    assert mk.first_paragraph({"mimeType": "text/plain", "body": {"data": _b64(plain)}}) == mk.RECOVERY_PARAGRAPH


def test_no_open_alert_is_ever_trashed(tmp_path: Path, monkeypatch) -> None:
    """THE FALSIFIER. Every fixture through the real verb: only the settled
    pair is trashed. Its control: the settled pair IS trashed, so the check
    can fail."""
    gmail = FakeGmail(_all())
    out = _run(_broker(tmp_path, monkeypatch, gmail, FakeHTTP()), mode="daily")
    assert out["status"] == "done"
    assert gmail.trashed == ["t-settled"]
    assert not set(gmail.trashed) & OPEN_ALERT_THREADS


# ------------------------------------------------------------------ the verb


def test_pagination_reads_every_page(tmp_path: Path, monkeypatch) -> None:
    threads = [recovery_pair(), waiting_person(), unrecovered_alert()]  # 4 messages
    gmail = FakeGmail(threads, page_size=1)  # four pages of one message each
    out = _run(_broker(tmp_path, monkeypatch, gmail, FakeHTTP()), mode="dry_run", uid=0)
    assert out["counts"]["pages"] == 4
    assert [c.get("pageToken", "") for c in gmail.list_calls] == ["", "1", "2", "3"]
    assert out["counts"]["threads"] == 3


def test_pagination_across_three_pages_finds_threads_on_the_last(tmp_path: Path, monkeypatch) -> None:
    threads = [seat_sent(), newsletter(), unrecovered_alert(), waiting_person(), recovery_pair()]  # 6 messages
    gmail = FakeGmail(threads, page_size=2)
    out = _run(_broker(tmp_path, monkeypatch, gmail, FakeHTTP()), mode="dry_run", uid=0)
    assert out["counts"]["pages"] == 3
    assert [d["thread_id"] for d in out["decisions"]["settled"]] == ["t-settled"]


def test_a_failed_read_trashes_and_sends_nothing(tmp_path: Path, monkeypatch) -> None:
    for fail_on in ("list", "get"):
        gmail = FakeGmail(_all(), fail_on=fail_on)
        http = FakeHTTP()
        broker = _broker(tmp_path, monkeypatch, gmail, http)
        out = _run(broker, mode="daily")
        assert out["status"] == "read_failed"
        assert gmail.trashed == [] and http.sends == []
        assert broker.ledger.rows == []
        assert not (tmp_path / "keeper" / "listed.json").exists()


def test_a_full_page_without_a_token_fails_closed(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail(_all(), page_size=10_000)
    monkeypatch.setattr(mkv, "PAGE_SIZE", 3)  # the fake returns everything with no token
    http = FakeHTTP()
    out = _run(_broker(tmp_path, monkeypatch, gmail, http), mode="daily")
    assert (out["status"], out["reason"]) == ("read_failed", "full_page_without_token")
    assert gmail.trashed == [] and http.sends == []


def test_dry_run_decides_and_does_nothing(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail(_all())
    http = FakeHTTP()
    broker = _broker(tmp_path, monkeypatch, gmail, http)
    out = _run(broker, mode="dry_run", uid=0)
    assert out["status"] == "dry_run"
    d = out["decisions"]
    assert [x["thread_id"] for x in d["settled"]] == ["t-settled"]
    assert {x["thread_id"] for x in d["open_alerts"]} == OPEN_ALERT_THREADS - {"t-refused"}
    assert [x["thread_id"] for x in d["never_clears"]] == ["t-refused"]
    assert [(x["thread_id"], x["message_id"]) for x in d["waiting"]] == [("t-wait", "w1")]
    assert {x["rule"] for x in d["open_alerts"]} == {"1b_open_alert"}
    assert gmail.trashed == [] and http.sends == [] and broker.ledger.rows == []
    assert not (tmp_path / "keeper").exists()


def test_the_daily_list_goes_once_to_the_authored_recipient_only(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail(_all())
    http = FakeHTTP()
    broker = _broker(tmp_path, monkeypatch, gmail, http)
    out = _run(broker)  # scheduled: the day's first run sends
    assert (out["status"], out["mode"]) == ("done", "daily")
    assert len(http.sends) == 1
    sent = http.sends[0]
    assert sent["to"] == [RECIPIENT]
    body = sent["text"]
    # Fixed section order.
    order = [
        body.index(h)
        for h in (
            "Waiting on a reply from you (1):",
            "SMD Ops alerts not cleared (6):",
            "do not clear on their own (1):",
            "Trashed 1 settled alerts.",
        )
    ]
    assert order == sorted(order)
    assert "dana@prospect.example" in body and "pat@client.example" not in body
    assert "news@vendor.example" not in body and SEAT_INBOX not in body
    assert "(sender not verified)" in body
    # The send is the audited transmit: intent + dispatched rows, on the skill lane.
    types = [r["action_type"] for r in broker.ledger.rows]
    assert "CONFIRM_SEND_INTENT" in types and "CONFIRM_SEND_DISPATCHED" in types
    dispatched = next(r for r in broker.ledger.rows if r["action_type"] == "CONFIRM_SEND_DISPATCHED")
    assert dispatched["skill_name"] == "mailbox-keeper"
    assert json.loads(dispatched["metadata"])["lane"] == "skill:mailbox-keeper"
    # A second scheduled run the same day only trashes.
    again = _run(broker)
    assert (again["status"], again["mode"]) == ("done", "midday")
    assert len(http.sends) == 1
    # Listed once: a forced daily the same day is refused as already sent.
    assert _run(broker, mode="daily")["status"] == "already_sent_today"


def test_already_listed_items_are_not_listed_again(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail([waiting_person(), unrecovered_alert(), send_refused_alert()])
    http = FakeHTTP()
    broker = _broker(tmp_path, monkeypatch, gmail, http)
    assert _run(broker)["status"] == "done"
    state = json.loads((tmp_path / "keeper" / "listed.json").read_text())
    state["digest_sent_on"] = "2000-01-01"  # a new day
    (tmp_path / "keeper" / "listed.json").write_text(json.dumps(state))
    assert _run(broker)["status"] == "done"
    assert http.sends[1]["text"] == "Nothing waiting on you. Trashed 0 settled alerts."
    assert http.sends[1]["subject"] == "Mailbox: nothing waiting on you"


def test_midday_trashes_only(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail(_all())
    http = FakeHTTP()
    out = _run(_broker(tmp_path, monkeypatch, gmail, http), mode="midday")
    assert out["status"] == "done" and out["counts"]["trashed"] == 1
    assert gmail.trashed == ["t-settled"] and http.sends == []
    log = (tmp_path / "keeper" / "trashed.jsonl").read_text().splitlines()
    assert [json.loads(line)["thread_id"] for line in log] == ["t-settled"]


def test_an_unlisted_skill_does_nothing(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail(_all())
    yaml_text = SEAT_YAML.replace("name: mailbox-keeper", "name: workspace", 1)
    out = _run(_broker(tmp_path, monkeypatch, gmail, FakeHTTP(), yaml_text))
    assert out == {"ok": True, "status": "not_enabled"}
    assert gmail.list_calls == []


def test_no_recipient_authored_trashes_but_sends_nothing(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail(_all())
    http = FakeHTTP()
    yaml_text = SEAT_YAML.replace(f"          digest_recipient: {RECIPIENT}\n", "")
    out = _run(_broker(tmp_path, monkeypatch, gmail, http, yaml_text), mode="daily")
    assert out["status"] == "recipient_unauthored"
    assert http.sends == []


def test_a_recipient_off_the_seat_surface_is_refused_by_the_transmit(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail([waiting_person()])
    http = FakeHTTP()
    yaml_text = SEAT_YAML.replace(f"digest_recipient: {RECIPIENT}", "digest_recipient: someone@outside.example")
    broker = _broker(tmp_path, monkeypatch, gmail, http, yaml_text)
    out = _run(broker, mode="daily")
    assert out["status"] == "digest_failed"
    assert http.sends == []
    assert not (tmp_path / "keeper" / "listed.json").exists()


def test_the_gateway_pid_alone_is_not_enough(tmp_path: Path, monkeypatch) -> None:
    """ROOT_OR_AGENT: a peer that is neither the agent uid nor root is refused
    before the handler reads anything."""
    gmail = FakeGmail(_all())
    broker = _broker(tmp_path, monkeypatch, gmail, FakeHTTP())
    with pytest.raises(PermissionError):
        broker.handle({"action": "mailbox_keeper_run", "mode": "dry_run"}, peer_pid=GATEWAY_PID, peer_uid=4242)
    assert gmail.list_calls == []


def test_an_unknown_mode_is_refused(tmp_path: Path, monkeypatch) -> None:
    broker = _broker(tmp_path, monkeypatch, FakeGmail([]), FakeHTTP())
    with pytest.raises(ValueError, match="mode"):
        _run(broker, mode="send_everything")


def test_settings_refuse_a_missing_mailbox_and_bad_numbers() -> None:
    with pytest.raises(mk.SettingsError, match="mailbox"):
        mk.settings_from({})
    with pytest.raises(mk.SettingsError, match="needs_you_after_hours"):
        mk.settings_from({"mailbox": MAILBOX, "needs_you_after_hours": "soon"})
    with pytest.raises(mk.SettingsError, match="require_alert_state_header"):
        mk.settings_from({"mailbox": MAILBOX, "require_alert_state_header": "yes"})
    s = mk.settings_from({"mailbox": MAILBOX}, seat_inbox=SEAT_INBOX)
    assert (s.owners, s.digest_recipient, s.needs_you_after_hours, s.window_days, s.require_alert_state_header) == (
        frozenset({MAILBOX}),
        "",
        24.0,
        14,
        False,
    )
    assert s.seat_inbox == SEAT_INBOX


def test_dkim_pass_reads_only_the_mailbox_verdict() -> None:
    assert mk.dkim_pass(GOOD_AUTH, "smd.services")
    assert not mk.dkim_pass("evil.example; dkim=pass header.i=@smd.services", "smd.services")
    assert not mk.dkim_pass("mx.google.com; dkim=pass header.i=@smd.services.evil.example", "smd.services")
    assert not mk.dkim_pass("mx.google.com; dkim=fail header.i=@smd.services", "smd.services")
