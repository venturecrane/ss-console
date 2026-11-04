"""Pulled files the extractor could not open because they are password-protected.

A password-protected PDF arrives intact (the bytes and hash match) and then
yields nothing: pypdf reports it was "not decrypted" and the extract stage
records that as the file's error. No unit can own a file with no text, so
before 2026-10-22 it reached the orphans decision as unexplained residue and
held the whole run (the Curtis chronology, matter 200530, an emailed
"Hospital Encounters" PDF). The same class held Martinez in August.

The reason is a measured fact about the file, not a judgement about its
content, so it is disclosed rather than held: the orphans decision explains
it with ``REASON`` and the Records Reviewed and Limitations section names it
to the reader. ``covered.covered_sets`` still counts it uncovered, so an
update reads it once the firm supplies the password.

Only the password class is explained here. Any other extract error (a broken
file, an unhandled type) stays residue and holds, because those are ours to
look at before a client reads that a document was skipped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REASON = "password-protected; it could not be opened without the password"

_PASSWORD = re.compile(r"not been decrypted|password|encrypt", re.IGNORECASE)


def is_password(error: object) -> bool:
    return bool(_PASSWORD.search(str(error or "")))


class Locked(ValueError):
    """A PDF a reader opened and found locked with a password. Its message is
    ``REASON``, so ``is_password`` recognises it wherever it is recorded as a
    file's error."""

    def __init__(self) -> None:
        super().__init__(REASON)


def locked(doc: Any) -> bool:
    """True for an open pymupdf document that needs a password an empty one
    does not satisfy. pymupdf opens such a file without complaint, reports its
    page count, and raises "document closed or encrypted" only when a page is
    touched, so every reader of a firm's file asks this before its first page.
    Encryption against editing alone opens with no password and is not locked."""
    return bool(doc.needs_pass) and not doc.authenticate("")


def refuse_locked(doc: Any) -> None:
    """Raise ``Locked`` when `doc` is locked; otherwise nothing. Closing the
    document stays the caller's (its ``with`` or ``finally``)."""
    if locked(doc):
        raise Locked()


def password_protected(slug_dir: Path) -> dict[str, str]:
    """``{id: name}`` for every extracted row whose error is the password class."""
    # Imported here so the lanes that only call ``locked`` (litigation,
    # negotiation, demand) do not load the stage machinery to do it.
    from .stages.base import read_jsonl

    return {
        str(r["id"]): str(r.get("name") or "?")
        for r in read_jsonl(slug_dir / "extracted.jsonl")
        if r.get("id") and is_password(r.get("error"))
    }
