"""A quotation never ends on a dangling comma.

A quotation cut mid-sentence from the record keeps the comma that stood
after its last word: ``Admit that YOU "held the lease,".`` reads as a typo to
an attorney (2026-11-05, a served discovery set quoting the defendant's
answer). The comma comes off when the closing mark is followed by the end of
a sentence, a parenthesis (a citation), or the end of the line. A comma before a
closing mark that the sentence continues past (``"the lease," "the
premises," and``) is ordinary American punctuation and stays. No word
changes, and the words left inside the marks are still a run of the record's
own words, so the quote gate finds them as before.
"""

from __future__ import annotations

import re

_DANGLING = re.compile(r'(?<=\w),(["”])(?=[ \t]*(?:[.;:?!()]|$))', re.MULTILINE)


def trim(doc: str) -> tuple[str, list[str]]:
    """``doc`` with every dangling comma before a closing mark removed, and an
    attorney note when any was."""
    out, n = _DANGLING.subn(r"\1", doc)
    return out, ([f"removed a dangling comma closing {n} quotation(s)"] if n else [])
