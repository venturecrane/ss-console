"""What each keying act did, written by the act itself, on disk.

WHY (the model-as-logic audit, 2026-11-04). The post journal (``post_journal``)
recorded, per letter, where it was filed (``placed_on``), what went wrong
(``issue``) and which matters were candidates (``candidates``), and all three
were TYPED BY THE MODEL from its memory of the turn. The connector checked only
the page spans, so a matter number misremembered, or a Medicals row that never
landed recorded as landed, reached the firm's daily post list as fact. The
tools that did the work already knew every one of those values.

So each act now writes its own outcome here, and ``record_post_batch`` derives
the record from these rows (``post_journal.derive``). A row is written by:

* ``resolve_invoice_matter`` when it is handed the bundle group
  (``sha256``, ``first_page``, ``last_page``, ``message_id``): the verdict, the
  matter when ``unique``, and the candidates' matter numbers;
* ``file_attachment_pages_to_matter``: ``filed`` with the matter and the
  number its resolution named, or why not;
* ``stage_vendor_invoice``: ``staged``, ``figure_not_saved`` (written, but it
  did not read back), ``possible_duplicate``, or ``refused`` with the why;
* ``add_medicals_row``: ``written``, ``figure_not_saved`` or
  ``tab_not_written``;
* ``add_negotiation_rows`` (with ``operator_tab`` and a ``source_file_id``):
  ``written``, ``figure_not_saved``, ``possible_duplicate`` or
  ``offer_not_written``.

Each writer writes in a ``finally`` (:func:`recording`), so a tool that RAISES
still leaves a row, with the exception's class as its status: the SDK turns
the exception into a refusal only after the body, and a raise that left no row
would read as "nothing was attempted".

KEYED BY BUNDLE AND MESSAGE. ``outcomes/<sha256>-<sha256(message_id)[:16]>.jsonl``
under ``$SMD_POST_JOURNAL_DIR``, so the same PDF sent twice, or a held letter
filed later from the sender's reply, never merges into an earlier run's rows.
One 0600 file per key, appended one whole line per row under a lock (a single
``write`` of a short line on a local filesystem is not interleaved), kept
``RETAIN_DAYS`` like the batches beside it. It holds page numbers, matter ids
and numbers, status words and short reasons; never page text.

WHAT A SWITCHED-OFF CHECK WOULD HAVE DONE. A check that ships in ``observe``
(``logic_gate``) writes an observation row instead of refusing:
``observations/<UTC date>.jsonl`` under the same directory, 0600, ``RETAIN_DAYS``.
Its own file, because it must also hold a call with no bundle to key it by (a
keying call that names no document), and because nothing derives from it: these
rows are read by a person deciding whether a switch can go to ``enforce``. A row
carries the switch, the tool, ``status`` (``would_refuse``, ``unreadable`` for a
source that could not be read, ``differs`` for a typed record that disagrees with
the derived one) and the sentence the check would have said.

A ledger that cannot be written never fails the act it records (the act is the
firm's work; the row is our bookkeeping). The record then derives
``not_attempted`` for that letter, which LISTS it for a person: the failure is
visible, never silent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

JOURNAL_DIR_ENV = "SMD_POST_JOURNAL_DIR"
DEFAULT_JOURNAL_DIR = "/opt/data/post-intake-journal"
RETAIN_DAYS = 45
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_DETAIL = 200
_MAX_FIELD = 120
_MAX_CANDIDATES = 10
_LOCK = threading.Lock()

NOW: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731 - a test swaps the clock

#: The tools that write here, by name.
RESOLVE = "resolve_invoice_matter"
FILE = "file_attachment_pages_to_matter"
STAGE = "stage_vendor_invoice"
MEDICALS = "add_medicals_row"
NEGOTIATION = "add_negotiation_rows"


#: The ``status`` of an observation row.
WOULD_REFUSE = "would_refuse"
SOURCE_UNREADABLE = "unreadable"
DIFFERS = "differs"
_MAX_REASON = 400


def journal_dir() -> Path:
    return Path(os.environ.get(JOURNAL_DIR_ENV) or DEFAULT_JOURNAL_DIR)


def message_digest(message_id: str) -> str:
    return hashlib.sha256((message_id or "").strip().encode()).hexdigest()[:16]


def ledger_path(sha256: str, message_id: str) -> Path:
    return journal_dir() / "outcomes" / f"{sha256}-{message_digest(message_id)}.jsonl"


def _line(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = str(value).replace("\N{EM DASH}", ",").replace("\N{EN DASH}", "-")
    return " ".join(text.split())[:limit]


def _page(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


@dataclass
class Outcome:
    """One act's row, filled in by the act as it learns each fact."""

    tool: str
    sha256: str
    message_id: str
    first_page: int | None = None
    last_page: int | None = None
    matter_id: str = ""
    matter_number: str = ""
    status: str = ""
    detail: str = ""
    returned_id: str = ""
    candidates: list[str] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        return {
            "ts": NOW().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": self.tool,
            "sha256": self.sha256,
            "message_id": _line(self.message_id, 300),
            "first_page": _page(self.first_page),
            "last_page": _page(self.last_page),
            "matter_id": _line(self.matter_id, _MAX_FIELD),
            "matter_number": _line(self.matter_number, _MAX_FIELD),
            "status": _line(self.status, _MAX_FIELD) or "unknown",
            "detail": _line(self.detail, _MAX_DETAIL),
            "returned_id": _line(self.returned_id, _MAX_FIELD),
            "candidates": [_line(c, _MAX_FIELD) for c in self.candidates if _line(c, _MAX_FIELD)][:_MAX_CANDIDATES],
        }


