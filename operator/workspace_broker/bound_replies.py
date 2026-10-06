"""The durable once-only record for the verified reply binding (reply_binding.py).

Its own module so the ORDINARY reply verb (transmit_verbs.msgraph) can consult
it without importing the binding: once an email has had its bound reply, a
held inbound reply released afterwards must not answer it a second time.

One row per binding key, in the broker-owned audit DB file:

    claimed     taken immediately before the POST; nothing has been sent yet
    sent        the POST returned
    unknown     the POST was attempted and failed in transport: it may have
                been delivered, so the claim stays and a person decides

The claim is taken AT the POST (reply_binding.py), so a refusal or a lookup
failure before it never spends the one reply. A POST Graph refuses with a 4xx,
with nothing in Sent Items since, is RELEASED (the row deleted).
"""

from __future__ import annotations

import sqlite3

from .audit_ledger import _iso_utc

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS bound_replies ("
    "binding_key TEXT PRIMARY KEY, "
    "graph_message_id TEXT NOT NULL, "
    "internet_message_id TEXT NOT NULL, "
    "claimed_at TEXT NOT NULL, "
    "session_id TEXT, "
    "outcome TEXT NOT NULL DEFAULT 'claimed'"
    ")"
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_bound_replies_graph ON bound_replies(graph_message_id)"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(CREATE_SQL)
    conn.execute(CREATE_INDEX_SQL)
    return conn


def claimed(db_path: str, key: str) -> bool:
    conn = _connect(db_path)
    try:
        return conn.execute("SELECT 1 FROM bound_replies WHERE binding_key=?", (key,)).fetchone() is not None
    finally:
        conn.close()


def claimed_for_graph_id(db_path: str, graph_message_id: str) -> bool:
    """Whether ANY bound reply has been claimed against this email."""
    if not graph_message_id:
        return False
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT 1 FROM bound_replies WHERE graph_message_id=?", (graph_message_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def claim(db_path: str, key: str, graph_message_id: str, internet_message_id: str, session_id: str) -> bool:
    """True exactly once per key: the PRIMARY KEY is the compare-and-set."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO bound_replies "
            "(binding_key, graph_message_id, internet_message_id, claimed_at, session_id) VALUES (?,?,?,?,?)",
            (key, graph_message_id, internet_message_id, _iso_utc(), session_id or None),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def release(db_path: str, key: str) -> None:
    """Undo a claim whose POST Graph refused outright (a 4xx, nothing sent,
    and nothing in Sent Items): the one reply stays available."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM bound_replies WHERE binding_key=?", (key,))
        conn.commit()
    finally:
        conn.close()


def settle(db_path: str, key: str, outcome: str) -> None:
    if outcome not in ("sent", "unknown"):
        raise ValueError(f"unknown bound reply outcome {outcome!r}")
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE bound_replies SET outcome=? WHERE binding_key=?", (outcome, key))
        conn.commit()
    finally:
        conn.close()


__all__ = ["claim", "claimed", "claimed_for_graph_id", "release", "settle"]
