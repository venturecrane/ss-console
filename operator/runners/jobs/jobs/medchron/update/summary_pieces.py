"""The update's added prior history in date pieces, when one request over
every added pre-incident entry would be too large to send.

`update_summary` was ONE request over the whole update. On the cost ledgers
of 11-22 it measured 4,592 / 139,763 / 713,760 input tokens at 109 / 998 /
2,918 new pages (71% of the model's 1,000,000 limit at the top), rising
faster than pages: an update of about 4,000 new pages would be refused and
the job lost after every paid stage before it.

Under the limit the request is the one it always was (same text, same order,
same custom id). Over it, the entries are grouped by their visit date and
the dates cut into contiguous runs, each under the limit, halved at the
middle of their size (the review's halving, ``review_parts.size_halves``,
applied to dates). A date is never cut: its entries go in one piece, so
every date is in exactly one request. Each piece is summarised with the same
prompt, told it is one date range of a larger history and may state no
absence, first, last, latest, onset or baseline (another range may say
otherwise), and checked against its OWN source by the same containment rule.
The pieces are an intermediate step: ONE merge call, same model, prompt and
effort, writes the single Prior Medical History from the pieces' blocks
(summaries only, so small), and that reply is what is checked against the
FULL source and delivered. Blind pieces joined as they stand could contradict
each other and would repeat a condition once per piece.

An API size refusal splits that piece again; each half holds fewer dates,
so this ends. One date alone is SENT even when its estimate is over the
limit (an estimate is not a refusal); only an actual
size refusal of one date raises ``OneDateTooLarge``, which the stage turns
into a refusal (the job is held with the reason).

A piece answered and passing its check is kept in ``summary_pieces.json``
under the update directory, keyed by a hash of model, prompt, output ceiling,
effort, the piece note and its source,
so a job stopped mid-split never pays twice for a piece already done.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from . import body as B, review_parts as RP

#: An estimated request above this many input tokens is sent in date pieces.
#: The review's own figure (``review_parts.REQUEST_TOKENS_MAX``): the same
#: estimate (text at four characters a token) measured at 0.46 to 0.65 of
#: the real input on 148 real requests, so 300,000 estimated is at most about
#: 650,000 real, plus this call's 16,000 output ceiling, well under the
#: 1,000,000 limit. Every update on record but the largest stays one request.
TOKENS_MAX = RP.REQUEST_TOKENS_MAX
CACHE = "summary_pieces.json"
CUSTOM_ID = "update-summarize"
MERGE_ID = "update-summarize-merge"
#: Leads each piece's user text (never the one-request case's).
PIECE_NOTE = (
    "These entries are ONE date range of a longer prior history; other ranges are summarised separately and "
    "merged afterwards. Summarise only what these entries say. Do not state that anything was absent, did not "
    "recur or was never reported, and do not call anything the first, last, latest, initial or most recent, "
    "say when a condition began, or describe a baseline: another range may say otherwise."
)
#: Leads the merge call's user text.
MERGE_NOTE = (
    "Below are Prior Medical History sections, each written from one date range of the same patient's "
    "pre-incident entries, in date order. Write the ONE Prior Medical History section for the whole history "
    "under the rules you were given: group by condition across every range, say each fact once, and carry "
    "each sentence's citation verbatim from the sections. Add no fact the sections do not state."
)
#: The hold reason: true on every path (earlier pieces may have been paid),
#: free of token counts.
HOLD = "One part of the added records is larger than a single reading allows; SMD is on it."

Send = Callable[[str, str], str]  # (source, custom_id) -> the reply's text
Check = Callable[[str, str], str]  # (source, reply) -> why it is refused, "" when kept


class OneDateTooLarge(Exception):
    """One visit date's entries alone are too large for a single request."""

    def __init__(self, day: str) -> None:
        super().__init__(HOLD)
        self.day = day


def source(entries: list[str]) -> str:
    return "\n\n".join(entries)


def estimate(system: str, entries: list[str]) -> int:
    return RP.request_tokens([{"type": "text", "text": system}, {"type": "text", "text": source(entries)}])


def by_date(entries: list[str]) -> list[list[str]]:
    """Each visit date's entries, in their own order, dates ascending."""
    days: dict[str, list[str]] = {}
    for e in entries:
        days.setdefault(B.iso(e), []).append(e)
    return [days[d] for d in sorted(days)]


def _flat(groups: list[list[str]]) -> list[str]:
    return [e for g in groups for e in g]


def halves(groups: list[list[str]]) -> tuple[list[list[str]], list[list[str]]] | None:
    """Two contiguous runs of dates, cut nearest the middle of their size;
    None for one date (a date is never cut)."""
    if len(groups) < 2:
        return None
    sizes = [len(source(g)) for g in groups]
    total, run, cut, gap = sum(sizes), 0, 1, None
    for k in range(1, len(groups)):
        run += sizes[k - 1]
        if gap is None or abs(2 * run - total) < gap:
            cut, gap = k, abs(2 * run - total)
    return groups[:cut], groups[cut:]


def plan(groups: list[list[str]], system: str, limit: int) -> list[list[list[str]]]:
    """The date runs, each estimated under `limit` but a single date, which
    is sent as it is (only an actual size refusal holds it)."""
    h = None if estimate(system, _flat(groups)) <= limit else halves(groups)
    if h is None:
        return [groups]
    return plan(h[0], system, limit) + plan(h[1], system, limit)


class Pieces:
    """The split path: every date run answered once, refused ones split again."""

    def __init__(self, ud: Path, key: str, send: Send, check: Check, too_large: Callable[[Exception], bool], log):
        self.path, self.key, self.send, self.check, self.too_large, self.log = (
            ud / CACHE,
            key,
            send,
            check,
            too_large,
            log,
        )
        self.done: dict[str, str] = {}
        try:
            got = json.loads(self.path.read_text(encoding="utf-8"))
            self.done = {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}
        except (OSError, ValueError):
            pass

    def _id(self, text: str) -> str:
        return hashlib.sha256((self.key + "\0" + text).encode("utf-8")).hexdigest()

    def _keep(self, k: str, reply: str) -> None:
        self.done[k] = reply
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.done, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def answer(self, groups: list[list[str]]) -> list[tuple[str, str]]:
        """[(piece source, reply)] in date order."""
        text = source(_flat(groups))
        k = self._id(text)
        if k in self.done:
            return [(text, self.done[k])]
        days = f"{B.iso(groups[0][0])}..{B.iso(groups[-1][0])}"
        try:
            reply = self.send(PIECE_NOTE + "\n\n" + text, f"{CUSTOM_ID}-{days}")
        except Exception as exc:
            if not self.too_large(exc):
                raise
            h = halves(groups)
            if h is None:
                raise OneDateTooLarge(B.iso(groups[0][0])) from exc
            self.log(f"  added prior history {days}: refused for its size; sent as two pieces")
            return self.answer(h[0]) + self.answer(h[1])
        if not self.check(text, reply.strip()):  # a refused reply is never kept, so a rerun asks again
            self._keep(k, reply)
        return [(text, reply)]

    def all(self, runs: list[list[list[str]]]) -> list[tuple[str, str]]:
        return [p for r in runs for p in self.answer(r)]


def merge_text(blocks: list[str]) -> str:
    """The merge call's user text: the note, then each piece's block in date order."""
    return MERGE_NOTE + "\n\n" + "\n\n".join(b.strip() for b in blocks)
