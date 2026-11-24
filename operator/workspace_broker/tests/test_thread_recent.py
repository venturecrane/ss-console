"""``thread_recent`` (thread_recent.py): what this mailbox already said in the
turn's own thread, so the reply never contradicts it (A&P 2026-11-23).

The replay, with invented names: an attorney asked to cut a set to "120125";
SMD's note in the thread said each set was cut to 120; his next email,
"120-125", opened a turn that could not see the note. The verb hands that turn
the note.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_reply_binding as rb
from workspace_broker import thread_recent
from workspace_broker.drafting_ledger import DraftingLedger
from workspace_broker.drafting_verbs import DraftingVerbs

NOTE = "The special interrogatories on matter 900417 are cut to the 120 most important in each set."


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class ThreadMailbox(rb.FakeMailbox):
    """The reply-binding fake, plus the conversation read and attachment names."""

    def __init__(self, **over) -> None:
        super().__init__(**over)
        self.attachments: dict[str, list[str]] = {}

    def __call__(self, request, timeout=None):
        url = request.full_url
        decoded = urllib.parse.unquote(url)
        if request.method == "GET" and "/attachments?" in url:
            self.calls.append((request.method, url))
            mid = decoded.split("/messages/", 1)[1].split("/attachments", 1)[0]
            return rb._Response(json.dumps({"value": [{"name": n} for n in self.attachments.get(mid, [])]}))
        if (
            request.method == "GET"
            and "/mailFolders/" not in url
            and "/messages?" in url
            and "conversationId eq" in decoded
        ):
            self.calls.append((request.method, url))
            return rb._Response(json.dumps({"value": [self.message]}))
        return super().__call__(request, timeout)


def _seat(tmp_path: Path, *, drafting: bool = False):
    box = ThreadMailbox()
    broker = rb._broker(tmp_path, box)
    if drafting:
        db = str(tmp_path / "audit.db")
        broker.drafting = DraftingVerbs(
            DraftingLedger(db, tmp_path / "queue"), customer_yaml=str(broker.customer_path), audit_append=None
        )
    return broker, box


def _ask(broker, pid: int = rb.GATEWAY_PID, **req) -> dict:
    return broker.handle(
        {"action": "thread_recent", "graph_message_id": rb.GRAPH_ID, **req}, peer_pid=pid, peer_uid=rb.AGENT_UID
    )


def test_the_note_sent_in_the_thread_reaches_the_turn(tmp_path: Path) -> None:
    """FALSIFIER: without the verb the turn saw only the email it answers."""
    broker, box = _seat(tmp_path)
    box.sent = [
        {
            "id": "SENT-NOTE",
            "conversationId": "CONV-1",
            "sentDateTime": _iso(datetime.now(timezone.utc) - timedelta(minutes=1)),
            "bodyPreview": NOTE,
            "hasAttachments": True,
        },
        {
            "id": "SENT-OLD",
            "conversationId": "CONV-1",
            "sentDateTime": _iso(datetime.now(timezone.utc) - timedelta(minutes=6)),
            "bodyPreview": "How many interrogatories should each set be cut to?",
            "hasAttachments": False,
        },
        {
            "id": "SENT-ELSEWHERE",
            "conversationId": "CONV-2",
            "sentDateTime": _iso(datetime.now(timezone.utc)),
            "bodyPreview": "another thread",
        },
    ]
    box.attachments["SENT-NOTE"] = ["Special Interrogatories Set One.docx"]
    out = _ask(broker)
    assert out["accepted"] is True
    assert [s["text"] for s in out["sent"]] == ["How many interrogatories should each set be cut to?", NOTE]
    assert out["sent"][-1]["files"] == ["Special Interrogatories Set One.docx"]


def test_only_the_newest_messages_and_a_short_preview(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    now = datetime.now(timezone.utc)
    box.sent = [
        {
            "id": f"S{i}",
            "conversationId": "CONV-1",
            "sentDateTime": _iso(now - timedelta(minutes=i)),
            "bodyPreview": "x" * 900,
        }
        for i in range(9)
    ]
    out = _ask(broker)
    assert len(out["sent"]) == thread_recent.MAX_SENT
    assert all(len(s["text"]) == thread_recent.PREVIEW_CHARS for s in out["sent"])


def test_drafting_files_delivered_for_a_request_in_the_thread(tmp_path: Path) -> None:
    broker, _box = _seat(tmp_path, drafting=True)
    ledger = broker.drafting.ledger
    ledger.ensure_schema()
    conn = ledger._connect()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for ref, state, klass in (
            (rb.IMID, "delivered", "special_interrogatories"),
            ("<other@x>", "delivered", "special_interrogatories"),
            (rb.IMID, "running", "requests_for_admission"),
        ):
            conn.execute(
                "INSERT INTO drafting_jobs (id, created_at, updated_at, state, matter_id, matter_number, "
                "file_to_matter_id, document_class, requester, request_ref, envelope_digest, delivery_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"J{ref}{state}",
                    now,
                    now,
                    state,
                    rb.MATTER,
                    "900417",
                    rb.MATTER,
                    klass,
                    rb.ADMIN,
                    ref,
                    "d",
                    json.dumps({"files": [{"name": f"Rogs {state} {ref}.docx"}]}),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    out = _ask(broker)
    assert out["filed"] == [
        {
            "at": now,
            "document": "special_interrogatories",
            "matter": "900417",
            "files": [f"Rogs delivered {rb.IMID}.docx"],
        }
    ]


def test_an_email_not_in_the_inbox_is_refused(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    box.present = False
    assert _ask(broker)["accepted"] is False


def test_thread_recent_is_the_gateways_only(tmp_path: Path) -> None:
    broker, _box = _seat(tmp_path)
    with pytest.raises(PermissionError):
        _ask(broker, pid=rb.GATEWAY_PID + 1)
