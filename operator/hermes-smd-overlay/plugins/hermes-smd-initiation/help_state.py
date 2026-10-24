"""The quiet help pointer's memory (ss-console#3423, increment 2).

THE INTENT. The Operator should help people discover that they can ask it for
help, without being noisy or repetitive. One line on a person's FIRST email to
the seat; after that, only on an objective struggle in a thread, once per
thread; never again for someone who has asked for help or has made ten
requests. This module holds the only state those rules need, computes the
signals, and owns the exact words of the two lines (:data:`POINTER_LINE`,
:data:`STRUGGLE_LINE`). The initiation plugin's per-turn hook calls
:func:`sender_signals` itself on a seat that binds operator-help and, when a
line is due, hands the reply that line verbatim. The model never decides WHEN
and never has to remember to ask: the first live increment told the model to
call a tool on every email turn, and on pilot-smokeball an ordinary question
was answered with no call and no line (ss-console#3423).

ONCE MEANS ONCE, DURABLY. The moment :func:`sender_signals` reports
``pointer_due`` (or ``struggle_offer_due`` for a thread), it records the offer
in the same write transaction that read the state. Nothing depends on the model
remembering to call ``operator_help_note`` afterwards: a skipped note can never
make the line come back once the person's older mail ages out of the request
index. The cost is deliberate and in the quiet direction: if the model is told
the line is due and leaves it out, the person is not offered it again. A write
that fails reports nothing due.

WHAT IS STORED, AND WHAT IS NOT. A seat-local sqlite file
(``/opt/data/smd/help_state.db``) with three tables:

* ``help_meta``: one random salt, generated on the first write and never
  leaving the file;
* ``help_senders``: per person, when the first-email pointer was offered and
  when they were helped;
* ``help_threads``: per (person, thread), when a struggle offer was made.

The person and the thread are stored only as sha256 hashes taken with that
per-seat salt (the person's bare lower-cased address, the thread's provider
reference). No address, subject, text or matter number is stored in the clear,
and the salt means a hash cannot be matched against a list of known addresses
without the file's own salt. Rows older than :data:`RETENTION_DAYS` are deleted
on every write.

WHERE THE REQUEST COUNTS COME FROM. ``requests.db`` (``shared.request_index``),
read-only, through :func:`shared.request_index.read_sender_requests`. That
file's sender column is nulled after its text custody window
(:data:`shared.request_index.TEXT_RETENTION_DAYS`, 7 days), so a request can
be attributed to a person for 7 days and no longer. :data:`LOOKBACK_DAYS` IS
that window, not a longer number the index could never honor: "prior
requests" means prior requests in the last 7 days. The consequences are
deliberate and conservative: ``pointer_due`` is also gated on the durable
``pointer_offered`` mark (kept here for 90 days), so a person is never pointed
twice inside that window, and the ten-request stop applies within 7 days.

HOW THE TURN'S OWN MESSAGE IS EXCLUDED. The webhook router indexes the inbound
BEFORE the turn runs, so a naive count would always see "1" for a first-timer.
The turn's own email is the origin :meth:`SessionInboundOrigin.bound_this_turn`
names; its ``message_id`` is the vendor message id the router keyed the index
row on (both come from the same seam DTO ``message_id``). That row is dropped
by id before counting, and it names the current thread.

A MESSAGE THE INDEX NEVER SAW GETS NOTHING. The router does not index RFC 3834
auto-submitted mail (an out-of-office, a vendor notice), nor anything from off
the roster. When the turn's own message is not in the index, nothing is due:
an auto-reply is not a person to point at anything.

JOB STATE IS NOT READ. A held or failed job would be a stronger struggle signal,
but the job ledgers live in the gate's audit file and behind the broker socket,
neither readable from the agent's tool context without content. The struggle
signal here is repetition only: at least :data:`REPEAT_ASK_PRIOR` earlier
emails from this person in this thread (so the current one is at least the
fourth), and the output names that basis (``struggle_basis``). A routine
back-and-forth (ask, "shorter please", "thanks") does not reach it.

NEVER RAISES. Every public function returns a value; a broken file costs a
pointer, never a reply. A read fault is reported as such (``*_read: false``)
and every ``*_due`` flag is then False: "could not look" never reads as "first
time".
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
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
#: How far back prior requests are counted: exactly the request index's sender
#: custody, the only window in which a request can be attributed to a person.
LOOKBACK_DAYS = request_index.TEXT_RETENTION_DAYS
#: No pointer and no offer once a person has made this many requests.
STOP_AFTER_REQUESTS = 10
#: Earlier emails from this person in this thread (the current one excluded)
#: that make the current one a struggle: the current is at least the fourth.
#: Ask, follow-up, thanks is three and is conversation, not struggle.
REPEAT_ASK_PRIOR = 3
#: What the struggle signal is computed from, said plainly in the output.
STRUGGLE_BASIS = "repeated_messages_in_thread"

#: The one line appended to a person's first email reply. THE ONLY copy of it:
#: the per-turn hook hands it to the reply verbatim. No em dash, no braces.
POINTER_LINE = (
    "If you're ever unsure how to ask me for something, just ask me how we can work together."
)
#: The one line appended, once per thread, when a person is visibly struggling.
STRUGGLE_LINE = (
    "If it would help, ask me how to phrase this kind of request and I'll walk you through it."
)

KIND_POINTER = "pointer_offered"
KIND_STRUGGLE = "struggle_offer"
KIND_HELPED = "help_given"
#: The only kinds the note tool accepts.
KINDS: tuple[str, ...] = (KIND_POINTER, KIND_STRUGGLE, KIND_HELPED)
#: The kinds :func:`sender_signals` records itself, at the moment it reports
#: them due. The note tool accepts them as no-ops.
KINDS_RECORDED_BY_SIGNALS: tuple[str, ...] = (KIND_POINTER, KIND_STRUGGLE)

SCOPE_SENDER = "sender"
#: Scopes the signals tool answers. Increment 3 adds "review" (seat-wide counts
#: for the self-improving guide); it is a new branch in :func:`signals`, never a
#: change to the sender scope's shape.
SCOPES: tuple[str, ...] = (SCOPE_SENDER,)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS help_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
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

_SALT_KEY = "salt"


def db_path() -> Path:
    """``SMD_HELP_STATE_DB_PATH`` (tests/dev; a literal read so the consumes
    contract scan sees it) or the volume default."""
    return Path(os.environ.get("SMD_HELP_STATE_DB_PATH") or DEFAULT_DB_PATH)


def _digest(salt: str, label: str, value: str) -> str:
    return hashlib.sha256(f"{salt}\x00{label}\x00{value}".encode()).hexdigest()


def sender_key(address: object, salt: str) -> str | None:
    """Salted sha256 of the bare lower-cased address, or ``None`` for none.

    ``"Chris Price <Chris@Example.com>"`` and ``chris@example.com`` hash the same."""
    bare = request_index.bare_sender(address)
    if bare is None:
        return None
    return _digest(salt, "sender", bare)


def thread_key(thread_ref: object, salt: str) -> str | None:
    """Salted sha256 of the thread reference, or ``None`` for no thread."""
    if not isinstance(thread_ref, str) or not thread_ref.strip():
        return None
    return _digest(salt, "thread", thread_ref.strip())


def _connect(path: Path) -> sqlite3.Connection:
    """A writer connection in autocommit mode; callers open ``BEGIN IMMEDIATE``
    so a read-decide-record runs as one serialized transaction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=5000")
    for stmt in _SCHEMA:
        conn.execute(stmt)
    return conn


