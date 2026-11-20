"""A sender's signature block, learned from his own emails (2026-11-20, approved redesign).

WHY A WHOLE BLOCK. A plain yes is read by a grammar over the person's own text
(overlay ``shared/reply_shape``): affirmative lines, an optional closing, and,
optionally, HIS signature block, whole, nothing else. Judging signature lines one
at a time by their shape let instructions shaped like signature lines through
("Only For Craig LLC", "12 Point Font Avenue"). A signature is not a shape: it
is the same lines a person ends every email with (the firm's attorney: 34 emails,
one identical block). So the block is learned, not recognised:

* it is the longest run of lines (at least :data:`MIN_LINES`) that ends his own
  text identically in at least :data:`MIN_EMAILS` of his emails AND in a
  majority of the ones read (:func:`learn`);
* it comes ONLY from a root command SMD runs, at deploy and again on demand,
  reading his newest :data:`MAX_EMAILS` emails in the seat mailbox (``python -m
  workspace_broker.sender_block seed``, idempotent, printing per address only
  the block's line count and how many emails it matched, never the text);
* nothing is ever learned from a reply at runtime (review 2026-11-20: any
  learning from live mail let two emails teach the seat that a qualifier is
  part of his signature), and a new seed never takes the current block with
  lines added above or below it: a line he wrote under or over his signature in
  most recent emails is a line, not his signature.

HTML bodies are read as text by ``mail_text.html_to_text``, a byte copy of the
function the seat reads the same emails with (``shared/mail_text``), so his
signature gives the same lines in the seed and in his reply.

Pure learning (:func:`learn`, :func:`learn_from_texts`) is importable on its own,
for the replay of a saved mailbox copy. Lines are compared in one normal form
(:func:`norm_line`), the same the overlay applies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from typing import Any

from . import mail_text
from .broker_context import BrokerContext
from .establishment_validation import EstablishmentValidationError, require_address

MIN_LINES = 3
#: The fewest of his emails a block must end.
MIN_EMAILS = 3
#: How many of an email's last lines are compared.
MAX_TAIL = 24
#: How many of a sender's newest emails the seed reads.
MAX_EMAILS = 50

VERB_SEED = "establish_block_seed"
VERBS: tuple[str, ...] = (VERB_SEED,)

_CREATE = (
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


def learn(tails: list[list[str]]) -> tuple[list[str], int]:
    """``(block, emails it ends)``: the longest run of at least :data:`MIN_LINES`
    lines ending at least :data:`MIN_EMAILS` AND a majority of ``tails``;
    ``([], 0)`` when none."""
    tails = [t[-MAX_TAIL:] for t in tails if t]
    for k in range(MAX_TAIL, MIN_LINES - 1, -1):
        counts = Counter(tuple(t[-k:]) for t in tails if len(t) >= k)
        if not counts:
            continue
        block, count = counts.most_common(1)[0]
        if count >= MIN_EMAILS and 2 * count > len(tails):
            return list(block), count
    return [], 0


def learn_from_texts(texts: list[str], html: bool = False) -> tuple[list[str], int]:
    """:func:`learn` over each email's own text (quoted history already removed)."""
    return learn([lines_of(mail_text.html_to_text(t) if html else t) for t in texts])


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
            html = body.get("contentType") == "html"
            out.append((str(item["id"]), mail_text.html_to_text(content) if html else content))
    return out


def seed(broker: BrokerContext, request: dict[str, Any]) -> dict[str, Any]:
    """``establish_block_seed`` (root): learn each sender's block from the seat
    mailbox. Re-runnable and idempotent: the same mail gives the same block. Per
    sender, the block's line count, the emails it matched, the emails read, and
    ``kept`` when a candidate holding the current block with lines added above
    or below it was refused (the current block stays)."""
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
        block, matched = learn([lines_of(text)[-MAX_TAIL:] for _id, text in emails])
        current = block_for(pending, sender)
        kept = bool(block) and _widens(current, block)
        if block and not kept:
            conn = _connect(pending)
            try:
                with conn:
                    _store_block(conn, sender, block, matched)
            finally:
                conn.close()
        report[sender] = {"lines": len(block), "matched": matched, "read": len(emails), "kept": kept}
    return {"ok": True, "senders": report}


def _widens(current: list[str], candidate: list[str]) -> bool:
    """Is ``candidate`` the current block with lines on either side of it?"""
    k = len(current)
    if not current or len(candidate) <= k:
        return False
    return any(candidate[i : i + k] == current for i in range(len(candidate) - k + 1))


HANDLERS = {VERB_SEED: seed}


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
        kept = "; kept the current block, the candidate only adds lines to it" if row.get("kept") else ""
        print(f"{sender}: block {row['lines']} lines, matched {row['matched']} of {row['read']} emails{kept}")
    return 0


if __name__ == "__main__":  # pragma: no cover - the command line
    sys.exit(main())

__all__ = [
    "HANDLERS",
    "MAX_EMAILS",
    "MAX_TAIL",
    "MIN_EMAILS",
    "MIN_LINES",
    "VERBS",
    "block_for",
    "learn",
    "learn_from_texts",
    "lines_of",
    "main",
    "norm_line",
    "seed",
]
