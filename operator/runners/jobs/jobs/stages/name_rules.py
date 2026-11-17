"""The firm's file-name exclusion rules, applied in ONE place. $0.

``coverage.exclusions`` are the firm's authored name rules ("billing or lien
record, not a treatment record", ...). Every reader asks this module whether
a name is excluded and why, so the pull, the coverage record, the orphan
decision and the limitations never disagree about one file.

A billing or lien rule does not exclude a name that also says it holds a
medical record. 2026-10-13: "Medical and Billing Records" was dropped by
`\\bbill`, and its visits with it. 2026-11-16: "Dr. Example Bill and Report.pdf"
(a billing statement, then a signed follow-up examination report) was
dropped by the same rule on an update, and the visit never reached the
chronology. "Billing Records" or "Imaging Bill" alone stays billing; a
billing page inside a file that is read is set aside page by page
(``update_select`` "billing page").
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_BILLING_RULE = re.compile(r"(?i)bill|lien|ledger|invoice|statement")
#: billing words that name billing paper even beside a medical-sounding noun
_BILLING_PHRASE = re.compile(
    r"(?i)\b(?:billing|bills?|itemi[sz]ed|lien)\s+(?:records?|statements?|notes?|reports?|summar(?:y|ies))\b"
)
#: the words that say a file holds a medical record. Kept NARROW on purpose:
#: only words that cannot describe a bill. Never a word that names a billing
#: form or a summary of charges ("imaging", "summary", "progress", "discharge"):
#: "Example Diagn Imaging Bill" is three insurance claim forms and stays out.
_MEDICAL_NAME = re.compile(
    r"(?i)\b(?:medical|chart|clinical|notes?|visits?|treatment|records?"
    r"|reports?|exams?|examinations?|evaluations?|re-?evaluations?|consults?|consultations?"
    r"|operative|op\s+notes?|narratives?|H\s*&\s*P|history\s+and\s+physical)\b"
)


def medical_overrides(rx: Any, why: str, name: str) -> bool:
    """True when a billing or lien name rule matched a name that also says
    it holds medical records: the file is read, not excluded."""
    if not (_BILLING_RULE.search(why) or _BILLING_RULE.search(str(getattr(rx, "pattern", "")))):
        return False
    return bool(_MEDICAL_NAME.search(_BILLING_PHRASE.sub(" ", name)))


def excluded_by_name(name: str, rules: Iterable[tuple[Any, str]]) -> str | None:
    """Why the firm's name rules exclude `name`, or None: the first rule that
    matches and is not a billing rule a medical name overrides."""
    return next((why for rx, why in rules if rx.search(name) and not medical_overrides(rx, why, name)), None)


__all__ = ["excluded_by_name", "medical_overrides"]
