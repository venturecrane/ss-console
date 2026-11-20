"""A sender's signature block, learned from his own emails (2026-11-20, approved redesign).

WHY A WHOLE BLOCK. A plain yes is read by a grammar over the person's own text
(overlay ``shared/reply_shape``): affirmative lines, an optional closing, and,
optionally, HIS signature block, whole, nothing else. Judging signature lines one
at a time by their shape let instructions shaped like signature lines through
("Only For Craig LLC", "12 Point Font Avenue"). A signature is not a shape: it
is the same lines a person ends every email with (the firm's attorney: 34 emails,
one identical block). So the block is learned, not recognised:

* it is the longest run of lines (at least :data:`MIN_LINES`) that ends his own
  text identically in at least two of his emails AND in a majority of the ones
  read (:func:`learn`);
* it is seeded at deploy by a root command reading the seat mailbox (``python
  -m workspace_broker.sender_block seed``, re-runnable and idempotent, printing
  per address only the block's line count and how many emails it matched,
  never the text);
* after deploy each email of his is observed (``establish_block_observe``): only
  hashes of his own text's last lines are kept, never the lines, and a newer
  block replaces the old only when the newest email and one other end with it
  identically, and it is not the old block with lines added on top (so a line
  written twice above his signature never joins it).

Pure learning (:func:`learn`, :func:`learn_from_texts`) is importable on its own,
for the replay of a saved mailbox copy. Lines are compared in one normal form
(:func:`norm_line`), the same the overlay applies.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
from collections import Counter
from typing import Any

from .broker_context import BrokerContext
from .establishment_validation import EstablishmentValidationError, require_address

MIN_LINES = 3
#: How many of an email's last lines are kept (as hashes) and compared.
MAX_TAIL = 24
#: How many of a sender's emails the seed reads, and the observer keeps.
MAX_EMAILS = 50

VERB_OBSERVE = "establish_block_observe"
VERB_SEED = "establish_block_seed"
VERBS: tuple[str, ...] = (VERB_OBSERVE, VERB_SEED)

_CREATE = (
    """CREATE TABLE IF NOT EXISTS sender_tails (
  sender TEXT NOT NULL, message_id TEXT NOT NULL, hashes TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (sender, message_id))""",
    """CREATE TABLE IF NOT EXISTS sender_block (
  sender TEXT PRIMARY KEY, lines TEXT NOT NULL, matched INTEGER NOT NULL, learned_at REAL NOT NULL)""",
)


def norm_line(line: str) -> str:
    """One line as blocks compare it: quotes folded, whitespace collapsed, lowercase."""
    text = str(line).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip().lower()


def lines_of(text: str) -> list[str]:
    """``text``'s non-blank lines in normal form."""
    return [n for n in (norm_line(raw) for raw in str(text or "").splitlines()) if n]


_BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>", re.IGNORECASE)


def text_of_html(markup: str) -> str:
    """An HTML body as lines of text (Graph's ``uniqueBody``)."""
    return html_lib.unescape(re.sub(r"<[^>]*>", "", _BLOCK_TAGS.sub("\n", str(markup or ""))))


def learn(tails: list[list[str]]) -> tuple[list[str], int]:
    """``(block, emails it ends)``: the longest run of at least :data:`MIN_LINES`
    lines ending at least two AND a majority of ``tails``; ``([], 0)`` when none."""
    tails = [t[-MAX_TAIL:] for t in tails if t]
    for k in range(MAX_TAIL, MIN_LINES - 1, -1):
        counts = Counter(tuple(t[-k:]) for t in tails if len(t) >= k)
        if not counts:
            continue
        block, count = counts.most_common(1)[0]
        if count >= 2 and 2 * count > len(tails):
            return list(block), count
    return [], 0


def learn_from_texts(texts: list[str], html: bool = False) -> tuple[list[str], int]:
    """:func:`learn` over each email's own text (quoted history already removed)."""
    return learn([lines_of(text_of_html(t) if html else t) for t in texts])


