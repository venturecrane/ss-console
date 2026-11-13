"""Records and medchron had no reply-once mark; a reply could go out twice
(2026-11-13).

Every other lane's ledger carries the reply-once compare-and-set
(``lane_ledger.ReplyOnce``) and the broker takes it at the send's first POST.
The chronology lane's once-only rested on the bound-reply claim alone, and the
records lane's reply (an SMD review row the gateway releases as an ordinary
``msgraph_reply``) consulted no ledger at all, so a second row for the same job
would have sent the records again.

Through the real classes: the real ``Broker``, the real ledgers on a real
sqlite file, the real ``MsGraphOps`` over the fake Graph mailbox. A "restart"
is a fresh broker and a fresh ledger object over the same file.

FALSIFIER: on the code before this change every test here fails: the medchron
ledger has no ``mark_replied`` and no ``reply_sent_at``, and a release naming a
records job is refused outright (``for_job names a job``), so nothing marks.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import (
    ADMIN,
    AGENT_UID,
    GATEWAY_PID,
    GRAPH_ID,
    FakeMailbox,
    _ack_in_thread,
    _broker,
    _chronology_job,
    _send,
)
from workspace_broker import bound_replies
from workspace_broker.medchron_ledger import MedchronLedger
from workspace_broker.records_reply import REPLY_KEY
from workspace_broker.records_store import RecordsLedger
from workspace_broker.reply_binding import BindingRefused

GOLDENS = Path(__file__).resolve().parent / "goldens" / "ledgers"
WORDS = "The records you asked for are attached."
OLD_JOB = "01KTJ0BX000000000000000777"
#: The runner's report of the review row it wrote (``records_lane._report_verdict``).
_ROW_REPORT = {"verdict": "delivered", "delivery": {"review_id": "a" * 24, "digest": "b" * 64, "decision": "approve"}}


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "audit.db")


def _landed(box: FakeMailbox) -> int:
    """Replies that reached Sent Items (``replies()`` counts every POST, a
    refused one included)."""
    return sum(1 for m in box.sent if m["id"] == "S")


# -- the chronology lane --------------------------------------------------------------


def test_a_chronology_reply_is_marked_on_its_ledger_and_goes_out_once(tmp_path: Path) -> None:
    job = _chronology_job(tmp_path, ["running", "delivered"])
    box = FakeMailbox()
    _ack_in_thread(box)
    binding = {"kind": "medchron_job", "job_id": job}
    assert _send(_broker(tmp_path, box), binding)["recipients"] == [ADMIN]
    row = MedchronLedger(_db(tmp_path), tmp_path / "mq").read(job)
    assert row["reply_sent_at"] and row["reply_key"] == "1:delivered"
    # A restart: a fresh broker and a fresh ledger over the same file.
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), binding)
    assert MedchronLedger(_db(tmp_path), tmp_path / "mq").mark_replied(job, "1:delivered") is False
    assert len(box.replies()) == 1


def test_a_chronology_the_ledger_shows_replied_sends_nothing(tmp_path: Path) -> None:
    """The ledger's mark stands on its own: with no bound-reply claim at all,
    a chronology already recorded as replied is not replied again, and the
    claim the attempt took is given back."""
    job = _chronology_job(tmp_path, ["running", "delivered"])
    assert MedchronLedger(_db(tmp_path), tmp_path / "mq").mark_replied(job, "1:delivered") is True
    box = FakeMailbox()
    _ack_in_thread(box)
    with pytest.raises(BindingRefused, match="already replied"):
        _send(_broker(tmp_path, box), {"kind": "medchron_job", "job_id": job})
    assert box.replies() == []
    assert not bound_replies.claimed(_db(tmp_path), f"medchron_job:{job}:1:delivered")


def test_a_chronology_reply_graph_refused_clears_its_mark(tmp_path: Path) -> None:
    job = _chronology_job(tmp_path, ["running", "delivered"])
    box = FakeMailbox()
    _ack_in_thread(box)
    box.post_status = 403
    with pytest.raises(Exception):
        _send(_broker(tmp_path, box), {"kind": "medchron_job", "job_id": job})
    assert MedchronLedger(_db(tmp_path), tmp_path / "mq").read(job)["reply_key"] is None
    box.post_status = None
    assert _send(_broker(tmp_path, box), {"kind": "medchron_job", "job_id": job})["recipients"] == [ADMIN]
    assert _landed(box) == 1


# -- the records lane -------------------------------------------------------------------


def _records_job(tmp_path: Path, message_ref: str = GRAPH_ID) -> str:
    """A records job in ``review``: its runner wrote the review row and
    reported it, exactly as ``jobs/records_lane._report_verdict`` does."""
    ledger = RecordsLedger(_db(tmp_path), tmp_path / "rq")
    job = ledger.submit(
        {"trigger": "request", "requester": ADMIN, "message_ref": message_ref, "request_text": "The intake records."}
    )
    ledger.record(job, {"state": "running"})
    ledger.record(job, _ROW_REPORT)
    return job


def _release(broker, job: str | None, message_id: str = GRAPH_ID) -> dict:
    """The gateway's release of a records review row (overlay
    ``hermes-smd-reply._review_one``): an ordinary reply naming the job."""
    return broker.handle(
        {
            "action": "msgraph_reply",
            "payload": {"message_id": message_id, "comment": WORDS},
            "anchor": {"kind": "graph_message", "graph_message_id": message_id},
            **({"for_job": {"kind": "records_job", "job_id": job}} if job else {}),
            "session_id": f"records:{job or ''}",
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )


def _mark(tmp_path: Path, job: str) -> tuple[str | None, str | None]:
    row = RecordsLedger(_db(tmp_path)).read(job) or {}
    return row.get("reply_sent_at"), row.get("reply_key")


def test_a_records_reply_is_marked_and_goes_out_once_across_a_restart(tmp_path: Path) -> None:
    job = _records_job(tmp_path)
    box = FakeMailbox()
    assert _release(_broker(tmp_path, box), job)["ok"] is True
    sent_at, key = _mark(tmp_path, job)
    assert sent_at and key == REPLY_KEY
    # A second row for the same job (a lane re-entry that lost its saved row),
    # released after a restart: refused before anything is sent.
    with pytest.raises(BindingRefused, match="already recorded as sent"):
        _release(_broker(tmp_path, box), job)
    assert len(box.replies()) == 1
    assert RecordsLedger(_db(tmp_path)).mark_replied(job, REPLY_KEY) is False


def test_a_records_reply_the_ledger_shows_sent_is_refused_at_the_post(tmp_path: Path, monkeypatch) -> None:
    """The compare-and-set itself refuses at the POST, not only the read
    before it: two releases racing past the read still send once."""
    job = _records_job(tmp_path)
    box = FakeMailbox()
    real = RecordsLedger.read

    def unmarked(self, job_id):
        row = real(self, job_id)
        return {**row, "reply_sent_at": None} if row else row

    assert RecordsLedger(_db(tmp_path)).mark_replied(job, REPLY_KEY) is True
    monkeypatch.setattr(RecordsLedger, "read", unmarked)
    with pytest.raises(BindingRefused, match="already sent"):
        _release(_broker(tmp_path, box), job)
    assert box.replies() == []


def test_a_records_reply_in_parts_still_sends_its_later_parts(tmp_path: Path) -> None:
    """The release marks the JOB, never the email: the files' later parts
    (plain replies to the same email, no ``for_job``) still go out."""
    job = _records_job(tmp_path)
    box = FakeMailbox()
    _release(_broker(tmp_path, box), job)
    assert _release(_broker(tmp_path, box), None)["ok"] is True
    assert len(box.replies()) == 2


def test_a_records_reply_graph_refused_releases_the_mark(tmp_path: Path) -> None:
    job = _records_job(tmp_path)
    box = FakeMailbox()
    box.post_status = 403
    with pytest.raises(Exception):
        _release(_broker(tmp_path, box), job)
    assert _mark(tmp_path, job) == (None, None)
    box.post_status = None
    assert _release(_broker(tmp_path, box), job)["ok"] is True
    assert _landed(box) == 1


def test_a_records_reply_whose_post_may_have_gone_keeps_the_mark(tmp_path: Path) -> None:
    job = _records_job(tmp_path)
    box = FakeMailbox()
    box.post_status = 503
    with pytest.raises(Exception):
        _release(_broker(tmp_path, box), job)
    assert _mark(tmp_path, job)[1] == REPLY_KEY
    attempted = len(box.replies())
    box.post_status = None
    with pytest.raises(BindingRefused):
        _release(_broker(tmp_path, box), job)
    assert len(box.replies()) == attempted and _landed(box) == 0


def test_a_records_reply_to_another_email_or_an_unknown_job_is_refused(tmp_path: Path) -> None:
    other = _records_job(tmp_path, "AAMkSOMEOTHERMAIL0001=")
    box = FakeMailbox()
    with pytest.raises(BindingRefused, match="not records job"):
        _release(_broker(tmp_path, box), other)
    with pytest.raises(BindingRefused, match="no records job"):
        _release(_broker(tmp_path, box), OLD_JOB)
    assert box.replies() == [] and _mark(tmp_path, other) == (None, None)


# -- the aged seat files ------------------------------------------------------------------

#: One row as an older ledger wrote it: its NOT NULL columns, nothing else (the
#: columns this change adds do not exist yet). Literal per lane.
_OLD_ROW = {
    "medchron": (
        "INSERT INTO medchron_jobs (id, created_at, updated_at, state, matter_id, matter_number, envelope_digest) "
        "VALUES (?, 'old', 'old', 'delivered', 'm', '1', 'd')"
    ),
    "records": (
        "INSERT INTO records_jobs (job_id, request_ref, message_ref, requester, state, created_at, updated_at, "
        "trigger, ack, envelope_digest) VALUES (?, 'r', 'm', 'a@firm.example', 'review', 'old', 'old', 'request', "
        "'turn', 'd')"
    ),
}


def _aged_steps(lane: str) -> list[dict]:
    return json.loads((GOLDENS / lane / "aged.json").read_text(encoding="utf-8"))["steps"]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}


@pytest.mark.parametrize(
    "lane,table,open_ledger",
    [("medchron", "medchron_jobs", MedchronLedger), ("records", "records_jobs", RecordsLedger)],
)
def test_every_aged_seat_file_migrates_and_an_old_row_marks_once(tmp_path: Path, lane, table, open_ledger) -> None:
    steps = _aged_steps(lane)
    assert steps, lane
    for i, step in enumerate(steps):
        db = tmp_path / f"{lane}-{i}.db"
        conn = sqlite3.connect(db)
        parts = [s.split(": ", 1) for s in step["schema"]]
        for _kind, sql in sorted(parts, key=lambda r: (not r[0].startswith("table"), r[0])):
            conn.execute(sql)
        before = _columns(conn, table)
        assert "reply_sent_at" not in before and "reply_key" not in before, step["commit"]
        conn.execute(_OLD_ROW[lane], (OLD_JOB,))
        conn.commit()
        conn.close()
        ledger = open_ledger(str(db), tmp_path / "q")
        row = ledger.read(OLD_JOB)
        assert row["reply_sent_at"] is None and row["reply_key"] is None, step["commit"]
        assert ledger.mark_replied(OLD_JOB, "1:delivered") is True
        assert ledger.mark_replied(OLD_JOB, "1:delivered") is False
        # Reopened (the migration runs again, idempotently): the mark stays.
        again = open_ledger(str(db), tmp_path / "q")
        assert again.read(OLD_JOB)["reply_key"] == "1:delivered"
        assert again.mark_replied(OLD_JOB, "1:delivered") is False
