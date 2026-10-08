"""A quotation inside a quotation, written with double marks at both levels,
turned to single marks inside, as a brief writes it.

The model sometimes quotes a record that itself quotes someone and keeps both
sets of double marks: ``"difficulty "even climbing the back stairs.""``. That reads
wrong to an attorney, and the drafting gate cannot pair the empty ``""`` at its
end. The repair is deterministic and changes no word: the inner opening mark
and the first of the two closing marks become single marks, giving
``"difficulty 'even climbing the back stairs.'"``. Only a closing ``""`` that
follows a word or punctuation is treated as a nested close, and only when an
inner opening mark sits between it and the outer quotation's opening mark in
the same paragraph; anything else is left for the gate to judge.
"""

from __future__ import annotations

import re

#: Two straight marks closing together, after a word or punctuation.
_CLOSE = re.compile(r'(?<=[^\s"])""')


def _fix_paragraph(para: str) -> tuple[str, int]:
    chars = list(para)
    fixed = 0
    for m in _CLOSE.finditer(para):
        inner_close = m.start()
        marks = [i for i in range(inner_close) if chars[i] == '"']
        # Need the outer opener and the inner opener, unpaired before the close:
        # an even number of marks before the outer opener, so the last two
        # marks are opener (outer) and opener (inner).
        if len(marks) < 2 or len(marks) % 2:
            continue
        inner_open = marks[-1]
        before = para[inner_open - 1] if inner_open > 0 else " "
        if not (before.isspace() or before in "(—-[:"):
            continue
        chars[inner_open] = "'"
        chars[inner_close] = "'"
        fixed += 1
    return "".join(chars), fixed


def repair(doc: str) -> tuple[str, list[str]]:
    """The document with each nested double-quoted inner quotation turned to
    single marks, and a log line per repair."""
    out: list[str] = []
    total = 0
    for para in re.split(r"(\n[ \t]*\n)", doc):
        fixed_para, n = _fix_paragraph(para)
        out.append(fixed_para)
        total += n
    log = [f"{total} nested quotation(s) set in single marks inside their quotation"] if total else []
    return "".join(out), log
