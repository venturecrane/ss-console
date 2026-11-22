"""The record summary's size, counted in tokens of the largest request it rides in.

WHY (2026-11-22). Discovery drafting and demand carry the record summary
(``digest.md``, or the fact index on the ledger path) into every writing,
checking and review request. Its ceiling was characters: drafting's
1,600,000 (``drafting/run.py``), demand's firm lever of 2,000,000. Measured on
real records that day, a summary runs about 2.17 characters a token (one case:
382,668 characters, 176,349 tokens), so 1.6M characters is about 737,000
tokens before the prompt around it and an output ceiling of up to 128,000: past
a 1,000,000-token window. The census of 341 ledgers found drafting compose at
672,158 tokens (67%) and demand review at 418,426 (42%), both rising with the
file.

So the budget is the window less the largest request that carries the summary
(its prompt without the summary, and its output ceiling) less a margin, and a
built summary is measured against it at 2 characters a token, the safe side
of the measured density. A summary under budget passes through untouched (a
job well under the limit sends exactly what it sent before). One over is
condensed again by the lane's own condense step, each pass strictly smaller,
until it fits; one that cannot shrink further ends the job failed and
resumable with ``SummaryTooLarge``, whose text is for SMD, never the firm.
"""

from __future__ import annotations

import math
from typing import Callable, TypeVar

T = TypeVar("T")

#: The model's context window (every model the drafting and demand lanes tier).
WINDOW_TOKENS = 1_000_000
#: Measured 2.17 on real records (11-22); chars / 4 undercounted by about 2x.
CHARS_PER_TOKEN = 2.0
#: The largest output ceiling a carrying request asks for (``demand.gapaudit.OUTPUT_MAX``).
OUTPUT_TOKENS = 128_000
#: Headroom for wording rules, a longer request or context; also the gateway's
#: smallest output it lowers a ceiling to.
MARGIN_TOKENS = 16_000
#: Drafting's largest frame is the discovery review: the served discovery papers
#: (``drafting.review.DISCOVERY_PAPERS_MAX_CHARS``, 300,000 characters, 150,000
#: tokens), the delivered documents and notes (two drafts,
#: ``drafting.evidence_cost.DRAFT_TOKENS`` each, 60,000), and the prompt and
#: matter context (10,000). Compose's frame is smaller
#: (``evidence_cost.COMPOSE_FRAME_CHARS``, about 80,000 tokens).
DRAFTING_FRAME_TOKENS = 220_000
#: Demand's largest frame is the review: the letter, the gap audit and the notes
#: as delivered, the matter fields and the figures (about 80,000 tokens on the
#: largest letters), and the review prompt; demand sends no exemplar.
DEMAND_FRAME_TOKENS = 120_000
#: Condense passes past the first. Each resends the summary, so they are few.
MAX_PASSES = 3
#: Pieces a summary is cut into for another condense pass (the chunk size).
PIECE_CHARS = 120_000


def budget_tokens(frame_tokens: int) -> int:
    """The summary's budget beside a frame of ``frame_tokens``."""
    return WINDOW_TOKENS - frame_tokens - OUTPUT_TOKENS - MARGIN_TOKENS


DRAFTING_BUDGET_TOKENS = budget_tokens(DRAFTING_FRAME_TOKENS)  # 636,000
DEMAND_BUDGET_TOKENS = budget_tokens(DEMAND_FRAME_TOKENS)  # 736,000


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def budget_chars(tokens: int) -> int:
    """The character target a condense step aims at for ``tokens``."""
    return int(tokens * CHARS_PER_TOKEN)


DRAFTING_BUDGET_CHARS = budget_chars(DRAFTING_BUDGET_TOKENS)  # 1,272,000
DEMAND_BUDGET_CHARS = budget_chars(DEMAND_BUDGET_TOKENS)  # 1,472,000


class SummaryTooLarge(RuntimeError):
    """The summary still does not fit after the condense step can do no more."""

    def __init__(self, tokens: int, budget: int, passes: int) -> None:
        self.tokens, self.budget, self.passes = tokens, budget, passes
        super().__init__(
            f"the record summary is about {tokens:,} tokens after {passes} further condensing pass(es), "
            f"over the {budget:,} that leaves room for the largest request that carries it; nothing that "
            "carries it was sent. A resume runs the summary step again"
        )


def pieces(text: str, cap: int = PIECE_CHARS) -> list[str]:
    """``text`` cut at paragraph breaks into pieces of at most ``cap``
    characters (a paragraph longer than that at line breaks, a line longer
    than that at ``cap``), so a further condense never sends one giant group.
    Joined with ``"\\n\\n"`` they are ``text`` again."""
    out: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        parts = [para] if len(para) <= cap else _cut(para, cap)
        for part in parts:
            if cur and len(cur) + 2 + len(part) > cap:
                out.append(cur)
                cur = part
            else:
                cur = f"{cur}\n\n{part}" if cur else part
    if cur or not out:
        out.append(cur)
    return out


def _cut(para: str, cap: int) -> list[str]:
    lines: list[str] = []
    for ln in para.split("\n"):
        lines += [ln[i : i + cap] for i in range(0, len(ln), cap)] or [""]
    out, cur = [], None
    for ln in lines:
        if cur is not None and len(cur) + 1 + len(ln) > cap:
            out.append(cur)
            cur = None
        cur = ln if cur is None else f"{cur}\n{ln}"
    return [*out, cur or ""]


def bound(
    first: T,
    budget: int,
    shrink: Callable[[T, int], T],
    log: Callable[[str], None],
    size: Callable[[T], int],
    max_passes: int = MAX_PASSES,
) -> T:
    """``first`` when its estimate fits ``budget`` tokens; otherwise
    ``shrink(current, target_chars)`` again until it does, each result
    strictly smaller (``size``, characters). Raises ``SummaryTooLarge`` when a
    pass does not shrink it or ``max_passes`` are spent."""
    out = first
    passes = 0
    while math.ceil(size(out) / CHARS_PER_TOKEN) > budget:
        before = size(out)
        if passes >= max_passes:
            raise SummaryTooLarge(math.ceil(before / CHARS_PER_TOKEN), budget, passes)
        new = shrink(out, budget_chars(budget))
        passes += 1
        after = size(new)
        log(
            f"  record summary over its {budget:,}-token budget: condensed again ({passes} of {max_passes}), "
            f"{before:,} to {after:,} characters"
        )
        if after >= before:
            raise SummaryTooLarge(math.ceil(after / CHARS_PER_TOKEN), budget, passes)
        out = new
    return out


def bound_text(text: str, budget: int, shrink: Callable[[str, int], str], log: Callable[[str], None]) -> str:
    """``bound`` for a summary that is one text, measured by its length."""
    return bound(text, budget, shrink, log, len)
