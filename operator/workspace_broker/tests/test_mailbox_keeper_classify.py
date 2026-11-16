"""The mailbox keeper's decisions list, through the REAL verb.

The real ``Broker``, ``WorkspaceOperations``, ``AgentMailOps`` and
``model_call.call``; only the vendors are fake (``mailbox_keeper_fakes.py``:
Gmail's client, AgentMail's HTTP, and the Messages API's answer per thread).

Each test names the plan item it defends (Verification 1 of the 2026-11-16
plan): send-once across machines, the jitter seam, SMD Ops labelling and the
needs-you subject table, the code floors, the fence in fractional cents, draft
threading and the DRAFT-label block, the act-phase skip on a changed thread,
ledgers only after the send, and the still-open count.
"""

from __future__ import annotations

import email.utils
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mailbox_keeper_fakes as fk
from mailbox_keeper_fakes import ALIAS, MAILBOX, RECIPIENT, SEAT_INBOX, FakeAgentMail, FakeGmail, FakeModel, msg
from workspace_broker import mailbox_keeper as mk
from workspace_broker import mailbox_keeper_classify as mkc
from workspace_broker import mailbox_keeper_mail as mail
from workspace_broker import mailbox_keeper_ops as mko

YAML = fk.seat_yaml(fk.CLASSIFY_SETTINGS)
YAML_OFF = YAML.replace("classify: true", "classify: false")


# ------------------------------------------------------------------ threads


def person() -> dict[str, Any]:
    return fk.thread(
        "t-person",
        msg(
            "p1",
            "t-person",
            30,
            "Lee Holt <lee@prospect.example>",
            "Kickoff date",
            text="Can we start on the 18th? Our budget is $4,000.\n\nOn Mon, someone wrote:\n> old quoted text 999",
            headers=(("Reply-To", "lee.office@prospect.example"),),
            to=ALIAS,
        ),
    )


def receipt() -> dict[str, Any]:
    body = "Thanks for your payment. Amount paid: $12.50 USD. Invoice 4411."
    return fk.thread(
        "t-receipt",
        msg(
            "r1",
            "t-receipt",
            20,
            "Vendor Billing <billing@vendor.example>",
            "Your receipt",
            text=body,
            headers=(("Precedence", "bulk"),),
        ),
    )


def bad_receipt() -> dict[str, Any]:
    body = "Your plan renews soon."
    return fk.thread(
        "t-badrcpt",
        msg(
            "b1",
            "t-badrcpt",
            20,
            "Cloud Co <billing@cloud.example>",
            "Renewal",
            text=body,
            headers=(("Precedence", "bulk"),),
        ),
    )


def notice() -> dict[str, Any]:
    return fk.thread(
        "t-notice",
        msg(
            "n1",
            "t-notice",
            10,
            "Registry <news@registry.example>",
            "Enabling 2FA",
            text="We now support 2FA.",
            headers=(("List-Unsubscribe", "<mailto:u@registry.example>"),),
        ),
    )


def tax() -> dict[str, Any]:
    return fk.thread(
        "t-tax",
        msg(
            "x1",
            "t-tax",
            10,
            "Dept of Revenue <noreply@tax.example>",
            "Filing reminder",
            text="Your filing is due.",
            headers=(("Precedence", "bulk"),),
        ),
    )


def answered() -> dict[str, Any]:
    # He wrote in it; the newest message is an automated follow-up.
    return fk.thread(
        "t-answered",
        msg("a1", "t-answered", 50, "Kim <kim@partner.example>", "Contract", text="Please sign."),
        msg("a2", "t-answered", 49, f"Scott <{MAILBOX}>", "Re: Contract", text="Signed.", labels=("SENT",)),
        msg(
            "a3",
            "t-answered",
            5,
            "Docs Bot <noreply@docs.example>",
            "Re: Contract",
            text="Signed by all parties.",
            headers=(("Precedence", "bulk"),),
        ),
    )


def figure() -> dict[str, Any]:
    return fk.thread(
        "t-figure",
        msg(
            "f1",
            "t-figure",
            10,
            "Promo <promo@shop.example>",
            "Big sale",
            text="Sale this week.",
            headers=(("Precedence", "bulk"),),
        ),
    )


