"""When the court's own paper settles a matter-record field beyond doubt.

The rules a caption correction must pass before anything is written to the
firm's record. They are deliberately narrow: a firm-wide survey of 290 litigated
files (2026-10-08) found most differences between a caption and the record are
NOT errors in the record (a middle initial the pleading adds, a nickname, a
company suffix, a second case number for a consolidated action, a claim number
typed where the case number goes), so everything outside these rules is only
reported to the attorney, never written.

* A party name is a MISSPELLING when both spellings have the same number of
  words, at most two words differ, and each differing pair is at least three
  letters long, within two edits, and neither is a prefix of the other ("Corin"
  and "Corine" are two names). Capitals and punctuation never count.
* A case number may be FILLED when the record has none and the court's value
  has a California superior court format, and CORRECTED when the record's is
  the court's with zeros dropped. Any other difference is a different number.
"""

from __future__ import annotations

import difflib
import re

CLOSE = 0.8
#: The superior court case-number formats seen across the survey.
CASE_FORMAT = re.compile(r"^(\d{2}CV\d{6}|\d{2}-\d{4}-\d{8}|S-CV-\d{7})$")


def tokens(name: str) -> list[str]:
    """Lower-case alphanumeric words; "&" reads as "and"."""
    return re.findall(r"[a-z0-9]+", str(name).lower().replace("&", " and "))


def same_name(a: str, b: str) -> bool:
    """Equal but for capitals, punctuation and spacing."""
    return tokens(a) == tokens(b)


def close(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, " ".join(tokens(a)), " ".join(tokens(b))).ratio() >= CLOSE


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def misspelled_words(court: str, record: str) -> list[tuple[str, str]] | None:
    """The (record word, court word) pairs that differ when ``record`` is a
    misspelling of ``court``; None when it is not one (or they are the same)."""
    c, r = tokens(court), tokens(record)
    if not c or len(c) != len(r):
        return None
    pairs = [(rw, cw) for cw, rw in zip(c, r) if cw != rw]
    if not pairs or len(pairs) > 2:
        return None
    for rw, cw in pairs:
        if min(len(rw), len(cw)) < 3 or levenshtein(rw, cw) > 2:
            return None
        if rw.startswith(cw) or cw.startswith(rw):
            return None
    return pairs


def is_misspelling(court: str, record: str) -> bool:
    return misspelled_words(court, record) is not None


def _flat(case_number: str) -> str:
    return str(case_number).replace("-", "").strip().upper()


def dropped_zeros(court: str, record: str) -> bool:
    """True when the record's number is the court's with one or more zeros
    left out (hyphens ignored)."""
    a, b = _flat(court), _flat(record)
    if not b or len(a) <= len(b):
        return False
    j = 0
    for ch in a:
        if j < len(b) and ch == b[j]:
            j += 1
        elif ch != "0":
            return False
    return j == len(b)


def fillable(court: str) -> bool:
    return bool(CASE_FORMAT.match(str(court).strip().upper()))
