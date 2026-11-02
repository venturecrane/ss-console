"""SMD review of a reply that carries matter files, before it is sent (ADR 0102).

WHY. The Captain wants to see what the Operator will send, and to whom, before
a client's files leave the seat. A seat authors that with
``matter_file_delivery.review: smd``. Every reply carrying matter files is then
written here instead of sent: who it goes to, the words, and for each file its
Smokeball file id, attachment name, page range, page count, size and sha256.
SMD reads it, and either approves it (the seat then sends exactly that, the
same broker-spool handles and hashes) or rejects it (it is discarded).

WHO DECIDES. The held row is written by the gateway (the agent uid). The
decision is a file written by ROOT under :data:`DECISIONS_DIR`, which the agent
uid cannot write: the gateway acts only on a decision file owned by uid 0 that
names the row's digest. So a turn cannot approve its own reply, and a decision
for a row that changed after SMD read it matches nothing.

USAGE, as root on the seat::

    python -m shared.matter_file_review list
    python -m shared.matter_file_review approve <id> <digest>
    python -m shared.matter_file_review reject <id> <digest>

The gateway's held-reply sweeper thread acts on a decision within a minute.
A held row expires after :data:`TTL_SECONDS` (the broker spool keeps the files
that long); approving an expired row is refused and nothing is sent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any

REVIEW_DIR_ENV = "SMD_MATTER_FILE_REVIEW_DIR"
DEFAULT_REVIEW_DIR = "/opt/data/matter-file-review"
DECISIONS_DIR_ENV = "SMD_MATTER_FILE_DECISIONS_DIR"
DEFAULT_DECISIONS_DIR = "/var/lib/smd-review"
TTL_SECONDS = 24 * 60 * 60
_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DECISIONS = ("approve", "reject")

#: The REPLY_HELD reason a files reply waiting for SMD carries.
HOLD_REASON = "matter_files_review"
STATUS_REVIEW = "review"
STATUS_SENT = "sent"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"


class ReviewError(ValueError):
    """The review action is refused; the message says why."""


def review_dir() -> Path:
    return Path(os.environ.get(REVIEW_DIR_ENV, "").strip() or DEFAULT_REVIEW_DIR)


def decisions_dir() -> Path:
    return Path(os.environ.get(DECISIONS_DIR_ENV, "").strip() or DEFAULT_DECISIONS_DIR)


def digest(row: dict[str, Any]) -> str:
    """sha256 over who, what and which bytes; never a timestamp or a status."""
    canon = {
        "message_id": row.get("message_id"),
        "to": row.get("to"),
        "text": row.get("text"),
        "html": row.get("html"),
        "batches": [
            [[f.get("handle"), f.get("sha256"), f.get("name")] for f in b]
            for b in row.get("batches") or []
        ],
    }
    raw = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def hold(
    *,
    message_id: str,
    to: str,
    text: str,
    html: str,
    batches: list[list[dict[str, Any]]],
    matter_id: str,
    session_id: str,
    now: float | None = None,
    directory: Path | None = None,
) -> dict[str, Any]:
    """Write one held reply for review; return the row (with ``id`` and ``digest``)."""
    base = directory or review_dir()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "id": os.urandom(12).hex(),
        "created_at": now if now is not None else time.time(),
        "status": STATUS_REVIEW,
        "message_id": message_id,
        "to": to,
        "text": text,
        "html": html,
        "batches": batches,
        "matter_id": matter_id,
        "session_id": session_id,
    }
    row["digest"] = digest(row)
    _write(base / f"{row['id']}.json", row)
    return row


def _write(path: Path, row: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(row, fh)
    os.replace(tmp, path)


def rows(directory: Path | None = None) -> list[dict[str, Any]]:
    base = directory or review_dir()
    out = []
    for path in sorted(base.glob("*.json")) if base.is_dir() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and _ID_RE.fullmatch(str(row.get("id") or "")):
            out.append(row)
    return out


def released_count(directory: Path | None = None) -> int:
    """Held files replies SMD approved and the seat then SENT: the countdown
    ``matter_file_delivery.review_first`` reads. Rows are never pruned.

    Only a released reply counts. A rejected, expired, failed or still-waiting
    row is a reply SMD has not let go, so it spends none of ``review_first``:
    on 2026-11-02 the first A&P files reply was rejected, and counting every
    held row would have sent the next one unreviewed."""
    return sum(1 for row in rows(directory) if row.get("status") == STATUS_SENT)


def finish(
    row: dict[str, Any], status: str, *, note: str = "", directory: Path | None = None
) -> None:
    """Move a row to a terminal status and drop its words (the hashes stay)."""
    base = directory or review_dir()
    row = {
        **row,
        "status": status,
        "decided_at": time.time(),
        "note": note[:300],
        "text": "",
        "html": "",
    }
    _write(base / f"{row['id']}.json", row)


def decision(row: dict[str, Any], *, directory: Path | None = None) -> str:
    """``approve`` / ``reject`` when ROOT decided this exact row, else ``""``."""
    base = directory or decisions_dir()
    for word in _DECISIONS:
        path = base / f"{row.get('id')}.{row.get('digest')}.{word}"
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_uid == 0:
            return word
    return ""


def expired(row: dict[str, Any], now: float | None = None) -> bool:
    return (now if now is not None else time.time()) - float(
        row.get("created_at") or 0
    ) > TTL_SECONDS


def describe(row: dict[str, Any]) -> str:
    """What SMD approves: every fact the digest covers, in words."""
    lines = [
        f"id:       {row.get('id')}   status: {row.get('status')}",
        f"held at:  {time.strftime('%Y-%m-%d %H:%M', time.localtime(float(row.get('created_at') or 0)))}",
        f"to:       {row.get('to')} (the reply is in-thread; the broker copies only the request's own firm staff)",
        f"answers:  graph message {row.get('message_id')}",
        f"matter:   {row.get('matter_id')}",
        "words (sent exactly as below):",
        "-" * 60,
        str(row.get("text") or "").rstrip("\n"),
        "-" * 60,
    ]
    batches = row.get("batches") or []
    for n, batch in enumerate(batches, start=1):
        lines.append(f"reply {n} of {len(batches)}:")
        for f in batch:
            lines.append(
                f"  file {f.get('name')}  smokeball {f.get('file_id')}  pages {f.get('pages') or 'all'} "
                f"({f.get('page_count')} p)  {f.get('size')} bytes  sha256 {f.get('sha256')}"
            )
    lines.append(f"digest:   {row.get('digest')}")
    return "\n".join(lines)


def decide(
    row_id: str, word: str, dig: str, *, directory: Path | None = None, reviews: Path | None = None
) -> Path:
    """Root writes the decision file the gateway acts on."""
    if word not in _DECISIONS:
        raise ReviewError("the decision is approve or reject")
    if not _ID_RE.fullmatch(row_id or "") or not _DIGEST_RE.fullmatch(dig or ""):
        raise ReviewError("give the row id and the digest exactly as `list` printed them")
    row = next((r for r in rows(reviews) if r.get("id") == row_id), None)
    if row is None or row.get("status") != STATUS_REVIEW:
        raise ReviewError("no reply is waiting for review under that id")
    if row.get("digest") != dig or digest(row) != dig:
        raise ReviewError("the digest does not match the held reply; list it again")
    if word == "approve" and expired(row):
        raise ReviewError(
            "that reply has expired (its files are gone from the broker's spool); nothing was sent"
        )
    base = directory or decisions_dir()
    base.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = base / f"{row_id}.{dig}.{word}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(fd)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m shared.matter_file_review", description=__doc__
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for word in _DECISIONS:
        p = sub.add_parser(word)
        p.add_argument("row_id")
        p.add_argument("digest")
    args = parser.parse_args(argv)
    if args.cmd == "list":
        waiting = [r for r in rows() if r.get("status") == STATUS_REVIEW]
        for row in waiting:
            print(
                describe(row) + ("\n(EXPIRED: approve is refused)" if expired(row) else "") + "\n"
            )
        print(f"{len(waiting)} repl{'y' if len(waiting) == 1 else 'ies'} waiting for review.")
        print(
            f"{released_count()} released after review on this seat so far (review_first counts these)."
        )
        return 0
    if os.geteuid() != 0:
        print(
            "refused: a decision is root's (the gateway trusts only a root-owned decision file)",
            file=sys.stderr,
        )
        return 2
    try:
        path = decide(args.row_id, args.cmd, args.digest)
    except (ReviewError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}; the seat acts on it within a minute.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a script on the seat
    sys.exit(main())