def triage(n: int, mid: str = "o1", tid: str = "t-triage") -> dict[str, Any]:
    # ops-triage: the agent's first "Subject: " line, "[SMD Ops] Triage: <H> handled, <N> need you" (TRIAGE.md).
    return fk.thread(tid, fk.ops_msg(mid, tid, 3, f"[SMD Ops] Triage: 4 handled, {n} need you"))


def card() -> dict[str, Any]:
    # request-card-email.ts cardSubject: `[SMD Ops] ${slug}: ${headline}: ${who}: ${excerpt}`.
    return fk.thread("t-card", fk.ops_msg("c1", "t-card", 2, "[SMD Ops] example-seat: answered in 4m: Pat: Question"))


def seat_digest() -> dict[str, Any]:
    return fk.thread(
        "t-digest",
        msg(
            "d1",
            "t-digest",
            20,
            f"SMD Operations <{SEAT_INBOX}>",
            "Mailbox: nothing needs you",
            text="Nothing needs you.",
        ),
    )


ANSWERS: dict[str, Any] = {
    "Kickoff date": {
        "kind": "decision",
        "what": "Lee asks to start on the 18th with a $4,000 budget.",
        "recommend": "Confirm the 18th works",
        "reply_needed": True,
        "draft": "Hi Lee, the 18th works for us.",
    },
    "Your receipt": {"kind": "receipt", "what": "Payment receipt from Vendor.", "amount": "$12.50 USD"},
    "Renewal": {"kind": "receipt", "what": "Cloud Co plan renewal.", "amount": "$99.00"},
    "Enabling 2FA": {"kind": "notice", "what": "Registry now supports 2FA."},
    "Filing reminder": {"kind": "notice", "what": "A filing reminder."},
    "Re: Contract": {"kind": "notice", "what": "The contract is signed."},
    "Big sale": {"kind": "decision", "what": "A 40% off sale.", "recommend": "Skip it.", "reply_needed": False},
}


def _broker(tmp_path, monkeypatch, threads, *, yaml_text=YAML, answers=ANSWERS, http=None, **model_kw):
    gmail = FakeGmail(threads, page_size=100)
    http = http or FakeAgentMail()
    model = FakeModel(answers, **model_kw)
    broker = fk.build_broker(tmp_path, monkeypatch, gmail, http, yaml_text, model=model)
    return broker, gmail, http, model


def _all_threads():
    return [person(), receipt(), bad_receipt(), notice(), tax(), answered(), figure(), triage(2), card(), seat_digest()]


# ------------------------------------------------------------------ the list


def test_the_morning_list_brings_decisions_files_receipts_and_archives_notices(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, _all_threads())
    out = fk.run(broker)
    assert (out["status"], out["mode"], out["classify"]) == ("done", "daily", "ok")
    # SMD Ops mail and the seat's own list never reach the model.
    read = {
        next(line for line in r["messages"][0]["content"].splitlines() if line.startswith("SUBJECT: "))
        for r in model.requests
    }
    assert read == {
        f"SUBJECT: {s}"
        for s in (
            "Kickoff date",
            "Your receipt",
            "Renewal",
            "Enabling 2FA",
            "Filing reminder",
            "Re: Contract",
            "Big sale",
        )
    }
    # No tools, thinking off, the authored model.
    assert all(
        "tools" not in r and r["thinking"] == {"type": "between_tools"} and r["model"] == "claude-sonnet-5-5"
        for r in model.requests
    )
    (sent,) = http.sends
    body = sent["text"]
    assert sent["subject"] == "Mailbox: 5 need you"
    assert "1. Lee Holt (prospect.example): Lee asks to start on the 18th with a $4,000 budget." in body
    assert "   Recommend: Confirm the 18th works. Draft is in the thread." in body
    assert "Triage says 2 need you (SMD Ops label)." in body
    assert "Filed: receipts Vendor Billing $12.50 USD (label Receipts)." in body
    assert 'Archived: Registry "Enabling 2FA".' in body
    assert "Could not read 1, left in inbox: Promo (shop.example): Big sale." in body
    # Receipt filed under Receipts and out of the inbox; notice archived.
    assert "Receipts" in gmail.label_names("t-receipt") and not gmail.inbox("t-receipt")
    assert not gmail.inbox("t-notice")
    # SMD Ops filed under its label, the card included, without a needs-you line.
    assert not gmail.inbox("t-card") and "SMD Ops" in gmail.label_names("t-card")
    assert "answered in 4m" not in body
    # The expense ledger, keyed by thread id, with the amount as written.
    (row,) = [json.loads(x) for x in (tmp_path / "keeper" / "expenses.jsonl").read_text().splitlines()]
    assert (row["thread_id"], row["vendor"], row["amount"], row["cents"], row["currency"]) == (
        "t-receipt",
        "Vendor Billing",
        "$12.50 USD",
        1250,
        "USD",
    )


