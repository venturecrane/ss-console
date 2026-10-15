"""A medical-legal expert report among an update's new documents. $0.

A life care plan, a future care needs assessment, an independent medical
examination, a record review or another expert's report is written FOR the
case about the treatment, not a record of it: itemizing it as treatment
entries would put an expert's opinions on the chronology as if a provider
had recorded them (2026-10-13: a firm-filed future care assessment went to
vision and composition as a medical source). Such a document is listed in
Records Reviewed as reviewed and not itemized, and on an update its scanned
pages beyond the first are never sent to vision.

Recognized by its file name, or by its first page's text.
"""

from __future__ import annotations

import re

REASON = "a medical-legal report, reviewed and not itemized"

_NAME = re.compile(
    r"(?i)\b(?:life[- ]care[- ]plan|future (?:medical )?care|care needs|"
    r"independent medical (?:exam|examination|evaluation)|records? review|"
    r"expert (?:witness )?(?:report|opinion)|medic(?:al|o)[- ]legal)"
)
_IME = re.compile(r"\bIME\b")  # case-sensitive: an acronym, never the word "time"
_TEXT = re.compile(
    r"(?i)\b(?:life care plan|future (?:medical )?care (?:needs|plan|costs?)|care needs assessment|"
    r"independent medical (?:examination|evaluation)|medic(?:al|o)[- ]legal (?:report|evaluation)|"
    r"expert (?:witness )?report|records? review report)"
)


def by_name(name: str) -> bool:
    return bool(_NAME.search(name or "") or _IME.search(name or ""))


def by_text(first_page: str) -> bool:
    return bool(_TEXT.search(first_page or ""))


def is_medlegal(names: list[str], first_page: str) -> bool:
    """Any of the document's names (its own, the firm's numbered file it is
    byte-identical to) or its first page says it is an expert's report."""
    return any(by_name(n) for n in names if n) or by_text(first_page)
