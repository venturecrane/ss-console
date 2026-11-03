"""The SMD review row of a reply carrying matter files, and ROOT's decision on it (ADR 0102).

Lifted from ``operator/hermes-smd-overlay/shared/matter_file_review.py`` so the
gateway (which sends an approved row) and the records lane (which writes rows
as root, with no turn) build and read the same bytes. The overlay keeps its CLI,
``rows()``, ``finish()`` and ``decide()``; it imports these primitives.

THE INVARIANTS this module owns:

* :func:`digest` covers who, what and which bytes (message id, recipient, the
  redirect flag only when set, text, html, every file's handle, sha256 and
  name), never a timestamp or a status. A decision names the digest, so a
  decision for a row that changed after SMD read it matches nothing.
* A decision is a regular file owned by uid 0 named exactly
  ``<id>.<digest>.<approve|reject>`` in the decisions directory. The agent uid
  cannot write that directory, so a turn cannot approve its own reply.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

REVIEW_DIR_ENV = "SMD_MATTER_FILE_REVIEW_DIR"
DEFAULT_REVIEW_DIR = "/opt/data/matter-file-review"
DECISIONS_DIR_ENV = "SMD_MATTER_FILE_DECISIONS_DIR"
DEFAULT_DECISIONS_DIR = "/var/lib/smd-review"
#: A held row expires after this long (the broker spool keeps the files that long).
TTL_SECONDS = 24 * 60 * 60
ID_RE = re.compile(r"^[0-9a-f]{24}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
DECISIONS = ("approve", "reject")

#: The REPLY_HELD reason a files reply waiting for SMD carries.
HOLD_REASON = "matter_files_review"
STATUS_REVIEW = "review"
STATUS_SENT = "sent"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
#: How much of ``reason`` a row keeps.
REASON_CHARS = 500


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
        # Only when set, so the digest of every row written before it is unchanged.
        **({"redirect": True} if row.get("redirect") else {}),
        "text": row.get("text"),
        "html": row.get("html"),
        "batches": [[[f.get("handle"), f.get("sha256"), f.get("name")] for f in b] for b in row.get("batches") or []],
    }
    raw = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def hold_row(
    *,
    message_id: str,
    to: str,
    text: str,
    html: str,
    batches: list[list[dict[str, Any]]],
    matter_id: str,
    session_id: str,
    reason: str = "",
    redirect: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """One held reply for review, with a fresh ``id`` and its ``digest``. Writes nothing.

    ``batches`` may be empty (a records reply that attaches nothing is still
    held). ``reason`` is shown, not digested. ``redirect`` means ``to`` is a
    device's authored person, not the email's sender."""
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
        "reason": str(reason or "")[:REASON_CHARS],
        **({"redirect": True} if redirect else {}),
    }
    row["digest"] = digest(row)
    return row


def write_row(row: dict[str, Any], directory: Path | None = None) -> Path:
    """Write ``row`` as ``<id>.json`` (mode 0600, atomic replace); return the path.

    The directory is created 0700 when missing. Ownership is the caller's: a
    root writer chowns the file to the uid the gateway runs as."""
    if not ID_RE.fullmatch(str(row.get("id") or "")):
        raise ReviewError("a review row needs a 24-hex id")
    base = directory or review_dir()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = base / f"{row['id']}.json"
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(row, fh)
    os.replace(tmp, path)
    return path


def read_rows(directory: Path | None = None) -> list[dict[str, Any]]:
    """Every readable row in ``directory`` (``*.json``, a dict with a 24-hex id), sorted by file name."""
    base = directory or review_dir()
    out = []
    for path in sorted(base.glob("*.json")) if base.is_dir() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and ID_RE.fullmatch(str(row.get("id") or "")):
            out.append(row)
    return out


def released_count(directory: Path | None = None) -> int:
    """Held files replies SMD approved and the seat then SENT: the countdown
    ``matter_file_delivery.review_first`` reads. A rejected, expired, failed or
    still-waiting row spends none of it (2026-11-02: counting every held row
    would have sent the next A&P reply unreviewed)."""
    return sum(1 for row in read_rows(directory) if row.get("status") == STATUS_SENT)


def decision_for(directory: Path | None, row: dict[str, Any]) -> str | None:
    """``approve`` / ``reject`` when ROOT decided this exact row (a regular file
    owned by uid 0, named ``<id>.<digest>.<word>``), else ``None``."""
    base = directory or decisions_dir()
    for word in DECISIONS:
        path = base / f"{row.get('id')}.{row.get('digest')}.{word}"
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_uid == 0:
            return word
    return None


def decision_file(directory: Path | None, row_id: str, dig: str, word: str) -> Path:
    """Create the decision file ``<id>.<digest>.<word>`` (O_EXCL, 0644); return its path.

    Only the NAME is checked here (``word`` is approve or reject, the id and
    digest are well formed). Whether a row with that id and digest is waiting,
    and not expired, is the caller's check; the overlay's ``decide`` makes it.
    Raises :class:`ReviewError` on a bad name, :class:`FileExistsError` when
    the decision was already written."""
    if word not in DECISIONS:
        raise ReviewError("the decision is approve or reject")
    if not ID_RE.fullmatch(row_id or "") or not DIGEST_RE.fullmatch(dig or ""):
        raise ReviewError("give the row id and the digest exactly as `list` printed them")
    base = directory or decisions_dir()
    base.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = base / f"{row_id}.{dig}.{word}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(fd)
    return path


def expired(row: dict[str, Any], now: float | None = None) -> bool:
    return (now if now is not None else time.time()) - float(row.get("created_at") or 0) > TTL_SECONDS


__all__ = [
    "DECISIONS",
    "DECISIONS_DIR_ENV",
    "DEFAULT_DECISIONS_DIR",
    "DEFAULT_REVIEW_DIR",
    "DIGEST_RE",
    "HOLD_REASON",
    "ID_RE",
    "REASON_CHARS",
    "REVIEW_DIR_ENV",
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "STATUS_REJECTED",
    "STATUS_REVIEW",
    "STATUS_SENT",
    "TTL_SECONDS",
    "ReviewError",
    "decision_file",
    "decision_for",
    "decisions_dir",
    "digest",
    "expired",
    "hold_row",
    "read_rows",
    "released_count",
    "review_dir",
    "write_row",
]