def test_code_floors_bound_the_models_value(tmp_path, monkeypatch) -> None:
    """Person, watch_senders, answered, receipt amount, unsupported figure."""
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, _all_threads())
    out = fk.run(broker, mode="dry_run", classify=True)
    rows = {r["thread_id"]: r for r in out["classified"]}
    # A watch_senders match the model called a notice is a decision, never archived.
    assert (rows["t-tax"]["kind"], rows["t-tax"]["floor"]) == ("decision", "watch_sender")
    # He wrote in the thread: clamped, never archived on the model's word.
    assert (rows["t-answered"]["kind"], rows["t-answered"]["floor"]) == ("decision", "owner_wrote")
    # A receipt whose amount is not in the email is a decision.
    assert (rows["t-badrcpt"]["kind"], rows["t-badrcpt"]["floor"]) == ("decision", "receipt_unverified")
    # "40%" is not in the thread: could not read.
    assert (rows["t-figure"]["kind"], rows["t-figure"]["reason"]) == ("unread", "unsupported_figure")
    # The verified receipt keeps its amount.
    assert (rows["t-receipt"]["kind"], rows["t-receipt"]["cents"]) == ("receipt", 1250)


def test_a_person_is_never_archived_on_the_models_word(tmp_path, monkeypatch) -> None:
    answers = {**ANSWERS, "Kickoff date": {"kind": "notice", "what": "A note from Lee."}}
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, [person()], answers=answers)
    assert fk.run(broker)["status"] == "done"
    assert gmail.inbox("t-person")
    assert "Lee Holt (prospect.example): A note from Lee." in http.sends[0]["text"]


def test_an_invalid_answer_is_could_not_read_and_left_in_the_inbox(tmp_path, monkeypatch) -> None:
    answers = {
        "Enabling 2FA": "Sure! This is a notice.",
        "Your receipt": {"kind": "receipt", "what": "x", "amount": "$12.50 USD", "extra": 1},
    }
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, [notice(), receipt()], answers=answers)
    assert fk.run(broker)["status"] == "done"
    assert gmail.inbox("t-notice") and gmail.inbox("t-receipt")
    assert "Could not read 2, left in inbox:" in http.sends[0]["text"]
    assert not json.loads((tmp_path / "keeper" / "classified.json").read_text())


def test_the_fence_sums_fractional_cents(tmp_path, monkeypatch) -> None:
    """3,000 in and 600 out at 200/1000 cents per million is 1.2 cents a call.
    Rounded up per call (2 cents), a 5-cent fence would stop after 2 calls; in
    fractional cents it allows 4 (4.8)."""
    threads = [
        fk.thread(
            f"t-n{i}",
            msg(
                f"n{i}",
                f"t-n{i}",
                5,
                f"News {i} <n{i}@news.example>",
                f"Issue {i}",
                text="Hello.",
                headers=(("Precedence", "bulk"),),
            ),
        )
        for i in range(6)
    ]
    yaml_text = YAML.replace("cost_fence_cents: 150", "cost_fence_cents: 5")
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, threads, yaml_text=yaml_text, answers={})
    out = fk.run(broker)
    assert len(model.requests) == 4
    assert (out["classify"], out["counts"]["unread"], out["counts"]["cost_cents"]) == ("fence", 2, 5)
    assert "Reading stopped early (fence)." in http.sends[0]["text"]
    assert json.loads((tmp_path / "keeper" / "spend.json").read_text())["cents"] == 4.8