def _salt(conn: sqlite3.Connection, *, create: bool) -> str | None:
    """This seat's salt; generated once on the first write when ``create``."""
    row = conn.execute("SELECT value FROM help_meta WHERE key = ?", (_SALT_KEY,)).fetchone()
    if row is not None:
        return row[0]
    if not create:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO help_meta (key, value) VALUES (?, ?)",
        (_SALT_KEY, secrets.token_hex(32)),
    )
    return conn.execute("SELECT value FROM help_meta WHERE key = ?", (_SALT_KEY,)).fetchone()[0]


def _retain(conn: sqlite3.Connection, now: float) -> None:
    cutoff = now - RETENTION_DAYS * 86400.0
    conn.execute("DELETE FROM help_threads WHERE offered_at < ?", (cutoff,))
    conn.execute("DELETE FROM help_senders WHERE updated_at < ?", (cutoff,))


def _state(conn: sqlite3.Connection, skey: str, tkey: str | None, now: float) -> dict:
    out = {"read": True, "pointer_offered": False, "helped": False, "offered_in_thread": False}
    cutoff = now - RETENTION_DAYS * 86400.0
    row = conn.execute(
        "SELECT pointer_offered_at, helped_at FROM help_senders "
        "WHERE sender_hash = ? AND updated_at >= ?",
        (skey, cutoff),
    ).fetchone()
    if row is not None:
        out["pointer_offered"] = row[0] is not None
        out["helped"] = row[1] is not None
    if tkey is not None:
        row = conn.execute(
            "SELECT 1 FROM help_threads WHERE sender_hash = ? AND thread_hash = ? "
            "AND offered_at >= ?",
            (skey, tkey, cutoff),
        ).fetchone()
        out["offered_in_thread"] = row is not None
    return out


def _write(conn: sqlite3.Connection, kind: str, skey: str, tkey: str | None, now: float) -> None:
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
            "UPDATE help_senders SET helped_at = COALESCE(helped_at, ?) WHERE sender_hash = ?",
            (now, skey),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO help_threads (sender_hash, thread_hash, offered_at) "
            "VALUES (?, ?, ?)",
            (skey, tkey, now),
        )


