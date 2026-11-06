"""The reach of a firm rule, as the broker carries it: shape only.

A firm rule about the firm's documents may name the document kinds it covers
and the attorney whose documents it covers (2026-11-06). WHICH kinds and whose
documents is the seat's question, decided in code against the requester's own
words (overlay ``shared/rule_reach.py``) and re-checked by the intake and the
spec applier (overlay ``shared/wording_rules.py``). The broker cannot read the
seat's registry, so it checks only that the two fields are well formed and
carries them from the proposal row to the committed adjustment unchanged: what
the person confirmed is what is installed.
"""

from __future__ import annotations

import re
from typing import Any

from .establishment_validation import EstablishmentValidationError

_KIND = re.compile(r"\A[a-z0-9_]{1,40}\Z")
MAX_KINDS = 10
MAX_ATTORNEY_CHARS = 120


def reach_of(subject_raw: dict[str, Any]) -> dict[str, Any]:
    """``{doc_kinds?, attorney?, reader?}`` from a proposal's subject, each only when given."""
    out: dict[str, Any] = {}
    kinds = subject_raw.get("doc_kinds")
    if kinds is not None:
        if (
            not isinstance(kinds, list)
            or not kinds
            or len(kinds) > MAX_KINDS
            or len(set(kinds)) != len(kinds)
            or not all(isinstance(k, str) and _KIND.match(k) for k in kinds)
        ):
            raise EstablishmentValidationError(
                f"subject.doc_kinds must be a list of 1 to {MAX_KINDS} distinct kind slugs"
            )
        out["doc_kinds"] = list(kinds)
    attorney = subject_raw.get("attorney")
    if attorney is not None:
        if (
            not isinstance(attorney, str)
            or not attorney.strip()
            or len(attorney) > MAX_ATTORNEY_CHARS
            or "\n" in attorney
            or "\r" in attorney
        ):
            raise EstablishmentValidationError("subject.attorney must be one line naming the attorney")
        if "doc_kinds" not in out:
            raise EstablishmentValidationError("subject.attorney needs subject.doc_kinds beside it")
        out["attorney"] = " ".join(attorney.split())
    reader = subject_raw.get("reader")
    if reader is not None:
        if not isinstance(reader, str) or not _KIND.match(reader):
            raise EstablishmentValidationError("subject.reader must be one reader slug")
        if "doc_kinds" not in out:
            raise EstablishmentValidationError("subject.reader needs subject.doc_kinds beside it")
        out["reader"] = reader
    return out


def adjustment_for(row: dict[str, Any], applied_by: str, at: str) -> dict[str, Any]:
    """The committed adjustment for a confirmed rule row. The reach the person was
    shown and confirmed is carried from the row unchanged; absent on a rule without."""
    subject = row["subject"]
    return {
        "id": row["proposal_id"],
        "text": row["text"],
        "sha256": row["text_sha256"],
        "instructed_by": row["instructed_by"],
        "applied_by": applied_by,
        "at": at,
        **{k: subject[k] for k in ("doc_kinds", "attorney", "reader") if k in subject},
    }


__all__ = ["MAX_ATTORNEY_CHARS", "MAX_KINDS", "adjustment_for", "reach_of"]