def test_the_daily_spend_ledger_caps_dry_runs(tmp_path, monkeypatch) -> None:
    broker, _, _, model = _broker(tmp_path, monkeypatch, [notice()])
    (tmp_path / "keeper").mkdir()
    today = json.loads(
        json.dumps(
            __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("America/Phoenix")).strftime("%Y-%m-%d")
        )
    )
    (tmp_path / "keeper" / "spend.json").write_text(json.dumps({"date": today, "cents": 300}))
    out = fk.run(broker, mode="dry_run", classify=True)
    assert model.requests == [] and out["classify"] == "fence"


# ------------------------------------------------------------------ drafts


def test_a_reply_draft_threads_and_signs_and_goes_to_reply_to(tmp_path, monkeypatch) -> None:
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, [person()])
    assert fk.run(broker)["counts"]["drafts"] == 1
    (draft,) = gmail.made_drafts
    h = draft["headers"]
    assert h["To"] == "lee.office@prospect.example"
    assert h["From"] == ALIAS  # received at the authored send-as
    assert h["Subject"] == "Re: Kickoff date"
    assert h["In-Reply-To"] == "<0100p1@email.example>" and h["References"] == "<0100p1@email.example>"
    assert draft["thread_id"] == "t-person"
    assert draft["body"].strip().endswith("the 18th works for us.\n\nThanks,\nScott")


def test_any_draft_in_the_thread_blocks_a_new_one(tmp_path, monkeypatch) -> None:
    t = person()
    t["messages"].append(
        msg("dr1", "t-person", 1, f"Scott <{MAILBOX}>", "Re: Kickoff date", text="draft", labels=("DRAFT",))
    )
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, [t])
    assert fk.run(broker)["counts"]["drafts"] == 0
    assert gmail.made_drafts == []
    assert "A draft is already in the thread." in http.sends[0]["text"]


def test_a_thread_that_changed_is_not_filed_or_drafted(tmp_path, monkeypatch) -> None:
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, [person(), notice()])
    reads: dict[str, int] = {}

    def arrive(g, tid):  # a new message lands between the read and the act
        reads[tid] = reads.get(tid, 0) + 1
        if reads[tid] == 2:
            g.by_id[tid]["messages"].append(
                msg(f"{tid}-late", tid, 0, "Lee Holt <lee@prospect.example>", "Re: late", text="One more thing.")
            )

    gmail.on_get = arrive
    out = fk.run(broker)
    assert out["status"] == "done"
    assert gmail.made_drafts == [] and gmail.inbox("t-notice")
    assert out["counts"]["file_skipped"] == 1
    # Not recorded as read: the next list reads it again.
    assert "t-notice" not in json.loads((tmp_path / "keeper" / "classified.json").read_text())


# ------------------------------------------------------------------ send once


def test_a_list_another_machine_sent_today_stops_this_one(tmp_path, monkeypatch) -> None:
    http = FakeAgentMail()
    now = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
    http.elsewhere = [
        {"message_id": "m0", "labels": ["sent"], "timestamp": now, "to": [RECIPIENT], "subject": "Mailbox: 2 need you"}
    ]
    broker, gmail, _, model = _broker(tmp_path, monkeypatch, [notice(), person()], http=http)
    out = fk.run(broker)
    assert out["status"] == "already_sent_today" and http.sends == []
    assert model.requests == [] and gmail.made_drafts == []  # asked before any model spend
    # Nothing filed, no ledger: the other machine's list owns today.
    assert gmail.inbox("t-notice") and not (tmp_path / "keeper" / "classified.json").exists()
    (q,) = http.listings
    assert (
        q["labels"] == ["sent"]
        and q["to"] == [RECIPIENT]
        and q["subject"] == ["Mailbox:"]
        and q["after"][0].endswith("Z")
    )


def test_a_list_sent_elsewhere_while_this_one_read_stops_the_send(tmp_path, monkeypatch) -> None:
    http = FakeAgentMail()
    broker, gmail, _, model = _broker(tmp_path, monkeypatch, [notice()], http=http)
    now = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
    real = model.__call__

    def racing(*args):
        http.elsewhere = [
            {
                "message_id": "m0",
                "labels": ["sent"],
                "timestamp": now,
                "to": [RECIPIENT],
                "subject": "Mailbox: 1 needs you",
            }
        ]
        return real(*args)

    monkeypatch.setattr(fk.model_call, "https_post", racing)
    out = fk.run(broker)
    assert out["status"] == "already_sent_today" and http.sends == [] and len(http.listings) == 2
    assert gmail.inbox("t-notice") and not (tmp_path / "keeper" / "classified.json").exists()


