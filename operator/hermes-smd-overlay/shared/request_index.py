"""Seat-local index of the requests people email in, for SMD's request cards.

When a firm person emails an Operator seat, SMD gets one email card per request
(who, subject, the opening of the Operator's reply, tools run, outcome, time to
first reply), a NO REPLY alarm when nothing answers within 30 minutes, and a
follow-up card when a job the request started finishes. The audit ledger can
answer every one of those questions except three: who wrote, what the subject
was, and what the reply said. The ledger deliberately holds none of them (an
audit export is a file that leaves the Machine). This file holds exactly those
three, on the seat volume, for as long as a card can still need them.

ss-console persists no client text (ADR 0052 s5): the subject, the sender and
the reply opening live ONLY here and in the transient card email. So custody is
this module's job, done on every write rather than by a sweeper that could
stop:

* the text columns (``sender``, ``subject``, the openings, and since
  2026-10-27 the whole ``request_text`` and ``reply_text``) are nulled on
  rows older than :data:`TEXT_RETENTION_DAYS`, which is longer than any card
  can wait (a card retries for a day);
* the rows themselves are deleted after :data:`ROW_RETENTION_DAYS`.

ONE WRITER PROCESS. The agent process writes this file (the router at intake,
the reply plugin at each send, the held-reply sweeper on release); the gate's
heartbeat only reads it, read-only. The card-sent state lives in a separate,
gate-owned file (``shared.request_cards``) so neither file has two writer
processes.

Never raises into a caller. Every public function logs and returns; a broken
index costs a card, never an intake or a send. ``logger.error`` reaches Sentry
through the logging integration, so a persistently broken index is not silent.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from email.utils import parseaddr

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "/opt/data/requests.db"

#: Text columns are nulled after this. A card retries for at most a day, so a
#: week is a generous margin and still a hard bound on how long a subject sits.
TEXT_RETENTION_DAYS = 7
#: Rows (ids and timestamps only, by then) are deleted after this.
ROW_RETENTION_DAYS = 30

#: Bounds at the write, so a pathological subject or body never bloats the file.
SENDER_MAX = 160
SUBJECT_MAX = 200
#: The opening is cut to about this many characters at a word boundary.
OPENING_CHARS = 240
#: The whole message (the request, or a reply) is kept up to about this many
#: characters, paragraphs intact, so a card can say what was asked and what was
#: answered rather than the first line of each (Captain, 2026-10-27: a card must
#: clearly state what the request is, who made it, and the Operator's response).
#: Same custody as the openings: nulled after TEXT_RETENTION_DAYS.
TEXT_CHARS = 4000

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS requests (
  vendor_message_id   TEXT PRIMARY KEY,
  internet_message_id TEXT,
  received_at         REAL NOT NULL,
  sender              TEXT,
  subject             TEXT,
  reply_opening       TEXT,
  opening_at          REAL,
  thread_ref          TEXT,
  request_opening     TEXT,
  request_text        TEXT,
  reply_text          TEXT
)
"""
_CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_requests_received ON requests(received_at)"
#: One row per reply SENT, keyed by the sent message's id (2026-10-26). The
#: ``requests`` row keeps only the FIRST reply's opening, so a later reply in
#: the same thread (2026-10-26: an Operator corrected itself 50 minutes after
#: its first answer) left no words anywhere SMD reads. This table is what a ``followup`` card quotes.
#: Written for every send, including a reply to a message the index never saw.
_CREATE_REPLIES_SQL = """
CREATE TABLE IF NOT EXISTS replies (
  sent_message_id TEXT PRIMARY KEY,
  in_reply_to     TEXT,
  sent_at         REAL NOT NULL,
  opening         TEXT,
  text            TEXT
)
"""
#: Columns added after the first seats wrote this file (2026-10-22: the thread a
#: message belongs to, and the requester's own opening words, so a follow-up on a
#: HELD job can be carded with what the person said). CREATE IF NOT EXISTS does
#: not add them to an existing table. Two halves keep an older file working, and
#: a column added here needs both: the WRITER adds them (``_connect``, once, by
#: name; ``converge`` runs it at agent boot so the file catches up seconds after
#: a deploy, not on the next piece of client mail), and the READER projects its
#: columns against what the file has (``read_requests``), because the gate reads
#: this file read-only from another process and a read that names a column the
#: writer has not added yet fails every card on the seat (Sentry SMD-OPERATOR-2S).
_ADDED_COLUMNS = (
    ("thread_ref", "TEXT"),
    ("request_opening", "TEXT"),
    ("request_text", "TEXT"),
    ("reply_text", "TEXT"),
)
#: The same for the ``replies`` table (2026-10-27: the whole text of each reply).
_ADDED_REPLY_COLUMNS = (("text", "TEXT"),)
#: Every column a reader returns, in SELECT order. The one list the writer's
#: schema and the reader's projection share: a column missing from the file
#: reads as None.
_READ_COLUMNS = (
    "vendor_message_id",
    "internet_message_id",
    "received_at",
    "sender",
    "subject",
    "reply_opening",
    "opening_at",
    "thread_ref",
    "request_opening",
    "request_text",
    "reply_text",
)
#: The text columns custody nulls after TEXT_RETENTION_DAYS.
_TEXT_COLUMNS = (
    "sender",
    "subject",
    "reply_opening",
    "request_opening",
    "request_text",
    "reply_text",
)


