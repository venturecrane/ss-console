"""A page's token stream and its runs of words. $0.

What is left of the text-window rule (2026-11-17), which set a new page
aside when a run of 1 to 3 pages of another document held every token of
it. It was removed on 2026-11-21: an update sets a page aside as a repeat
only when it is the same page (pixel-equal, or the same text,
``select.same_text``). The token stream and its runs stay: ``update/encounter``
reads them to decide whether one added entry says what another says.

* ``stream_of``: the page's tokens in order (words lowercase, numbers in
  one spelling, ``values.norm_number``), with None where a print or export
  stamp line was (``select.stamp_lines``), so removing a stamp never makes
  two tokens adjacent that the page did not set side by side.
* ``runs_of``: every run of RUN consecutive tokens that crosses no stamp.
"""

from __future__ import annotations

import re

from .values import CITE, norm_number

RUN = 4
_TOKEN = re.compile(r"[a-z]+|\d+(?:[.,/:]\d+)*")


def _tok(t: str) -> str:
    return t if t[0].isalpha() else "#" + norm_number(t)


def stream_of(text: str) -> tuple[str | None, ...]:
    """The page's tokens in order (words lowercase, numbers in one
    spelling), with None where a stripped stamp line was (``select.stamp_lines``)."""
    from .select import stamp_lines

    out: list[str | None] = []
    for ln, stamp in stamp_lines(text):
        if stamp:
            out.append(None)
        else:
            out.extend(_tok(t) for t in _TOKEN.findall(CITE.sub(" ", ln).lower()))
    while out and out[0] is None:  # a header or footer stamp is the page break itself, not inside the text
        out.pop(0)
    while out and out[-1] is None:
        out.pop()
    return tuple(out)


def runs_of(stream: tuple[str | None, ...]) -> list[tuple[str | None, ...]]:
    """Every run of RUN consecutive tokens that crosses no stamp."""
    return [r for i in range(len(stream) - RUN + 1) if None not in (r := stream[i : i + RUN])]