def test_a_failed_sent_lookup_refuses_the_send(tmp_path, monkeypatch) -> None:
    http = FakeAgentMail()
    http.fail_listing = True
    broker, gmail, _, _ = _broker(tmp_path, monkeypatch, [notice()], http=http)
    out = fk.run(broker)
    assert out["status"] == "send_check_failed" and http.sends == []
    assert gmail.inbox("t-notice")
    assert not (tmp_path / "keeper" / "classified.json").exists()
    assert not (tmp_path / "keeper" / "expenses.jsonl").exists()


def test_the_jitter_is_per_machine_and_waited_before_the_lookup(tmp_path, monkeypatch) -> None:
    assert mail.jitter_seconds("") == 0
    a, b = mail.jitter_seconds("3d8d9e1f2a7b48"), mail.jitter_seconds("e784079b449483")
    assert 0 <= a < mail.JITTER_SECONDS and 0 <= b < mail.JITTER_SECONDS and a != b
    assert mail.jitter_seconds("3d8d9e1f2a7b48") == a  # stable across processes (sha256, not hash())
    broker, _, http, _ = _broker(tmp_path, monkeypatch, [notice()])
    waits: list[float] = []
    monkeypatch.setattr(mail, "SLEEP", lambda s: waits.append(s) or None)
    monkeypatch.setenv("FLY_MACHINE_ID", "3d8d9e1f2a7b48")
    fk.run(broker)
    # Waited once, before the first of the two lookups (before the reading, and
    # again just before the send).
    assert waits == [a] and len(http.listings) == 2


def test_ledgers_are_written_only_after_the_send(tmp_path, monkeypatch) -> None:
    yaml_text = YAML.replace(f"digest_recipient: {RECIPIENT}", "digest_recipient: someone@outside.example")
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, [receipt(), notice()], yaml_text=yaml_text)
    out = fk.run(broker)
    assert out["status"] == "digest_failed"
    assert gmail.inbox("t-receipt") and gmail.inbox("t-notice")
    assert not (tmp_path / "keeper" / "expenses.jsonl").exists()
    assert not (tmp_path / "keeper" / "classified.json").exists()
    assert json.loads((tmp_path / "keeper" / "listed.json").read_text())["digest_sent_on"] == ""


# ------------------------------------------------------------------ SMD Ops


def test_hourly_labels_smd_ops_and_the_next_list_names_what_needs_him(tmp_path, monkeypatch) -> None:
    yaml_text = YAML.replace("send_after_hour: 0", "send_after_hour: 23")
    broker, gmail, http, model = _broker(
        tmp_path, monkeypatch, [triage(3), triage(0, "o2", "t-quiet"), card(), person()], yaml_text=yaml_text
    )
    out = fk.run(broker, mode="hourly")
    assert (out["status"], out["counts"]["labelled"], out["counts"]["needs_you"]) == ("done", 3, 1)
    assert not any(gmail.inbox(t) for t in ("t-triage", "t-quiet", "t-card")) and gmail.inbox("t-person")
    assert model.requests == [] and http.sends == []
    # The first labelling run also files older SMD Ops mail, once.
    assert any("newer_than" not in c["q"] for c in gmail.list_calls)
    calls = len(gmail.list_calls)
    fk.run(broker, mode="hourly")
    assert all("newer_than" in c["q"] for c in gmail.list_calls[calls:])
    # The day's list names it although it left the inbox hours ago, and only once.
    fk.run(broker, mode="daily")
    assert "Triage says 3 need you (SMD Ops label)." in http.sends[0]["text"]
    state = json.loads((tmp_path / "keeper" / "listed.json").read_text())
    assert state["ops_pending"] == [] and "o1" in state["ops_listed"]