def read_sender(sender: str, thread_ref: str | None, *, path: Path | None = None) -> dict:
    """``{read, pointer_offered, helped, offered_in_thread}`` for one person.

    READ-ONLY. A file that does not exist yet, or has no salt yet (nothing was
    ever recorded), is "nothing offered" (read: true). A file that exists and
    cannot be read is ``read: false``. Never raises."""
    target = path or db_path()
    out = {"read": False, "pointer_offered": False, "helped": False, "offered_in_thread": False}
    if request_index.bare_sender(sender) is None:
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
        if not {"help_meta", "help_senders", "help_threads"} <= tables:
            out["read"] = True
            return out
        salt = _salt(conn, create=False)
        if salt is None:
            out["read"] = True
            return out
        skey = sender_key(sender, salt)
        assert skey is not None  # bare_sender checked above
        return _state(conn, skey, thread_key(thread_ref, salt), time.time())
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
    if request_index.bare_sender(sender) is None:
        return False
    if kind == KIND_STRUGGLE and not (isinstance(thread_ref, str) and thread_ref.strip()):
        return False
    now = time.time()
    try:
        conn = _connect(path or db_path())
    except Exception:  # noqa: BLE001 — a broken file costs a pointer, never a reply
        logger.warning("help_state: cannot open the help state", exc_info=True)
        return False
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            salt = _salt(conn, create=True)
            assert salt is not None
            _retain(conn, now)
            skey = sender_key(sender, salt)
            assert skey is not None
            _write(conn, kind, skey, thread_key(thread_ref, salt), now)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return True
    except Exception:  # noqa: BLE001
        logger.warning("help_state: record failed", exc_info=True)
        return False
    finally:
        conn.close()


def sender_signals(
    sender: str,
    message_id: str,
    *,
    path: Path | None = None,
    requests_path: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """The sender-scope signals for one resolved person. Never raises.

    ``message_id`` is the turn's own anchored vendor message id: its index row
    is excluded from every count and supplies the current thread. When a
    ``*_due`` flag comes back true, the offer is ALREADY recorded (same
    transaction), so it is reported due exactly once. The output carries
    booleans and counts only."""
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
    indexed = bool(own)
    thread = own[0].get("thread_ref") if own else None
    if not (isinstance(thread, str) and thread):
        thread = None
    prior_in_thread = sum(1 for r in prior if thread is not None and r.get("thread_ref") == thread)

    first_time = requests_read and not prior
    struggle = requests_read and prior_in_thread >= REPEAT_ASK_PRIOR
    state = {"read": False, "pointer_offered": False, "helped": False, "offered_in_thread": False}
    pointer_due = False
    struggle_due = False
    try:
        conn = _connect(path or db_path())
    except Exception:  # noqa: BLE001 — a broken file costs a pointer, never a reply
        logger.warning("help_state: cannot open the help state", exc_info=True)
        conn = None
    if conn is not None:
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                salt = _salt(conn, create=True)
                assert salt is not None
                _retain(conn, ts)
                skey = sender_key(sender, salt)
                if skey is None:
                    raise ValueError("no sender address")
                tkey = thread_key(thread, salt)
                state = _state(conn, skey, tkey, ts)
                stopped_now = state["helped"] or len(prior) >= STOP_AFTER_REQUESTS
                eligible = requests_read and indexed and not stopped_now
                pointer_due = eligible and first_time and not state["pointer_offered"]
                struggle_due = (
                    eligible and tkey is not None and struggle and not state["offered_in_thread"]
                )
                if pointer_due:
                    _write(conn, KIND_POINTER, skey, tkey, ts)
                if struggle_due:
                    _write(conn, KIND_STRUGGLE, skey, tkey, ts)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        except Exception:  # noqa: BLE001 — a failed record must never report "due"
            logger.warning("help_state: state read/record failed", exc_info=True)
            state = {**state, "read": False}
            pointer_due = False
            struggle_due = False
        finally:
            conn.close()

    stopped = state["helped"] or len(prior) >= STOP_AFTER_REQUESTS
    return {
        "resolved": True,
        "requests_read": requests_read,
        "state_read": state["read"],
        "this_message_indexed": indexed,
        "first_time": first_time,
        "prior_requests": len(prior),
        "pointer_offered": state["pointer_offered"],
        "helped": state["helped"],
        "thread_known": thread is not None,
        "offered_in_thread": state["offered_in_thread"],
        "struggle_in_thread": struggle,
        "struggle_basis": STRUGGLE_BASIS,
        "stopped": stopped,
        "pointer_due": bool(pointer_due),
        "struggle_offer_due": bool(struggle_due),
    }


__all__ = [
    "DB_PATH_ENV",
    "DEFAULT_DB_PATH",
    "KINDS",
    "KINDS_RECORDED_BY_SIGNALS",
    "KIND_HELPED",
    "KIND_POINTER",
    "KIND_STRUGGLE",
    "LOOKBACK_DAYS",
    "POINTER_LINE",
    "REPEAT_ASK_PRIOR",
    "RETENTION_DAYS",
    "SCOPES",
    "SCOPE_SENDER",
    "STOP_AFTER_REQUESTS",
    "STRUGGLE_BASIS",
    "STRUGGLE_LINE",
    "db_path",
    "read_sender",
    "record",
    "sender_key",
    "sender_signals",
    "thread_key",
]
