"""The quiet help pointer's memory (ss-console#3423, increment 2).

THE INTENT. The Operator should help people discover that they can ask it for
help, without being noisy. One line on a person's FIRST email to the seat; after
that, only on an objective struggle in a thread, once per thread; never again
for someone who has asked for help or has made ten requests. The rules are
authored in the inbound email prompt (``bootstrap/translate.py``); this module
holds the only state they need and computes the signals, so the model applies
booleans the platform decided instead of judging "is this their first time?"
from a conversation it cannot see.

WHAT IS STORED, AND WHAT IS NOT. A seat-local sqlite file
(``/opt/data/smd/help_state.db``) with two tables:

* ``help_senders``: per person, when the first-email pointer was offered and
  when they were helped;
* ``help_threads``: per (person, thread), when a struggle offer was made.

The person is keyed by the sha256 of their lower-cased address and the thread
by the sha256 of its provider reference. No address, no subject, no text and no
matter number is ever written: the file answers "has this hash been offered?"
and nothing else. Rows older than :data:`RETENTION_DAYS` are deleted on every
write.

WHERE THE REQUEST COUNTS COME FROM. ``requests.db`` (``shared.request_index``),
read-only, through :func:`shared.request_index.read_sender_requests`. That file's
sender column is nulled after its own text custody window (7 days), so "prior
requests" counts what the index can still attribute: at most the last 7 days,
never 30. The consequences are deliberate and conservative: ``first_time`` is
also gated on ``pointer_offered`` (kept here for 90 days), so a person is never
pointed twice inside that window; the ten-request stop applies within the
index's window.

HOW THE TURN'S OWN MESSAGE IS EXCLUDED. The webhook router indexes the inbound
BEFORE the turn runs, so a naive count would always see "1" for a first-timer.
The turn's own email is the origin :meth:`SessionInboundOrigin.bound_this_turn`
names; its ``message_id`` is the vendor message id the router keyed the index
row on (both come from the same seam DTO ``message_id``). That row is dropped
by id before counting. The current thread is read from that same row, falling
back to the origin's conversation id when the row is absent.

JOB STATE IS NOT READ. A held or failed job would be a stronger struggle signal,
but the job ledgers live in the gate's audit file and behind the broker socket,
neither readable from the agent's tool context without content. The struggle
signal here is the repeat ask only, and the output says so
(``struggle_basis``).

NEVER RAISES. Every public function returns a value; a broken file costs a
pointer, never a reply. A read fault is reported as such (``*_read: false``)
and every ``*_due`` flag is then False: "could not look" never reads as "first
time".
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from shared import request_index

logger = logging.getLogger(__name__)

DB_PATH_ENV = "SMD_HELP_STATE_DB_PATH"
DEFAULT_DB_PATH = "/opt/data/smd/help_state.db"

#: Offer rows are deleted after this.
RETENTION_DAYS = 90
#: How far back prior requests are counted. The index's own sender custody is
#: shorter (7 days), and is the effective bound; see the module docstring.
LOOKBACK_DAYS = 30
#: No pointer and no offer once a person has made this many requests.
STOP_AFTER_REQUESTS = 10
#: Prior asks from this person in this thread (the current one excluded) that
#: make the current one a repeat ask, i.e. at least the third in the thread. A
#: normal follow-up ("make it shorter") is the second, and is not a struggle.
REPEAT_ASK_PRIOR = 2

KIND_POINTER = "pointer_offered"
KIND_STRUGGLE = "struggle_offer"
KIND_HELPED = "help_given"
#: The only kinds the note tool accepts.
KINDS: tuple[str, ...] = (KIND_POINTER, KIND_STRUGGLE, KIND_HELPED)

SCOPE_SENDER = "sender"
#: Scopes the signals tool answers. Increment 3 adds "review" (seat-wide counts
#: for the self-improving guide); it is a new branch in :func:`signals`, never a
#: change to the sender scope's shape.
SCOPES: tuple[str, ...] = (SCOPE_SENDER,)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS help_senders ("
    " sender_hash TEXT PRIMARY KEY,"
    " pointer_offered_at REAL,"
    " helped_at REAL,"
    " updated_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS help_threads ("
    " sender_hash TEXT NOT NULL,"
    " thread_hash TEXT NOT NULL,"
    " offered_at REAL NOT NULL,"
    " PRIMARY KEY (sender_hash, thread_hash))",
)


def db_path() -> Path:
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


def _hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    norm = value.strip().lower()
    if not norm:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def sender_key(address: object) -> str | None:
    """sha256 of the lower-cased address, or ``None`` for no address."""
    return _hash(address)


def thread_key(thread_ref: object) -> str | None:
    """sha256 of the thread reference, or ``None`` for no thread."""
    if not isinstance(thread_ref, str) or not thread_ref.strip():
        return None
    return hashlib.sha256(thread_ref.strip().encode("utf-8")).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    for stmt in _SCHEMA:
        conn.execute(stmt)
    return conn


def _retain(conn: sqlite3.Connection, now: float) -> None:
    cutoff = now - RETENTION_DAYS * 86400.0
    conn.execute("DELETE FROM help_threads WHERE offered_at < ?", (cutoff,))
    conn.execute("DELETE FROM help_senders WHERE updated_at < ?", (cutoff,))


def read_sender(sender: str, thread_ref: str | None, *, path: Path | None = None) -> dict:
    """``{read, pointer_offered, helped, offered_in_thread}`` for one person.

    A file that does not exist yet is "nothing offered" (read: true). A file
    that exists and cannot be read is ``read: false``. Never raises."""
    target = path or db_path()
    out = {"read": False, "pointer_offered": False, "helped": False, "offered_in_thread": False}
    skey = sender_key(sender)
    if skey is None:
        return out
    if not target.exists():
        out["read"] = True
        return out
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
    except Exception:  # noqa: BLE001
        logger.warning("help_state: cannot open %s", target, exc_info=True)
        return out
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cutoff = time.time() - RETENTION_DAYS * 86400.0
        if "help_senders" in tables:
            row = conn.execute(
                "SELECT pointer_offered_at, helped_at FROM help_senders "
                "WHERE sender_hash = ? AND updated_at >= ?",
                (skey, cutoff),
            ).fetchone()
            if row is not None:
                out["pointer_offered"] = row[0] is not None
                out["helped"] = row[1] is not None
        tkey = thread_key(thread_ref)
        if tkey is not None and "help_threads" in tables:
            row = conn.execute(
                "SELECT 1 FROM help_threads WHERE sender_hash = ? AND thread_hash = ? "
                "AND offered_at >= ?",
                (skey, tkey, cutoff),
            ).fetchone()
            out["offered_in_thread"] = row is not None
        out["read"] = True
        return out
    except Exception:  # noqa: BLE001 — "could not look" is reported, never raised
        logger.warning("help_state: read failed", exc_info=True)
        return {**out, "read": False}
    finally:
        conn.close()


def record(
    kind: str, sender: str, thread_ref: str | None = None, *, path: Path | None = None
) -> bool:
    """Record one offer or help against a person (and thread). Returns whether
    it was written; never raises. Unknown ``kind`` writes nothing."""
    if kind not in KINDS:
        return False
    skey = sender_key(sender)
    if skey is None:
        return False
    tkey = thread_key(thread_ref)
    if kind == KIND_STRUGGLE and tkey is None:
        return False
    now = time.time()
    try:
        conn = _connect(path or db_path())
    except Exception:  # noqa: BLE001 — a broken file costs a pointer, never a reply
        logger.warning("help_state: cannot open the help state", exc_info=True)
        return False
    try:
        with conn:
            _retain(conn, now)
            conn.execute(
                "INSERT INTO help_senders (sender_hash, updated_at) VALUES (?, ?) "
                "ON CONFLICT(sender_hash) DO UPDATE SET updated_at = excluded.updated_at",
                (skey, now),
            )
            if kind == KIND_POINTER:
                conn.execute(
                    "UPDATE help_senders SET pointer_offered_at = COALESCE(pointer_offered_at, ?) "
                    "WHERE sender_hash = ?",
                    (now, skey),
                )
            elif kind == KIND_HELPED:
                conn.execute(
                    "UPDATE help_senders SET helped_at = COALESCE(helped_at, ?) "
                    "WHERE sender_hash = ?",
                    (now, skey),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO help_threads (sender_hash, thread_hash, offered_at) "
                    "VALUES (?, ?, ?)",
                    (skey, tkey, now),
                )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("help_state: record failed", exc_info=True)
        return False
    finally:
        conn.close()


def _thread_of(own: list[dict], conversation_id: str | None) -> str | None:
    """The current thread: the turn's own index row first, else the origin's
    conversation id, else ``None``."""
    if own and isinstance(own[0].get("thread_ref"), str) and own[0]["thread_ref"]:
        return own[0]["thread_ref"]
    if isinstance(conversation_id, str) and conversation_id.strip():
        return conversation_id.strip()
    return None


def current_thread(
    sender: str,
    message_id: str,
    conversation_id: str | None = None,
    *,
    requests_path: str | None = None,
) -> str | None:
    """The thread the turn's own email belongs to, or ``None``. Never raises."""
    try:
        rows = request_index.read_sender_requests(
            sender, time.time() - LOOKBACK_DAYS * 86400.0, path=requests_path
        )
    except Exception:  # noqa: BLE001
        rows = []
    own = [r for r in rows if r.get("vendor_message_id") == message_id]
    return _thread_of(own, conversation_id)