def test_the_needs_you_table_reads_each_emitters_subject() -> None:
    """Each subject built the way its emitter builds it (file named per row)."""
    yes = {
        # operator/ops-machine/TRIAGE.md, ops-triage
        "[SMD Ops] Triage: 3 handled, 2 need you": "triage",
        "[SMD Ops] Triage: 0 handled, 1 needs you": "triage",
        "[SMD Ops] Triage: report (format not followed)": "triage_unreadable",
        # src/lib/operator/request-card-email.ts cardHeadline + cardSubject
        "[SMD Ops] example-seat: NO REPLY after 30m: Chris Doe: Re: the filing": "no_reply",
        "[SMD Ops] example-seat: NEW KIND OF ASK, job waiting: demand: Chris Doe: draft it": "new_kind_of_ask",
        "[SMD Ops] example-seat: NEW KIND OF ASK, first use: records: Chris Doe: pull them": "new_kind_of_ask",
        # operator/bin/lib/work_health_auth.py
        "[SMD Ops] example-seat: the Smokeball sign-in is dead": "sign_in_dead",
        # workers/fleet-alerts/src/alert-words.ts + transition-email.ts (opened, reminder)
        "[SMD Ops] example-seat: the Clio sign-in is about to expire": "sign_in_expiring",
        "Re: [SMD Ops] example-seat: the Clio sign-in is about to expire": "sign_in_expiring",
        "[SMD Ops] example-seat: still open: the Clio sign-in is about to expire": "sign_in_expiring",
        # workers/cost-anomaly/src/notify.ts
        "[SMD Ops] 1 cost anomaly on 2026-11-16": "cost_anomaly",
        "[SMD Ops] 3 cost anomalies on 2026-11-16": "cost_anomaly",
        # mailbox_keeper.NEVER_CLEARS (send-refused.ts, shortfalls.ts): no recovery email
        "[SMD Ops] example-seat: a reply did not go out": "never_clears",
        "[SMD Ops] example-seat: a scheduled message did not go out": "never_clears",
        "[SMD Ops] example-seat: a limit stopped the work": "never_clears",
    }
    for subject, kind in yes.items():
        found = mko.needs_you(subject, "WHAT HAPPENED: something.")
        assert found and found[0] == kind, subject
    no = [
        "[SMD Ops] Triage: 5 handled, 0 need you",
        "[SMD Ops] example-seat: answered in 4m: Chris Doe: Question",
        "[SMD Ops] example-seat: the Operator may be down",
        "[SMD Ops] Weekly: what a check stopped the Operator from doing (3)",
        "[SMD Ops] example-seat: cleared: the Clio sign-in is about to expire",
    ]
    for subject in no:
        assert mko.needs_you(subject, "WHAT HAPPENED: something.") is None, subject
    # A recovery never needs him, whatever its subject.
    assert (
        mko.needs_you("Re: [SMD Ops] example-seat: the Clio sign-in is about to expire", mk.RECOVERY_PARAGRAPH) is None
    )
    assert mko.needs_you("[SMD Ops] Triage: 1 handled, 2 need you", "x") == ("triage", "Triage says 2 need you")


# ------------------------------------------------------------------ still open, off, dry run


def test_a_receipt_that_could_not_be_labelled_stays_and_is_read_again(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, [receipt()])
    gmail.fail_on = "label_create"
    out = fk.run(broker)
    assert out["status"] == "done"
    assert gmail.inbox("t-receipt")  # not filed
    classified_path = tmp_path / "keeper" / "classified.json"
    classified = json.loads(classified_path.read_text()) if classified_path.exists() else {}
    assert "t-receipt" not in classified  # so the next run reads it again
    gmail.fail_on = ""
    state_path = tmp_path / "keeper" / "listed.json"
    state = json.loads(state_path.read_text())
    state["digest_sent_on"] = "2000-01-01"
    state_path.write_text(json.dumps(state))
    http.sends.clear()
    calls = len(model.requests)
    assert fk.run(broker)["status"] == "done"
    assert len(model.requests) == calls + 1
    assert not gmail.inbox("t-receipt") and "Receipts" in gmail.label_names("t-receipt")


def test_still_open_counts_decisions_listed_before(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, [person(), notice()])
    assert fk.run(broker)["status"] == "done"
    state_path = tmp_path / "keeper" / "listed.json"
    state = json.loads(state_path.read_text())
    state["digest_sent_on"] = "2000-01-01"
    state_path.write_text(json.dumps(state))
    http.sends.clear()
    calls = len(model.requests)
    assert fk.run(broker)["status"] == "done"
    assert len(model.requests) == calls  # nothing new to read
    text = http.sends[0]["text"]
    assert text.startswith("Nothing needs you.\n\nStill open from earlier: 1 (first listed ")