def db_path() -> str:
    """The index path: ``SMD_REQUEST_INDEX_DB_PATH`` (tests/dev) or the volume default."""
    return os.environ.get("SMD_REQUEST_INDEX_DB_PATH") or DEFAULT_DB_PATH


def _connect(path: str) -> sqlite3.Connection:
    """A fresh writer connection. One per call: the hook thread and the sweeper
    thread both write, and a per-call connection with WAL + a busy timeout is
    the simplest thing that cannot share a cursor across threads."""
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_CREATE_SQL)
    conn.execute(_CREATE_INDEX_SQL)
    conn.execute(_CREATE_REPLIES_SQL)
    present = {row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
    for name, kind in _ADDED_COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {name} {kind}")
    present = {row[1] for row in conn.execute("PRAGMA table_info(replies)").fetchall()}
    for name, kind in _ADDED_REPLY_COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE replies ADD COLUMN {name} {kind}")
    return conn


def converge(*, path: str | None = None) -> bool:
    """Bring the index file to the current schema now, writing no row.

    Called at agent boot by the intake writer (the router plugin), so a file
    written by an older image gains its columns seconds after a deploy instead
    of on the next inbound message. The gate reads this file from another
    process, read-only; until the writer has converged it, every card on the
    seat depends on the reader tolerating the older shape. Returns whether the
    file is at the current schema; never raises."""
    target = path or db_path()
    try:
        conn = _connect(target)
        try:
            present = {row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
            in_replies = {row[1] for row in conn.execute("PRAGMA table_info(replies)").fetchall()}
            conn.commit()
        finally:
            conn.close()
        return all(name in present for name, _kind in _ADDED_COLUMNS) and all(
            name in in_replies for name, _kind in _ADDED_REPLY_COLUMNS
        )
    except Exception:  # noqa: BLE001 — a boot never fails on the card index
        logger.error("request_index: could not converge %s", target, exc_info=True)
        return False


def _custody(conn: sqlite3.Connection, now: float) -> None:
    """Null old text, delete old rows. Runs inside every write's transaction."""
    sets = ", ".join(f"{c}=NULL" for c in _TEXT_COLUMNS)
    some = " OR ".join(f"{c} IS NOT NULL" for c in _TEXT_COLUMNS)
    conn.execute(
        f"UPDATE requests SET {sets} WHERE received_at < ? AND ({some})",
        (now - TEXT_RETENTION_DAYS * 86400.0,),
    )
    conn.execute(
        "DELETE FROM requests WHERE received_at < ?",
        (now - ROW_RETENTION_DAYS * 86400.0,),
    )
    conn.execute(
        "UPDATE replies SET opening=NULL, text=NULL"
        " WHERE sent_at < ? AND (opening IS NOT NULL OR text IS NOT NULL)",
        (now - TEXT_RETENTION_DAYS * 86400.0,),
    )
    conn.execute(
        "DELETE FROM replies WHERE sent_at < ?",
        (now - ROW_RETENTION_DAYS * 86400.0,),
    )


def _clean(value: object, limit: int) -> str | None:
    """Whitespace-collapsed, control-free, bounded text, or None when empty."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:limit] or None


# A first line that only greets: "Hi Sam,", "Hello -", "Good morning all,".
_SALUTATION_RE = re.compile(
    r"^(hi|hello|hey|dear|good (morning|afternoon|evening)|greetings)\b", re.IGNORECASE
)


def reply_opening(body: object) -> str | None:
    """The first ~240 characters of a message after its salutation line (a
    reply's, or since 2026-10-22 the requester's own).

    The salutation is the first non-empty line when it greets (``Hi Name,``) or
    is a short line ending in a comma. Whitespace is collapsed; the cut lands on
    a word boundary with an ellipsis when the body runs longer. ``None`` for an
    empty or non-string body.
    """
    if not isinstance(body, str):
        return None
    lines = [line.strip() for line in body.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if lines:
        first = lines[0]
        if _SALUTATION_RE.match(first) or (len(first) <= 60 and first.endswith(",")):
            lines.pop(0)
    text = _clean("\n".join(lines), 10_000)
    if not text:
        return None
    if len(text) <= OPENING_CHARS:
        return text
    cut = text[:OPENING_CHARS]
    space = cut.rfind(" ")
    if space > OPENING_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "..."


def message_text(body: object) -> str | None:
    """The whole of a message after its salutation line, paragraphs kept, cut at
    a word boundary near :data:`TEXT_CHARS` with an ellipsis. ``None`` for an
    empty or non-string body.

    The same salutation rule as :func:`reply_opening`. Within a line,
    whitespace collapses and non-printing characters drop; runs of blank lines
    become one, so the card reads as the message did."""
    if not isinstance(body, str):
        return None
    lines = [
        " ".join("".join(ch for ch in line if ch.isprintable()).split())
        for line in body.splitlines()
    ]
    while lines and not lines[0]:
        lines.pop(0)
    if lines:
        first = lines[0]
        if _SALUTATION_RE.match(first) or (len(first) <= 60 and first.endswith(",")):
            lines.pop(0)
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    text = "\n".join(kept).strip()
    if not text:
        return None
    if len(text) <= TEXT_CHARS:
        return text
    cut = text[:TEXT_CHARS]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    if space > TEXT_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:\n") + "..."


def record_inbound(
    *,
    vendor_message_id: object,
    internet_message_id: object = "",
    sender: object = "",
    subject: object = "",
    received_at: float | None = None,
    thread_ref: object = "",
    body: object = "",
    path: str | None = None,
) -> bool:
    """Index one person's request at intake. First write wins (a redelivered
    message does not move its received time). Returns whether a row was
    written; never raises.

    ``thread_ref`` is the provider's conversation id, the same for every
    message in one thread, so a follow-up can be tied to the request whose job
    it is about. ``body`` is the person's own words (the reader's text, never
    the quoted history), kept as their opening the way a reply's is: when they
    write again on a HELD job, that opening is what SMD is paged with."""
    if not isinstance(vendor_message_id, str) or not vendor_message_id:
        return False
    now = time.time()
    try:
        conn = _connect(path or db_path())
    except Exception:  # noqa: BLE001 — a broken index costs a card, never an intake
        logger.error("request_index: cannot open the request index", exc_info=True)
        return False
    try:
        with conn:
            _custody(conn, now)
            cur = conn.execute(
                "INSERT OR IGNORE INTO requests (vendor_message_id, internet_message_id, "
                "received_at, sender, subject, thread_ref, request_opening, request_text)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    vendor_message_id[:512],
                    (internet_message_id[:512] if isinstance(internet_message_id, str) else "")
                    or None,
                    float(received_at if received_at is not None else now),
                    _clean(sender, SENDER_MAX),
                    _clean(subject, SUBJECT_MAX),
                    _clean(thread_ref, 512),
                    reply_opening(body),
                    message_text(body),
                ),
            )
        return cur.rowcount == 1
    except Exception:  # noqa: BLE001
        logger.error("request_index: record_inbound failed", exc_info=True)
        return False
    finally:
        conn.close()


def record_reply_opening(
    in_reply_to: object,
    body: object,
    *,
    sent_message_id: object = None,
    path: str | None = None,
) -> bool:
    """Keep the opening of the FIRST reply to a request. A later reply never
    overwrites it, and a reply to an unindexed message writes nothing there.

    With ``sent_message_id``, EVERY reply's opening is also kept in ``replies``
    under that id, first or not, indexed request or not: what a ``followup``
    card quotes. Returns whether the request row was updated; never raises."""
    sent_id = sent_message_id if isinstance(sent_message_id, str) and sent_message_id else None
    if (not isinstance(in_reply_to, str) or not in_reply_to) and sent_id is None:
        return False
    now = time.time()
    try:
        conn = _connect(path or db_path())
    except Exception:  # noqa: BLE001 — a broken index costs a card, never a send
        logger.error("request_index: cannot open the request index", exc_info=True)
        return False
    try:
        opening = reply_opening(body)
        text = message_text(body)
        updated = 0
        with conn:
            _custody(conn, now)
            if sent_id is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO replies"
                    " (sent_message_id, in_reply_to, sent_at, opening, text)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        sent_id,
                        in_reply_to if isinstance(in_reply_to, str) else None,
                        now,
                        opening,
                        text,
                    ),
                )
            if isinstance(in_reply_to, str) and in_reply_to:
                updated = conn.execute(
                    "UPDATE requests SET reply_opening=?, reply_text=?, opening_at=? "
                    "WHERE vendor_message_id=? AND opening_at IS NULL",
                    (opening, text, now, in_reply_to),
                ).rowcount
        return updated == 1
    except Exception:  # noqa: BLE001
        logger.error("request_index: record_reply_opening failed", exc_info=True)
        return False
    finally:
        conn.close()


def bare_sender(value: object) -> str | None:
    """A sender as its bare, lower-cased address, or ``None``.

    ``"Chris Price <Chris@Example.com>"`` and ``"chris@example.com"`` are the
    same person. The intake writers store ``InboundMessage.from_addr``, which
    ``shared.inbound_message`` already reduces to this form for both msgraph and
    AgentMail; the help pointer (ss-console#3423) normalizes again on both sides
    of every comparison so a writer that ever stored a display-name form could
    not split one person into two."""
    if not isinstance(value, str):
        return None
    return parseaddr(value)[1].strip().lower() or None


def _read_rows(target: str, columns: tuple[str, ...], where: str, params: tuple) -> list[dict]:
    """The shared read-only probe-and-project: SELECT the ``columns`` the file
    HAS (one read snapshot with the probe), return every name in ``columns``
    per row, None for one the file does not have yet. ``[]`` when the table
    does not exist. Raises on a file that cannot be read."""
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        conn.execute("BEGIN")
        present = {row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
        if not present:
            return []
        selected = [c for c in columns if c in present]
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM requests WHERE {where} ORDER BY received_at",
            params,
        ).fetchall()
        out = []
        for row in rows:
            record = dict.fromkeys(columns)
            record.update(zip(selected, row, strict=True))
            out.append(record)
        return out
    finally:
        conn.close()


def read_requests(since: float, *, path: str | None = None) -> list[dict]:
    """Every indexed request received at or after ``since``, READ-ONLY. Two
    readers, both in other code paths than the writer: the gate's heartbeat
    (request cards, another process), and since ss-console#3423 the agent-side
    initiation plugin's help pointer, through :func:`read_sender_requests`.
    ``[]`` when the file does not exist yet, or exists without the table
    (nothing has been indexed); raises on a file that exists and cannot be
    read, so the caller can tell "none" from "could not look" and skip the tick
    rather than decide on an empty list.

    The reader never assumes the writer has caught up: it selects the columns
    the file HAS (one read snapshot with the probe) and returns every column
    in :data:`_READ_COLUMNS`, None for one the file does not have yet. A seat
    that has indexed nothing since a deploy that added a column still cards.
    """
    target = path or db_path()
    if not os.path.exists(target):
        return []
    return _read_rows(target, _READ_COLUMNS, "received_at >= ?", (since,))


#: What :func:`read_sender_requests` returns: ids, time and thread, no text.
_SENDER_READ_COLUMNS = tuple(
    c for c in _READ_COLUMNS if c in ("vendor_message_id", "received_at", "thread_ref")
)


def read_sender_requests(sender: str, since: float, *, path: str | None = None) -> list[dict]:
    """One sender's indexed requests since ``since``: ids, times and thread only.

    The narrow read the help pointer uses (ss-console#3423): no subject, no
    opening, no address leaves this function. Matched on the bare lower-cased
    address (:func:`bare_sender`, both sides), so it sees only rows whose
    sender text custody has not yet nulled (:data:`TEXT_RETENTION_DAYS`); an
    older request from this person is invisible here, by design of that
    custody. READ-ONLY, and the same contract as :func:`read_requests`: ``[]``
    when the file or table does not exist yet, a column the writer has not
    added reads as None, and a file that exists but cannot be read RAISES so
    the caller can tell "none" from "could not look"."""
    target = path or db_path()
    want = bare_sender(sender)
    if want is None:
        return []
    if not os.path.exists(target):
        return []
    rows = _read_rows(
        target,
        (*_SENDER_READ_COLUMNS, "sender"),
        "received_at >= ? AND sender IS NOT NULL",
        (since,),
    )
    return [
        {c: r[c] for c in _SENDER_READ_COLUMNS} for r in rows if bare_sender(r["sender"]) == want
    ]


def read_reply_openings(since: float, *, path: str | None = None) -> dict[str, str | None]:
    """sent_message_id -> the opening of that reply, for replies sent at or
    after ``since``. READ-ONLY, same contract as :func:`read_requests`: ``{}``
    when the file or the table does not exist yet (an image older than the
    table wrote it), raises on a file that exists and cannot be read."""
    target = path or db_path()
    if not os.path.exists(target):
        return {}
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        if not conn.execute("PRAGMA table_info(replies)").fetchall():
            return {}
        rows = conn.execute(
            "SELECT sent_message_id, opening FROM replies WHERE sent_at >= ?", (since,)
        ).fetchall()
        return {str(r[0]): r[1] for r in rows}
    finally:
        conn.close()


def read_reply_texts(since: float, *, path: str | None = None) -> dict[str, str | None]:
    """sent_message_id -> the whole text of that reply, for replies sent at or
    after ``since``; None for a reply written before the column existed.
    READ-ONLY, the same contract as :func:`read_reply_openings`, and it projects
    against what the file HAS: the writer may not have added ``text`` yet."""
    target = path or db_path()
    if not os.path.exists(target):
        return {}
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        present = {row[1] for row in conn.execute("PRAGMA table_info(replies)").fetchall()}
        if "text" not in present:
            return {}
        rows = conn.execute(
            "SELECT sent_message_id, text FROM replies WHERE sent_at >= ?", (since,)
        ).fetchall()
        return {str(r[0]): r[1] for r in rows}
    finally:
        conn.close()


__all__ = [
    "DEFAULT_DB_PATH",
    "bare_sender",
    "converge",
    "read_sender_requests",
    "ROW_RETENTION_DAYS",
    "TEXT_RETENTION_DAYS",
    "db_path",
    "message_text",
    "read_reply_openings",
    "read_reply_texts",
    "read_requests",
    "record_inbound",
    "record_reply_opening",
    "reply_opening",
]