def sender_signals(
    sender: str,
    message_id: str,
    conversation_id: str | None = None,
    *,
    path: Path | None = None,
    requests_path: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """The sender-scope signals for one resolved person. Never raises.

    ``message_id`` is the turn's own anchored vendor message id: its index row
    is excluded from every count and supplies the current thread. The output
    carries booleans and counts only."""
    ts = time.time() if now is None else now
    requests_read = True
    rows: list[dict] = []
    try:
        rows = request_index.read_sender_requests(
            sender, ts - LOOKBACK_DAYS * 86400.0, path=requests_path
        )
    except Exception:  # noqa: BLE001 — "could not look" is not "none"
        logger.warning("help_state: request index unreadable", exc_info=True)
        requests_read = False

    own = [r for r in rows if r.get("vendor_message_id") == message_id]
    prior = [r for r in rows if r.get("vendor_message_id") != message_id]
    thread = _thread_of(own, conversation_id)

    prior_in_thread = (
        sum(1 for r in prior if thread is not None and r.get("thread_ref") == thread)
        if thread is not None
        else 0
    )
    state = read_sender(sender, thread, path=path)

    first_time = requests_read and not prior
    struggle = requests_read and prior_in_thread >= REPEAT_ASK_PRIOR
    stopped = state["helped"] or len(prior) >= STOP_AFTER_REQUESTS
    readable = requests_read and state["read"]
    return {
        "resolved": True,
        "requests_read": requests_read,
        "state_read": state["read"],
        "first_time": first_time,
        "prior_requests": len(prior),
        "pointer_offered": state["pointer_offered"],
        "helped": state["helped"],
        "thread_known": thread is not None,
        "offered_in_thread": state["offered_in_thread"],
        "struggle_in_thread": struggle,
        "struggle_basis": "repeat_ask_in_thread",
        "stopped": stopped,
        "pointer_due": bool(
            readable and first_time and not state["pointer_offered"] and not stopped
        ),
        "struggle_offer_due": bool(
            readable
            and thread is not None
            and struggle
            and not state["offered_in_thread"]
            and not stopped
        ),
    }


__all__ = [
    "DB_PATH_ENV",
    "DEFAULT_DB_PATH",
    "KINDS",
    "KIND_HELPED",
    "KIND_POINTER",
    "KIND_STRUGGLE",
    "LOOKBACK_DAYS",
    "REPEAT_ASK_PRIOR",
    "RETENTION_DAYS",
    "SCOPES",
    "SCOPE_SENDER",
    "STOP_AFTER_REQUESTS",
    "current_thread",
    "db_path",
    "read_sender",
    "record",
    "sender_key",
    "sender_signals",
    "thread_key",
]