def test_a_meter_refusal_reads_nothing_and_lists_people_by_rule(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, [person(), notice()])
    out = fk.run(broker, model="refused")
    assert (out["status"], out["classify"]) == ("done", "refused_by_meter")
    assert model.requests == [] and gmail.inbox("t-notice")
    assert "Lee Holt <lee@prospect.example>: Kickoff date (waiting since" in http.sends[0]["text"]


def test_classify_off_never_calls_the_model(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, [person(), notice(), triage(1)], yaml_text=YAML_OFF)
    out = fk.run(broker)
    assert (out["status"], out["classify"]) == ("done", "off")
    assert model.requests == [] and gmail.inbox("t-notice") and not gmail.inbox("t-triage")
    assert "Triage says 1 need you (SMD Ops label)." in http.sends[0]["text"]


def test_dry_run_with_classify_returns_each_thread_and_acts_on_nothing(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, _all_threads(), yaml_text=YAML_OFF)
    out = fk.run(broker, mode="dry_run", classify=True)
    assert out["status"] == "dry_run" and out["classify"] == "ok"
    row = next(r for r in out["classified"] if r["thread_id"] == "t-person")
    assert (
        row["kind"] == "decision" and row["draft"].startswith("Hi Lee") and row["recommend"] == "Confirm the 18th works"
    )
    assert out["ops"]["needs_you"] == ["Triage says 2 need you"]
    assert gmail.modified == [] and gmail.made_drafts == [] and gmail.trashed == [] and http.sends == []
    assert sorted(p.name for p in (tmp_path / "keeper").iterdir()) == ["spend.json"]
    assert out["counts"]["cost_cents"] == 9 and out["cost_cents_exact"] == 8.4


def test_a_dry_run_without_classify_spends_nothing(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, _all_threads())
    out = fk.run(broker, mode="dry_run")
    assert "classified" not in out and model.requests == []


# ------------------------------------------------------------------ pure pieces


def test_quoted_history_is_stripped_and_the_newest_kept_under_the_cap() -> None:
    assert mkc.strip_quoted("Yes.\n\nOn Mon, Jan 5, Lee wrote:\n> hi\n> there") == "Yes."
    s = mk.settings_from({"mailbox": MAILBOX})
    long = [
        mk.parse_message(msg(f"m{i}", "t", 50 - i, "Lee <lee@prospect.example>", "s", text=f"{i} " + "x" * 3000))
        for i in range(5)
    ]
    text = mkc.thread_text(long, s.timezone)
    assert len(text) <= mkc.THREAD_CHARS + 40 and "4 xxx" in text and "0 xxx" not in text


def test_amounts_parse_to_cents_and_currency() -> None:
    assert mkc.parse_amount("$12.50 USD") == (1250, "USD")
    assert mkc.parse_amount("$1,234.56") == (123456, "USD")
    assert mkc.parse_amount("EUR 9") == (900, "EUR")
    assert mkc.parse_amount("42.00 GBP") == (4200, "GBP")
    assert mkc.parse_amount("about 12") is None and mkc.parse_amount("12.50") is None


def test_the_schema_is_strict() -> None:
    assert mkc.validate({"kind": "notice", "what": "x"}) == {"kind": "notice", "what": "x"}
    # A notice's stray fields are dropped, never acted on.
    assert mkc.validate({"kind": "notice", "what": "x", "recommend": "y"}) == {"kind": "notice", "what": "x"}
    assert mkc.validate({"kind": "decision", "what": "x", "recommend": "y", "reply_needed": True}) == "schema:draft"
    # Too long is cut at a word by code, not refused.
    long = mkc.validate({"kind": "decision", "what": "word " * 60, "recommend": "y", "reply_needed": False})
    assert isinstance(long, dict) and len(long["what"]) <= mkc.WHAT_CHARS and long["what"].endswith("...")
    assert mkc.validate({"kind": "receipt", "what": "x", "amount": "$1"})["amount"] == "$1"
    assert mkc.validate({"kind": "answered", "what": "x"}) == "schema:kind"
    assert mkc.validate({"kind": "notice", "what": "x", "mood": "y"}) == "schema:extra_key"


