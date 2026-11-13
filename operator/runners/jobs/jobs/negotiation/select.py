"""Which newly saved documents the negotiation watch reads: an offer-related
file NAME, in a format it can read.

COVERAGE GAP (named on purpose, in the skill doc too): selection is by name. An
offer saved as "Scan_0042.pdf" or "Correspondence.pdf" is not read, and its
offer is not entered or announced. The firm's own offer letters and the
carriers' emails are named by the staff who save them, which is what the
2026-10-09 backfill measured; a generic name is the residue.
"""

from __future__ import annotations

import re
from typing import Any

from .. import filing

#: "counter" only at the start of a word ("Hospital Encounters.pdf" matched it on
#: the firm's data, 2026-10-10); "998" only as its own number, never inside one.
NAME = re.compile(
    r"offer|demand|(?<!\d)998(?!\d)|tender|(?<![a-z])counter|settle|evaluat|policy[\s_-]*limit", re.IGNORECASE
)
EXTENSIONS = frozenset({".pdf", ".docx", ".doc", ".msg", ".eml"})


#: The extension lower-cased with its dot (``jobs/filing.folded_ext``).
extension = filing.folded_ext


def full_name(f: dict[str, Any]) -> str:
    """The full filename, folded: the one rule (``jobs/filing.full_name``)."""
    return filing.full_name(f, fold=True)


def select(f: dict[str, Any]) -> bool:
    """True for a live file whose name is offer-related and whose format the
    reader handles."""
    if f.get("deleted"):
        return False
    return extension(f) in EXTENSIONS and bool(NAME.search(str(f.get("name") or "")))


__all__ = ["EXTENSIONS", "NAME", "extension", "full_name", "select"]
