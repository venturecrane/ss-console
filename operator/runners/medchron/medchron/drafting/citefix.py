"""Deposition citations the drafting gate refuses for starting after the
question an answer answered (gate 2b), repaired in code.

The gate states the cited range and the question's page:line. The fix moves
the citation's start back to the question, keeping its end: "66:14-19"
becomes "66:13-19", and a question on the previous page makes
"66:1-5" into "65:25-66:5". Only the citation that follows the quoted answer
is touched; a citation the repair cannot find is left for the gate to refuse
again (failed, our cause), never guessed at.
"""

from __future__ import annotations

import re

_REFUSAL = re.compile(
    r"\[2b\] cited range (\d+):(\d+) to (\d+):(\d+) excludes the question this answer answered, "
    r"at (\d+):(\d+): \"(.+?)\"(?:\s|$|;)"
)
#: How far after the quote the citation may sit.
WINDOW = 200


def findings(refusals: list[str]) -> list[tuple[int, int, int, int, str]]:
    """(start page, start line, question page, question line, quote) per 2b refusal."""
    out = []
    for r in refusals:
        m = _REFUSAL.search(str(r))
        if m:
            sp, sl, _ep, _el, qp, ql, quote = m.groups()
            out.append((int(sp), int(sl), int(qp), int(ql), quote))
    return out


def repair(doc: str, refusals: list[str]) -> tuple[str, list[str]]:
    """The document with each refused citation's start moved to its question,
    and a log line per repair."""
    log: list[str] = []
    for sp, sl, qp, ql, quote in findings(refusals):
        probe = " ".join(quote.split())[:40]
        flat_at = doc.find(probe) if probe else -1
        if flat_at < 0:
            continue
        window_end = flat_at + len(quote) + WINDOW
        cite = re.compile(rf"(?<![\d:]){sp}:{sl}(?:-(\d+)(?::(\d+))?)?(?!\d)")
        m = cite.search(doc, flat_at, window_end)
        if not m:
            continue
        end_page, end_line = (m.group(1), m.group(2)) if m.group(2) else (None, m.group(1))
        if end_line is None:
            new = f"{qp}:{ql}-{sp}:{sl}"
        elif end_page is not None:
            new = f"{qp}:{ql}-{end_page}:{end_line}"
        elif qp == sp:
            new = f"{qp}:{ql}-{end_line}"
        else:
            new = f"{qp}:{ql}-{sp}:{end_line}"
        doc = doc[: m.start()] + new + doc[m.end() :]
        log.append(f"citation {m.group(0)} moved to {new} to include the question at {qp}:{ql}")
    return doc, log
