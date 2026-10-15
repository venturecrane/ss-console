"""SMD notes (note_verbs.py): each record check has a test that fails without it.

A note puts words SMD approved in a firm colleague's own thread. The broker
decides what the root command could get wrong: the email is a recent received
message from someone the seat may answer, the words carry no em dash and no
identifier the email does not carry, a person approved them, the live record is
exactly the dry run SMD read (the digest), and the same words go once.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_reply_binding as rb
from workspace_broker import note_identifiers
from workspace_broker.note_ledger import NoteLedger

ROOT_PID = 7777
COLLEAGUE = "colleague@firm.example"
OFFICE = "office@firm.example"
OUTSIDE = "opposing@counsel.example"
SUBJECT = "Vasic Matter 900417 rep letter"
BODY_HTML = "<html><body><p>Please file the rep letter for matter 900417 dated 10/12/26.</p></body></html>"
TEXT = "Hi,\n\nThe rep letter for matter 900417 is filed on the matter.\n"


def _message(**over) -> dict:
    return {
        "from": {"emailAddress": {"address": COLLEAGUE}},
        "subject": SUBJECT,
        "body": {"contentType": "html", "content": BODY_HTML},
        "toRecipients": [{"emailAddress": {"address": rb.MAILBOX}}, {"emailAddress": {"address": OFFICE}}],
        "ccRecipients": [{"emailAddress": {"address": OUTSIDE}}],
        **over,
    }


def _seat(tmp_path: Path, **over) -> tuple[rb.Broker, rb.FakeMailbox]:
    box = rb.FakeMailbox(**_message(**over))
    return rb._broker(tmp_path, box), box


def _call(broker, action: str, uid: int | None = 0, pid: int = ROOT_PID, **req) -> dict:
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def _preview(broker, text: str = TEXT, **extra) -> dict:
    req = {"graph_message_id": rb.GRAPH_ID, "text": text, "approved_by": "scott@smd.services", **extra}
    return _call(broker, "note_preview", **req)


def _record(broker, text: str = TEXT, confirm: str | None = None, **extra) -> dict:
    if confirm is None:
        confirm = _preview(broker, text, **extra)["digest"]
    req = {"graph_message_id": rb.GRAPH_ID, "text": text, "approved_by": "scott@smd.services", "confirm": confirm}
    return _call(broker, "note_record", **{**req, **extra})


def _notes(broker) -> list[dict]:
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM note_jobs")]
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()


# -- the happy paths (a refusal means nothing if nothing passes) ---------------


def test_preview_shows_the_anchor_the_exact_cc_and_writes_nothing(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    out = _preview(broker)
    assert out["accepted"] is True
    assert (out["subject"], out["sender"]) == (SUBJECT, COLLEAGUE)
    # The firm's own participant is copied; the outside party and this mailbox are not.
    assert out["cc"] == [OFFICE]
    assert len(out["digest"]) == 64 and out["text"] == TEXT
    assert _notes(broker) == []
    assert rb._rows(broker, "NOTE_JOB_RECORDED") == []
    assert box.replies() == []


def test_record_writes_one_note_and_an_audit_row_without_the_text(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _record(broker)
    assert out["accepted"] is True and out["state"] == "delivered"
    notes = _notes(broker)
    assert [(n["id"], n["text"], n["requester"], n["request_ref"]) for n in notes] == [
        (out["job_id"], TEXT, COLLEAGUE, rb.GRAPH_ID)
    ]
    rows = rb._rows(broker, "NOTE_JOB_RECORDED")
    assert len(rows) == 1 and rows[0]["job_id"] == out["job_id"]
    assert rows[0]["approved_by"] == "scott@smd.services" and rows[0]["requester_key"]
    assert "900417" not in json.dumps(rows) and "rep letter" not in json.dumps(rows)


def test_status_reads_the_words_back_for_the_deliver_turn(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    job = _record(broker)["job_id"]
    out = _call(broker, "note_job_status", uid=rb.AGENT_UID, pid=rb.GATEWAY_PID, job_id=job)
    assert out["job"] == {"job_id": job, "state": "delivered", "text": TEXT}


# -- who may call ---------------------------------------------------------------


@pytest.mark.parametrize("action", ["note_preview", "note_record"])
def test_preview_and_record_are_root_only(tmp_path: Path, action: str) -> None:
    broker, _ = _seat(tmp_path)
    with pytest.raises(PermissionError):
        _call(broker, action, uid=rb.AGENT_UID, pid=rb.GATEWAY_PID, graph_message_id=rb.GRAPH_ID, text=TEXT)


def test_status_is_not_for_an_agent_uid_off_the_gateway(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    with pytest.raises(PermissionError):
        _call(broker, "note_job_status", uid=rb.AGENT_UID, pid=ROOT_PID, job_id="0" * 26)


# -- the digest: the live record is the dry run SMD read -------------------------


def test_a_confirm_that_does_not_match_records_nothing(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _record(broker, confirm="0" * 64)
    assert out["accepted"] is False and "confirm" in out["reason"]
    assert _notes(broker) == []


def test_a_copy_list_that_changed_since_the_dry_run_is_refused(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    digest = _preview(broker)["digest"]
    box.message["ccRecipients"] = [{"emailAddress": {"address": "partner@firm.example"}}]
    out = _record(broker, confirm=digest)
    assert out["accepted"] is False
    assert _notes(broker) == []


def test_another_approver_needs_a_new_dry_run(tmp_path: Path) -> None:
    """The digest binds who approved the words, so --confirm cannot carry one
    person's approval onto another name. FALSIFIER: drop approved_by from it."""
    broker, _ = _seat(tmp_path)
    digest = _preview(broker)["digest"]
    out = _record(broker, confirm=digest, approved_by="someone.else@smd.services")
    assert out["accepted"] is False and _notes(broker) == []


