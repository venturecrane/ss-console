"""The mailbox keeper (mailbox_keeper.py, mailbox_keeper_verbs.py), end to end.

Every test drives the REAL verb through the real verb table on a real
``Broker``: the real ``WorkspaceOperations`` over a fake Gmail service, and the
real ``AgentMailOps`` over a fake HTTP opener, so the digest goes through the
same fenced, audited transmit the seat uses. Only the vendors are fake
(``mailbox_keeper_fakes.py``). The model-reading half is
``test_mailbox_keeper_classify.py``.

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

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mailbox_keeper_fakes as fk
from mailbox_keeper_fakes import (
    ALERT_FROM,
    GOOD_AUTH,
    MAILBOX,
    NO_DKIM,
    NOW_MS,
    OWNERS,
    RECIPIENT,
    SEAT_INBOX,
    UUID1,
    FakeAgentMail,
    FakeGmail,
    msg,
)
from workspace_broker import mailbox_keeper as mk
from workspace_broker import mailbox_keeper_mail as mail
from workspace_broker.server import Broker

GATEWAY_PID = fk.GATEWAY_PID
SEAT_YAML = fk.seat_yaml(schedule="47 * * * *")
LABELLED_YAML = fk.seat_yaml("          alert_label: 'SMD Ops'")
_alert_html = fk.alert_html
_b64 = fk.b64


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
        f"Scott <{RECIPIENT}>",
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
            msg("p2", "t-replied", 49, f"Scott <{MAILBOX}>", "Re: Question", text="Yes.", labels=("SENT",)),
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


# ------------------------------------------------------------------ the broker


FakeHTTP = FakeAgentMail


def _broker(tmp_path: Path, monkeypatch, gmail: FakeGmail, http: FakeAgentMail, yaml_text: str = SEAT_YAML) -> Broker:
    return fk.build_broker(tmp_path, monkeypatch, gmail, http, yaml_text)


def _run(broker: Broker, mode: str = "scheduled", uid: int = fk.AGENT_UID) -> dict[str, Any]:
    return fk.run(broker, mode, uid)


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
    monkeypatch.setattr(mail, "PAGE_SIZE", 3)  # the fake returns everything with no token
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
    out = _run(broker)  # scheduled: the day's first run at or after send_after_hour sends
    assert (out["status"], out["mode"], out["classify"]) == ("done", "daily", "off")
    assert len(http.sends) == 1
    sent = http.sends[0]
    assert sent["to"] == [RECIPIENT]
    assert sent["subject"] == "Mailbox: 1 needs you"
    body = sent["text"]
    # Fixed section order: what needs him, then what happened to SMD Ops mail.
    order = [body.index(h) for h in ("Needs you (1):", "SMD Ops: 1 settled alerts trashed, 4 left in the inbox")]
    assert order == sorted(order)
    assert "dana@prospect.example" in body and "pat@client.example" not in body
    assert "news@vendor.example" not in body and SEAT_INBOX not in body
    # No alert_label authored: SMD Ops mail stays in the inbox and is not listed.
    assert "[SMD Ops]" not in body
    # The send is the audited transmit: intent + dispatched rows, on the skill lane.
    types = [r["action_type"] for r in broker.ledger.rows]
    assert "CONFIRM_SEND_INTENT" in types and "CONFIRM_SEND_DISPATCHED" in types
    dispatched = next(r for r in broker.ledger.rows if r["action_type"] == "CONFIRM_SEND_DISPATCHED")
    assert dispatched["skill_name"] == "mailbox-keeper"
    assert json.loads(dispatched["metadata"])["lane"] == "skill:mailbox-keeper"
    # A second scheduled run the same day is the hourly pass only.
    again = _run(broker)
    assert (again["status"], again["mode"]) == ("done", "hourly")
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
    http.sends.clear()  # the AgentMail listing holds today's first list; a new day would not
    assert _run(broker)["status"] == "done"
    assert http.sends[0]["subject"] == "Mailbox: nothing needs you"
    assert http.sends[0]["text"].startswith("Nothing needs you.\n\nStill open from earlier: 1.\n")


def test_hourly_trashes_and_labels_only(tmp_path: Path, monkeypatch) -> None:
    gmail = FakeGmail(_all())
    http = FakeHTTP()
    out = _run(_broker(tmp_path, monkeypatch, gmail, http, LABELLED_YAML), mode="hourly")
    assert out["status"] == "done" and out["counts"]["trashed"] == 1
    assert gmail.trashed == ["t-settled"] and http.sends == []
    log = (tmp_path / "keeper" / "trashed.jsonl").read_text().splitlines()
    assert [json.loads(line)["thread_id"] for line in log] == ["t-settled"]
    # Verified alerts leave the inbox under the label; the spoofed, the
    # lookalike and the one he replied in stay.
    assert {
        t for t in gmail.by_id if t not in gmail.trashed and not gmail.inbox(t) and "SMD Ops" in gmail.label_names(t)
    } == {
        "t-reminder",
        "t-open",
        "t-refused",
        "t-reopen",
    }
    assert all(gmail.inbox(t) for t in ("t-spoof", "t-lookalike", "t-owner", "t-wait", "t-news"))
    # The hourly read asks only for SMD Ops mail.
    assert all("from:team@smd.services" in c["q"] for c in gmail.list_calls)


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
    # The SMD Ops record is written before anything leaves the inbox; the day's
    # list is not marked sent.
    assert json.loads((tmp_path / "keeper" / "listed.json").read_text())["digest_sent_on"] == ""


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


def test_no_reply_anywhere_in_the_local_part_is_automated_and_watch_senders_override() -> None:
    # 2026-11-15 smd-office dry run: anchored matching listed these as people.
    s = mk.settings_from({"mailbox": "me@example.com", "watch_senders": "@taxoffice.example"})
    for addr in ("microsoft-noreply@microsoft.com", "donotreplytpt9@other.gov", "billing-no-reply@x.example"):
        assert not mk.is_person(_person_msg(addr), s), addr
    assert mk.is_person(_person_msg("donotreplytpt1@taxoffice.example"), s)
    assert mk.is_person(_person_msg("dana@prospect.example"), s)


def _person_msg(addr: str) -> mk.Message:
    return mk.Message(
        id="m1",
        thread_id="t1",
        internal_ms=0,
        labels=frozenset({"INBOX"}),
        sender=addr,
        sender_name="",
        subject="s",
        in_reply_to="",
        references="",
        auth_results="",
        list_unsubscribe=False,
        bulk=False,
        alert_state="",
        first_paragraph="",
    )
