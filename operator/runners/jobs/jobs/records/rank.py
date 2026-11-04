"""Which of the matter's files a record is likely in: ranked by its words, never filtered.

WHY (2026-11-03, measured at $0 on the A&P matter's 100 PDFs). The search
required EVERY word of the record to appear in a file name, so five of six
records matched nothing by their own words; one borrowed another record's
files, one landed on a 2,313-page set with no index while the file that held
it was a 56 KB exhibit, and one provider's eight files were cut to three in
LISTING order, dropping the one the 11-02 delivery used.

Now every PDF on the matter is SCORED against the record (code, no model):

* the words are casefolded and split on anything that is not a letter or a
  digit; stop words and credentials (``md``, ``m.d.``, ``psyd``, ``faaos``...)
  are dropped, so "Dr. Jane Roe, M.D." matches a file named "Roe records";
* 2 per provider word in the file name, 1 per other word the record asked
  with, 1 per date token (year, month name, day) of ``hints.dates``;
* a file scoring 0 is never a candidate. A record no file scores for has no
  candidate, and the job holds that record rather than reading a guess.

The caller takes the best :data:`TOP` and breaks ties on its own (a complete
page index first, then fewer pages), which needs the downloaded file.
"""

from __future__ import annotations

import re
from typing import Any

#: Files kept for a record before the tie-break (the job then keeps the best 3).
TOP = 8
STOP = frozenset(
    "a an and any all as at by for from in into me my of on or our please send the their this to with records record "
    "copy copies file files documents document notes note report reports visit visits".split()
)
CREDENTIALS = frozenset("md m d do psyd phd abpp atr bc faaos faoa dr np pa rn".split())
_MONTHS = "january february march april may june july august september october november december".split()
_WORD = re.compile(r"[a-z0-9]+")
_MDY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")


def _norm(token: str) -> str:
    return token.lstrip("0") or "0" if token.isdigit() else token


def tokens(text: Any) -> set[str]:
    """Casefolded letter-or-digit runs, numbers without leading zeros."""
    return {_norm(t) for t in _WORD.findall(str(text or "").casefold())}


def date_tokens(dates: Any) -> set[str]:
    """Each date's year, month (number, name, 3-letter name) and day."""
    out: set[str] = set()
    for d in dates if isinstance(dates, list) else [dates] if dates else []:
        out |= tokens(d) - CREDENTIALS
        for m, day, year in _MDY.findall(str(d)):
            if 1 <= int(m) <= 12:
                name = _MONTHS[int(m) - 1]
                out |= {name, name[:3], _norm(m), _norm(day), year[-2:] if len(year) == 2 else year}
    for name in list(out):
        if name in _MONTHS:
            out.add(name[:3])
    return out


def _words(rec: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    hints = rec.get("hints") or {}
    provider = tokens(hints.get("provider")) - STOP - CREDENTIALS
    dates = date_tokens(hints.get("dates") or hints.get("date"))
    asked = tokens(rec.get("asked")) - STOP - CREDENTIALS - provider - dates
    return provider, asked, dates


def score(name: Any, rec: dict[str, Any]) -> int:
    have = tokens(name)
    provider, asked, dates = _words(rec)
    return 2 * len(provider & have) + len(asked & have) + len(dates & have)


def rank(rows: list[dict[str, Any]], rec: dict[str, Any], top: int = TOP) -> list[tuple[str, int]]:
    """``[(file_id, score)]`` for the matter's PDFs that score above 0, best
    first (smaller file, then id, breaking a tie), at most ``top``."""
    scored = []
    for row in rows:
        if str(row.get("fileExtension") or "").strip().lower().lstrip(".") != "pdf" or not row.get("id"):
            continue
        s = score(row.get("name"), rec)
        if s > 0:
            scored.append((-s, int(row.get("sizeBytes") or 0), str(row["id"])))
    return [(fid, -neg) for neg, _size, fid in sorted(scored)[:top]]


__all__ = ["CREDENTIALS", "STOP", "TOP", "date_tokens", "rank", "score", "tokens"]