def _hashes(lines: list[str]) -> dict[str, str]:
    tail = lines[-MAX_TAIL:]
    return {
        str(k): hashlib.sha256("\n".join(tail[-k:]).encode("utf-8")).hexdigest()
        for k in range(MIN_LINES, len(tail) + 1)
    }


def _connect(pending: Any) -> Any:
    conn = pending._connect()
    for sql in _CREATE:
        conn.execute(sql)
    return conn


def block_for(pending: Any, sender: str) -> list[str]:
    """The sender's learned block, ``[]`` when none."""
    conn = _connect(pending)
    try:
        row = conn.execute("SELECT lines FROM sender_block WHERE sender = ?", (sender,)).fetchone()
    finally:
        conn.close()
    return [str(s) for s in json.loads(row[0])] if row else []


def _store_block(conn: Any, sender: str, block: list[str], matched: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sender_block (sender, lines, matched, learned_at) VALUES (?, ?, ?, ?)",
        (sender, json.dumps(block), matched, time.time()),
    )


def _store_tail(conn: Any, sender: str, message_id: str, lines: list[str]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sender_tails (sender, message_id, hashes, created_at) VALUES (?, ?, ?, ?)",
        (sender, message_id, json.dumps(_hashes(lines)), time.time()),
    )
    conn.execute(
        "DELETE FROM sender_tails WHERE sender = ? AND message_id NOT IN "
        "(SELECT message_id FROM sender_tails WHERE sender = ? ORDER BY created_at DESC LIMIT ?)",
        (sender, sender, MAX_EMAILS),
    )


def observe_lines(pending: Any, sender: str, message_id: str, lines: list[str]) -> bool:
    """Keep this email's tail hashes; learn or replace the block when the rule
    allows. True when the stored block changed."""
    conn = _connect(pending)
    try:
        with conn:
            _store_tail(conn, sender, message_id, lines)
            rows = conn.execute("SELECT hashes FROM sender_tails WHERE sender = ?", (sender,)).fetchall()
            others = [json.loads(r[0]) for r in rows]
            row = conn.execute("SELECT lines FROM sender_block WHERE sender = ?", (sender,)).fetchone()
            current = json.loads(row[0]) if row else []
            candidate = _candidate(lines, others, bool(current))
            if not candidate or candidate[0] == current:
                return False
            if current and candidate[0][-len(current) :] == current:
                return False  # the old block with lines added on top: never
            _store_block(conn, sender, candidate[0], candidate[1])
            return True
    finally:
        conn.close()


def _candidate(lines: list[str], tails: list[dict[str, str]], replacing: bool) -> tuple[list[str], int] | None:
    """The longest suffix of THIS email shared by another (and, when nothing is
    learned yet, by a majority of the stored emails)."""
    mine = _hashes(lines)
    tail = lines[-MAX_TAIL:]
    for k in range(len(tail), MIN_LINES - 1, -1):
        count = sum(1 for t in tails if t.get(str(k)) == mine[str(k)])
        if count >= 2 and (replacing or 2 * count > len(tails)):
            return tail[-k:], count
    return None