def _prune(directory: Path) -> None:
    cutoff = time.time() - RETAIN_DAYS * 86400
    for old in directory.glob("*.jsonl"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            continue


def append(outcome: Outcome) -> bool:
    """Append one row. False (and a line on stderr) when the bundle key is not a
    sha256 or the disk refuses; never raises."""
    sha = (outcome.sha256 or "").strip().lower()
    if not _SHA_RE.match(sha):
        return False
    outcome.sha256 = sha
    return _append_line(ledger_path(sha, outcome.message_id), outcome.row())


def _append_line(path: Path, row: dict[str, Any]) -> bool:
    data = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    try:
        with _LOCK:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(handle, data)
            finally:
                os.close(handle)
            _prune(path.parent)
    except OSError as exc:
        print(f"outcome_ledger: row not written ({exc.__class__.__name__})", file=sys.stderr)
        return False
    return True


def observe(
    switch: str,
    tool: str,
    status: str,
    reason: str,
    *,
    sha256: str = "",
    message_id: str = "",
    first_page: int | None = None,
    last_page: int | None = None,
    matter_id: str = "",
) -> bool:
    """Write one observation row. Never raises; False when the disk refuses."""
    now = NOW().astimezone(timezone.utc)
    row = {
        "ts": now.isoformat().replace("+00:00", "Z"),
        "switch": switch,
        "tool": tool,
        "status": status,
        "reason": _line(reason, _MAX_REASON),
        "sha256": _line(sha256, 64).lower(),
        "message_id": _line(message_id, 300),
        "first_page": _page(first_page),
        "last_page": _page(last_page),
        "matter_id": _line(matter_id, _MAX_FIELD),
    }
    return _append_line(journal_dir() / "observations" / f"{now.date().isoformat()}.jsonl", row)


def observations() -> list[dict[str, Any]]:
    """Every observation row still on disk, oldest first."""
    rows: list[dict[str, Any]] = []
    for path in sorted((journal_dir() / "observations").glob("*.jsonl")):
        try:
            lines = path.read_text("utf-8").splitlines()
        except OSError:
            continue
        for text in lines:
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


@contextmanager
def recording(outcome: Outcome) -> Iterator[Outcome]:
    """Hold ``outcome`` open while the act runs and write it in ``finally``,
    whatever happened. A raise is recorded as the exception's class, then
    re-raised unchanged."""
    try:
        yield outcome
    except BaseException as exc:
        outcome.status = exc.__class__.__name__
        outcome.detail = str(exc)
        raise
    finally:
        append(outcome)


def outcomes(sha256: str, message_id: str) -> list[dict[str, Any]]:
    """Every row written under this bundle and message, oldest first. A line
    that does not parse is skipped (a row is one whole write, so a torn line is
    a corrupted file, and the record then derives from what remains, which
    lists any letter it cannot place)."""
    sha = (sha256 or "").strip().lower()
    if not _SHA_RE.match(sha):
        return []
    try:
        raw = ledger_path(sha, message_id).read_text("utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for text in raw.splitlines():
        try:
            row = json.loads(text)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("tool"), str):
            rows.append(row)
    return rows


__all__ = [
    "DIFFERS",
    "FILE",
    "JOURNAL_DIR_ENV",
    "MEDICALS",
    "NEGOTIATION",
    "Outcome",
    "RESOLVE",
    "SOURCE_UNREADABLE",
    "STAGE",
    "WOULD_REFUSE",
    "append",
    "journal_dir",
    "ledger_path",
    "message_digest",
    "observations",
    "observe",
    "outcomes",
    "recording",
]