def test_changed_words_need_a_new_dry_run(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    digest = _preview(broker)["digest"]
    out = _record(broker, text=TEXT + "Thanks again.\n", confirm=digest)
    assert out["accepted"] is False and _notes(broker) == []


def test_the_same_words_on_the_same_email_go_once(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    first = _record(broker)
    assert _preview(broker)["accepted"] is False
    again = _record(broker, confirm=first["digest"])
    assert again["accepted"] is False and again["job_id"] == first["job_id"]
    assert len(_notes(broker)) == 1


# -- the anchor ----------------------------------------------------------------


@pytest.mark.parametrize("folder", ["SENTITEMS", "JUNK", "DRAFTS"])
def test_an_email_outside_the_inbox_is_refused(tmp_path: Path, folder: str) -> None:
    broker, _ = _seat(tmp_path, parentFolderId=folder)
    assert _preview(broker)["accepted"] is False
    assert _record(broker, confirm="x" * 64)["accepted"] is False and _notes(broker) == []


def test_an_email_not_in_the_mailbox_is_refused(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    box.present = False
    assert _preview(broker)["accepted"] is False


def test_a_sender_this_seat_may_not_answer_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path, **{"from": {"emailAddress": {"address": rb.STRANGER}}})
    out = _preview(broker)
    assert out["accepted"] is False and "inbound_allow_from" in out["reason"]


def test_a_stale_anchor_is_refused(tmp_path: Path) -> None:
    old = rb._iso(datetime.now(timezone.utc) - timedelta(days=15))
    broker, _ = _seat(tmp_path, receivedDateTime=old)
    out = _preview(broker)
    assert out["accepted"] is False and "older than 14 days" in out["reason"]


def test_a_graph_id_with_path_characters_is_refused(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    out = _call(broker, "note_preview", graph_message_id="../inbox/x", text=TEXT, approved_by="scott@smd.services")
    assert out["accepted"] is False and box.calls == []


# -- the words -----------------------------------------------------------------


def test_a_missing_approver_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _call(broker, "note_record", graph_message_id=rb.GRAPH_ID, text=TEXT, confirm="x" * 64)
    assert out["accepted"] is False and "approved_by" in out["reason"]
    assert _notes(broker) == []


@pytest.mark.parametrize(
    ("text", "why"),
    [("", "no text"), ("   \n", "no text"), ("x" * 2001, "at most 2000"), ("Filed — done.", "em dash")],
)
def test_bad_words_are_refused(tmp_path: Path, text: str, why: str) -> None:
    broker, _ = _seat(tmp_path)
    out = _preview(broker, text=text)
    assert out["accepted"] is False and why in out["reason"]


# -- identifier corroboration ---------------------------------------------------


def test_a_matter_number_the_email_never_names_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _preview(broker, text="The rep letter for matter 900418 is filed.")
    assert out["accepted"] is False and "900418" in out["reason"]


def test_a_number_in_the_body_only_corroborates(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path, subject="Rep letter")
    assert _preview(broker)["accepted"] is True


def test_a_number_split_by_markup_does_not_corroborate(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path, subject="Rep letter", body={"contentType": "html", "content": "<p>9004<b>17</b></p>"})
    assert _preview(broker)["accepted"] is False


def test_a_date_written_another_way_corroborates(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    assert _preview(broker, text="Matter 900417: the letter dated October 12, 2026 is filed.")["accepted"] is True


def test_a_date_the_email_never_names_is_refused(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    out = _preview(broker, text="Matter 900417: the hearing is on March 3, 2027.")
    assert out["accepted"] is False and "March 3, 2027" in out["reason"]


def test_today_needs_no_corroboration(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    today = datetime.now(timezone.utc).strftime("%m/%d/%Y")
    assert _preview(broker, text=f"Matter 900417 was filed {today}.")["accepted"] is True


def test_a_shaped_case_number_must_corroborate(tmp_path: Path) -> None:
    broker, _ = _seat(tmp_path)
    assert _preview(broker, text="Case 2:24-cv-01234 is filed.")["accepted"] is False


# -- parity with the overlay gate's shapes ---------------------------------------

_CORPUS = [
    "A123456789 and A-12345678 and A#123-456-789",
    "Receipt EAC2190012345 arrived",
    "SSN 123-45-6789",
    "Case 1:24-cv-01234, No. 24-12345, 2026-PI-101, PI-2026-0001",
    "Dated 6/8/26, 06-08-2026, 2026-06-08T09:00:00Z, June 8, 2026, 8 June 2026, Jun. 8 2026",
]


@pytest.mark.parametrize("text", _CORPUS)
def test_the_structured_shapes_match_the_identifier_filter(text: str) -> None:
    """FALSIFIER: drift a pattern here (or there) and this goes red. The
    substrate copy predates the overlay's ordinal suffixes, so the corpus has
    none; the ordinal forms are pinned below."""
    from safety_substrate import identifier_filter as source

    ours = sorted((h.kind, h.canonical) for h in note_identifiers.structured(text))
    theirs = sorted((h.kind.value, h.canonical) for h in source._extract(text, include_names=False))
    assert ours == theirs and ours


def test_ordinal_dates_fold_like_the_overlay_does() -> None:
    hits = note_identifiers.structured("August 5th, 2026 and 5th August 2026")
    assert {h.canonical for h in hits} == {"2026-08-05"}


def test_status_never_carries_the_request_or_approver(tmp_path: Path) -> None:
    """The DELIVER turn gets the words and the state, nothing about who asked
    for them or who approved them."""
    ledger = NoteLedger(str(tmp_path / "audit.db"))
    job = ledger.create(requester=COLLEAGUE, request_ref=rb.GRAPH_ID, text=TEXT, approved_by="a", recorded_by="root")
    broker, _ = _seat(tmp_path)
    out = _call(broker, "note_job_status", job_id=job)
    assert set(out["job"]) == {"job_id", "state", "text"}