def observe(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """``establish_block_observe``: one email of the sender's, his own text's last
    lines (the overlay's own-text cut). Only hashes are kept, and the block."""
    from .readback_bindings import _pending_store

    sender = require_address(request.get("sender"), "sender")
    message_id = str(request.get("message_id") or "").strip()
    raw = request.get("lines")
    if not message_id or len(message_id) > 512 or not isinstance(raw, list) or len(raw) > MAX_TAIL:
        raise EstablishmentValidationError(f"message_id and up to {MAX_TAIL} lines are required")
    lines = [n for n in (norm_line(s) for s in raw if isinstance(s, str)) if n]
    changed = observe_lines(_pending_store(broker), sender, message_id, lines)
    return {"ok": True, "changed": changed}


def _graph_own_texts(ops: Any, sender: str, limit: int) -> list[tuple[str, str]]:
    """``(message id, own text)`` of the sender's newest ``limit`` emails in this
    mailbox: Graph's ``uniqueBody`` is the part of a message not quoted."""
    from .msgraph_lookup import _quoted, _read

    query = (
        "?$filter=receivedDateTime ge 1900-01-01T00:00:00Z and "
        f"from/emailAddress/address eq '{_quoted(sender)}'"
        f"&$orderby=receivedDateTime desc&$top={int(limit)}&$select=id,uniqueBody"
    )
    found = _read(ops, ops._mail_path("messages") + query.replace(" ", "%20"))
    value = found.get("value") if isinstance(found, dict) else None
    out: list[tuple[str, str]] = []
    for item in value if isinstance(value, list) else []:
        body = item.get("uniqueBody") if isinstance(item, dict) else None
        if isinstance(body, dict) and item.get("id"):
            content = str(body.get("content") or "")
            out.append((str(item["id"]), text_of_html(content) if body.get("contentType") == "html" else content))
    return out


def seed(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """``establish_block_seed`` (root): learn each sender's block from the seat
    mailbox. Re-runnable and idempotent: the same mail gives the same block. Per
    sender, the block's line count, the emails it matched, the emails read."""
    from .readback_bindings import _pending_store

    pending = _pending_store(broker)
    senders = [require_address(s, "sender") for s in request.get("senders") or []]
    limit = min(int(request.get("limit") or MAX_EMAILS), MAX_EMAILS)
    ops = getattr(broker, "msgraph", None)
    if ops is None:
        raise EstablishmentValidationError("this seat's broker has no mailbox to read")
    report: dict[str, dict[str, int]] = {}
    for sender in senders:
        emails = _graph_own_texts(ops, sender, limit)
        tails = [lines_of(text)[-MAX_TAIL:] for _id, text in emails]
        block, matched = learn(tails)
        conn = _connect(pending)
        try:
            with conn:
                for (message_id, _text), lines in zip(emails, tails, strict=True):
                    _store_tail(conn, sender, message_id, lines)
                if block:
                    _store_block(conn, sender, block, matched)
        finally:
            conn.close()
        report[sender] = {"lines": len(block), "matched": matched, "read": len(emails)}
    return {"ok": True, "senders": report}


HANDLERS = {VERB_OBSERVE: observe, VERB_SEED: seed}


def main(argv: list[str] | None = None) -> int:
    """``python -m workspace_broker.sender_block seed --sender a@x [--sender ...]``
    (root, on the seat). Prints per address: lines, emails matched, emails read."""
    from . import broker_socket, ops_close, seat_layout

    ap = argparse.ArgumentParser(prog="workspace_broker.sender_block")
    ap.add_argument("command", choices=["seed"])
    ap.add_argument("--sender", action="append", default=[], help="a roster address; repeat for each")
    ap.add_argument("--limit", type=int, default=MAX_EMAILS)
    ap.add_argument("--socket", default=os.environ.get(ops_close.SOCKET_ENV, seat_layout.BROKER_SOCKET))
    args = ap.parse_args(argv)
    if not args.sender:
        print("sender_block: name at least one --sender", file=sys.stderr)
        return 2
    request = {"action": VERB_SEED, "senders": args.sender, "limit": args.limit}
    try:
        raw = broker_socket.request(args.socket, request, timeout=300.0, separators=(",", ":"))
    except OSError as exc:
        print(f"sender_block: the broker could not be reached ({type(exc).__name__})", file=sys.stderr)
        return 3
    answer = json.loads(bytes(raw).decode("utf-8"))
    if not isinstance(answer, dict) or not answer.get("ok"):
        print(f"sender_block: refused: {answer.get('error') if isinstance(answer, dict) else answer}", file=sys.stderr)
        return 2
    for sender, row in sorted((answer.get("senders") or {}).items()):
        print(f"{sender}: block {row['lines']} lines, matched {row['matched']} of {row['read']} emails")
    return 0


if __name__ == "__main__":  # pragma: no cover - the command line
    sys.exit(main())

__all__ = [
    "HANDLERS",
    "MAX_EMAILS",
    "MAX_TAIL",
    "MIN_LINES",
    "VERBS",
    "block_for",
    "learn",
    "learn_from_texts",
    "lines_of",
    "main",
    "norm_line",
    "observe",
    "observe_lines",
    "seed",
    "text_of_html",
]
