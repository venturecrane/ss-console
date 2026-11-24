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
import urllib.error
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
OTHER = "paralegal@firm.example"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to(*addresses: str) -> list[dict]:
    return [{"emailAddress": {"address": a}} for a in addresses]


class ThreadMailbox(rb.FakeMailbox):
    """The reply-binding fake, plus the received-in-conversation read and a
    switch that fails the sent read."""

    def __init__(self, **over) -> None:
        super().__init__(**over)
        self.received: list[dict] | None = None
        self.sent_status: int | None = None

    def __call__(self, request, timeout=None):
        url = request.full_url
        decoded = urllib.parse.unquote(url)
        if request.method == "GET" and "/mailFolders/sentitems/messages" in url and self.sent_status:
            self.calls.append((request.method, url))
            raise urllib.error.HTTPError(url, self.sent_status, "x", {}, None)  # type: ignore[arg-type]
        if (
            request.method == "GET"
            and "/mailFolders/" not in url
            and "/messages?" in url
            and "conversationId eq" in decoded
        ):
            self.calls.append((request.method, url))
            return rb._Response(json.dumps({"value": self.received if self.received is not None else [self.message]}))
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


def _msg(mid: str, minutes: int, preview: str, to: tuple[str, ...] = (rb.ADMIN,), **extra) -> dict:
    return {
        "id": mid,
        "conversationId": "CONV-1",
        "sentDateTime": _iso(datetime.now(timezone.utc) - timedelta(minutes=minutes)),
        "bodyPreview": preview,
        "toRecipients": _to(*to),
        "ccRecipients": [],
        **extra,
    }


def test_the_note_sent_in_the_thread_reaches_the_turn(tmp_path: Path) -> None:
    """FALSIFIER: without the verb the turn saw only the email it answers."""
    broker, box = _seat(tmp_path)
    box.sent = [
        _msg("SENT-NOTE", 1, NOTE, attachments=[{"name": "Special Interrogatories Set One.docx"}, "not a row"]),
        _msg("SENT-OLD", 6, "How many interrogatories should each set be cut to?"),
        {**_msg("SENT-ELSEWHERE", 0, "another thread"), "conversationId": "CONV-2"},
    ]
    out = _ask(broker)
    assert out["accepted"] is True
    assert [s["text"] for s in out["sent"]] == ["How many interrogatories should each set be cut to?", NOTE]
    assert out["sent"][-1]["files"] == ["Special Interrogatories Set One.docx"]
    assert out["sent"][-1]["to"] == [rb.ADMIN]
    # Attachment names ride the sent query: no per-message read.
    assert not [u for _m, u in box.calls if "/attachments" in u]
    assert any("$expand=attachments" in urllib.parse.unquote(u) for _m, u in box.calls)


def test_a_message_to_someone_else_is_not_shown(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    box.sent = [
        _msg("FWD", 2, "Forwarding the set for review.", to=(OTHER,)),
        _msg("CC", 1, NOTE, to=(OTHER,), ccRecipients=_to(rb.ADMIN)),
    ]
    out = _ask(broker)
    assert [s["text"] for s in out["sent"]] == [NOTE]
    assert out["sent"][0]["to"] == [OTHER, rb.ADMIN]


@pytest.mark.parametrize(
    "preview",
    [
        f"{NOTE} From: Dana Example Sent: Monday Reduce the rogs to 120125",
        f"{NOTE} -----Original Message----- Reduce the rogs",
        f"{NOTE} On Mon, Nov 23, 2026 at 1:26 PM Dana Example wrote: Reduce the rogs",
        f"{NOTE} > Reduce the rogs",
    ],
)
def test_the_quoted_original_is_cut_from_the_preview(tmp_path: Path, preview: str) -> None:
    """The firm's own words never land in a block the model is told never to contradict."""
    broker, box = _seat(tmp_path)
    box.sent = [_msg("N", 1, preview)]
    assert _ask(broker)["sent"][0]["text"] == NOTE


def test_only_the_newest_messages_and_a_short_preview(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    box.sent = [_msg(f"S{i}", i, "x" * 900) for i in range(9)]
    out = _ask(broker)
    assert len(out["sent"]) == thread_recent.MAX_SENT
    assert all(len(s["text"]) == thread_recent.PREVIEW_CHARS for s in out["sent"])


def test_the_inbox_id_is_read_once(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    _ask(broker)
    _ask(broker)
    assert len([u for _m, u in box.calls if "/mailFolders/inbox?" in u]) == 1


def _drafting_rows(broker, refs: list[tuple[str, str, str]]) -> str:
    ledger = broker.drafting.ledger
    ledger.ensure_schema()
    conn = ledger._connect()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        for ref, state, klass in refs:
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
    return now


def test_drafting_files_delivered_for_a_request_in_the_thread(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path, drafting=True)
    now = _drafting_rows(
        broker,
        [
            (rb.IMID, "delivered", "special_interrogatories"),
            ("<other@x>", "delivered", "special_interrogatories"),
            (rb.IMID, "running", "requests_for_admission"),
        ],
    )
    out = _ask(broker)
    assert out["filed"] == [
        {
            "at": now,
            "document": "special_interrogatories",
            "matter": "900417",
            "files": [f"Rogs delivered {rb.IMID}.docx"],
        }
    ]
    refs_read = [urllib.parse.unquote(u) for _m, u in box.calls if "/messages?" in u and "/mailFolders/" not in u]
    assert refs_read and "receivedDateTime ge " in refs_read[0] and "$orderby=receivedDateTime desc" in refs_read[0]


def test_a_failed_sent_read_keeps_the_filed_section(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path, drafting=True)
    _drafting_rows(broker, [(rb.IMID, "delivered", "special_interrogatories")])
    box.sent_status = 503
    out = _ask(broker)
    assert out["accepted"] is True and out["sent"] is None and out["sent_error"]
    assert out["filed"] and out["filed"][0]["matter"] == "900417"


def test_a_failed_filed_read_keeps_the_sent_section(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path, drafting=True)
    box.sent = [_msg("N", 1, NOTE)]
    broker.drafting.ledger.delivered_for_refs = None  # a broken ledger: calling it raises
    out = _ask(broker)
    assert out["filed"] is None and out["filed_error"]
    assert [s["text"] for s in out["sent"]] == [NOTE]


def test_an_email_not_in_the_inbox_is_refused(tmp_path: Path) -> None:
    broker, box = _seat(tmp_path)
    box.present = False
    assert _ask(broker)["accepted"] is False


def test_thread_recent_is_the_gateways_only(tmp_path: Path) -> None:
    broker, _box = _seat(tmp_path)
    with pytest.raises(PermissionError):
        _ask(broker, pid=rb.GATEWAY_PID + 1)
