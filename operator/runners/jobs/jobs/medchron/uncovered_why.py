"""Why a delivered chronology did not read a document, as a fixed token.

2026-10-26 on the A&P seat: an attorney asked about two files his delivered
update had not read. The ledger row said only that two were uncovered, so the
Operator searched an 836-file listing by name, gave up, and guessed the files
were scans with no text layer. They were password-protected, and the update
had recorded that. The coverage record now carries the reason beside each
uncovered id, so the answer comes from the job's own record.

The reason is a TOKEN from this fixed, code-authored vocabulary, never a file
name or any text from the document: the ledger holds ids and counts only (ADR
0052; the comment above ``covered_json`` in the broker's ``medchron_ledger``).
The broker refuses any other value (``medchron_coverage.WHY_TOKENS``, pinned to
this tuple by a test). A token is a hint for wording an answer, not a gate:
nothing decides coverage from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

RETRIEVAL_FAILED = "retrieval_failed"
NOT_PDF_OR_IMAGE = "not_pdf_or_image"
IMAGE_UNREADABLE = "image_unreadable"
PASSWORD_PROTECTED = "password_protected"
NO_TEXT = "no_text"
NOT_EXPLAINED = "not_explained"

TOKENS = (RETRIEVAL_FAILED, NOT_PDF_OR_IMAGE, IMAGE_UNREADABLE, PASSWORD_PROTECTED, NO_TEXT, NOT_EXPLAINED)


def restricted(why: Mapping[str, str], uncovered: Iterable[str]) -> dict[str, str]:
    """``why`` cut to the ids still uncovered, in sorted key order."""
    keep = set(uncovered)
    return {k: why[k] for k in sorted(why) if k in keep}


def union(records: Iterable[Mapping[str, str] | None], uncovered: Iterable[str]) -> dict[str, str]:
    """Several records' reasons, the first record to name an id deciding it,
    restricted to the ids still uncovered."""
    out: dict[str, str] = {}
    for rec in records:
        for k, v in sorted((rec or {}).items()):
            if v in TOKENS:
                out.setdefault(str(k), v)
    return restricted(out, uncovered)