def test_a_notices_own_words_are_not_figure_checked(tmp_path, monkeypatch) -> None:
    """A notice is listed by sender and subject; its what is never shown."""
    answers = {**ANSWERS, "Enabling 2FA": {"kind": "notice", "what": "Security setup, 2 steps, due 12/1."}}
    broker, *_ = _broker(tmp_path, monkeypatch, [notice()], answers=answers)
    out = fk.run(broker, mode="dry_run", classify=True)
    assert out["classified"][0]["kind"] == "notice"


def test_the_draft_carries_the_rfc_threading_headers_through_the_real_operation(tmp_path, monkeypatch) -> None:
    gmail = FakeGmail([person()])
    broker = fk.build_broker(tmp_path, monkeypatch, gmail, FakeAgentMail(), YAML)
    broker.operations.gmail_create_draft(
        {
            "mailbox": MAILBOX,
            "to": "a@b.example",
            "subject": "Re: x",
            "body": "y",
            "thread_id": "t-person",
            "in_reply_to": "<1@x.example>",
            "references": "<0@x.example> <1@x.example>",
        }
    )
    h = gmail.made_drafts[0]["headers"]
    assert h["In-Reply-To"] == "<1@x.example>" and email.utils.parseaddr(h["To"])[1] == "a@b.example"


def test_every_model_call_failing_on_a_small_inbox_is_a_failed_classify(tmp_path, monkeypatch) -> None:
    answers = {**ANSWERS, "Enabling 2FA": "!error", "Your receipt": "!error"}
    broker, gmail, http, _ = _broker(tmp_path, monkeypatch, [notice(), receipt()], answers=answers)
    out = fk.run(broker)
    assert out["classify"] == "failed"  # the hourly triage reads this word
    assert gmail.inbox("t-notice") and gmail.inbox("t-receipt")


def test_a_run_without_the_breakers_answer_reads_nothing(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, [person(), notice()])
    out = fk.run(broker, model="")
    assert out["classify"] == "refused_by_meter" and model.requests == []


def test_a_sent_list_from_before_the_window_is_not_counted() -> None:
    from workspace_broker.agentmail_ops import _before

    assert _before("2026-11-15T13:48:31Z", "2026-11-16T07:00:00+00:00")
    assert not _before("2026-11-16T13:48:31Z", "2026-11-16T07:00:00+00:00")
    assert not _before("garbled", "2026-11-16T07:00:00+00:00")  # unreadable: counted as sent


def test_a_dry_run_can_list_what_a_reading_would_be_shown_for_free(tmp_path, monkeypatch) -> None:
    broker, gmail, http, model = _broker(tmp_path, monkeypatch, _all_threads())
    out = fk.run(broker, mode="dry_run", candidates=True)
    subjects = {c["subject"] for c in out["candidates"]}
    assert "Kickoff date" in subjects and not any(s.startswith("[SMD Ops]") for s in subjects)
    assert model.requests == [] and http.sends == [] and gmail.modified == []


def test_repeated_ops_lines_collapse_and_only_the_newest_triage_is_kept() -> None:
    from workspace_broker import mailbox_keeper_digest as mkd

    day = 1_794_800_000_000
    pending = [
        {"kind": "never_clears", "line": "seat-a: a scheduled message did not go out", "ms": day + i} for i in range(12)
    ]
    pending += [{"kind": "triage", "line": f"Triage says {n} need you", "ms": day + 100 + n} for n in (1, 2, 3)]
    pending.append({"kind": "sign_in_dead", "line": "seat-a: the Clio sign-in is dead", "ms": day + 50})
    lines = mkd.ops_lines(pending, "America/Phoenix")
    assert len(lines) == 3
    assert lines[0] == "Triage says 3 need you"
    assert lines[1] == "seat-a: the Clio sign-in is dead"
    assert lines[2].startswith("seat-a: a scheduled message did not go out (12 times, latest ")


def test_mail_an_smd_operator_sent_is_not_from_a_person() -> None:
    raw = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Operator <operator@firm.example>"},
                {"name": "Subject", "value": "Operator help: this week's note"},
                {"name": "X-SMD-Audit-Row", "value": "row-1"},
            ]
        },
    }
    m = mk.parse_message(raw)
    settings = mk.Settings(mailbox="owner@firm.example", owners=frozenset({"owner@firm.example"}))
    assert m.from_operator and not mk.is_person(m, settings)
