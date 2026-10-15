"""The note heading a continuation page needs when its heading page is not
composed. $0.

WHY (2026-10-14). An update composes only the new documents' pages the
delivery did not read. An EMR print runs one note over several pages; when
the page holding the note's heading (its title, "Author:", "Date of Service:")
is already read and excluded, the page that continues the note is composed
with one context block (the foot of the page before, ``stages/chunking``).
The heading was further back, so compose took the page BANNER instead ("12/08
- Admission ... (continued)") and credited a nurse practitioner's earlier
pre-op note to the surgeon on the admission date.

`heading_block` walks back from such a page over the pages that are not
composed and carries the nearest note heading as a delimited block, so the
entry takes its author and date of service from the note itself. When no
heading is found the block says so: the provider is flagged as not stated on
the composed pages, never assigned from a banner or an encounter header.
"""

from __future__ import annotations

import re
from collections.abc import Callable

#: A line that names a note's author, signer or date of service. An encounter
#: banner ("Admission", "Attending:") is deliberately NOT one: it names the
#: stay, not the note.
HEAD_LINE = re.compile(
    r"(?i)^\s*(?:author\s*:|date of service\s*:|filed\s*:.*date of service|"
    r"(?:electronically\s+)?signed by\b|dictated by\b|note by\b|creation time\s*:)"
)
_AUTHOR = re.compile(r"(?i)^\s*author\s*:")
#: Another note's metadata: never taken as this note's title line.
_META = re.compile(r"(?i)author|signed|editor|date of service|filed\s*:|version \d+ of")
#: How far back a heading is looked for, in pages not composed.
MAX_BACK = 6
#: A heading in the top share of a read page is that page's own note.
OWN_SHARE = 0.6
#: Lines of title kept above the heading lines.
TITLE_LINES = 2
NOT_FOUND = "(note author not stated on the composed pages)"


def _lines(text: str) -> list[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def _heading(text: str) -> str:
    """The NEAREST note heading on a page: from its last "Author:" line (an
    EMR prints the metadata of several notes back to back; the last is the
    note that runs on) or else its last cluster of heading lines, with up to
    TITLE_LINES title lines above it that are not another note's metadata.
    '' when the page names no note author or date of service."""
    lines = _lines(text)
    hits = [i for i, ln in enumerate(lines) if HEAD_LINE.match(ln)]
    if not hits:
        return ""
    end = hits[-1]
    authors = [i for i in hits if _AUTHOR.match(lines[i])]
    start = authors[-1] if authors else end
    if not authors:
        for i in reversed(hits[:-1]):
            if start - i > 4:
                break
            start = i
    for _ in range(TITLE_LINES):
        if start == 0 or _META.search(lines[start - 1]):
            break
        start -= 1
    return "\n".join(lines[start : end + 2])


def _has_own(text: str) -> bool:
    lines = _lines(text)
    top = lines[: max(1, int(len(lines) * OWN_SHARE))]
    return any(HEAD_LINE.match(ln) for ln in top)


def heading_block(pages: dict[int, str], pg: int, composed: Callable[[int], bool]) -> str:
    """The note-heading block for read page `pg`, or '' when it needs none
    (its page before is composed, it is the document's first page, or it
    states its own note heading)."""
    if pg <= 1 or composed(pg - 1) or _has_own(pages.get(pg) or ""):
        return ""
    for back in range(pg - 1, max(0, pg - 1 - MAX_BACK), -1):
        if composed(back):
            break
        found = _heading(pages.get(back) or "")
        if found:
            return (
                f"[note heading, p.{back}: the note p.{pg} continues; for reading only, never cite it. "
                f"Its author and date of service are this page's provider and date, not a page banner]\n"
                f"{found}\n[end note heading]\n"
            )
    return (
        f"[note heading: none found on the pages before p.{pg} that are not composed. If p.{pg} continues a "
        f'note begun on an earlier page, write its provider as "{NOT_FOUND}", never a name from a page '
        f"banner or an encounter header]\n"
    )
