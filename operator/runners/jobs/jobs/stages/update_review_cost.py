"""What the update review (``stages/update_review.py``) is projected to cost,
for the job's spend cap. $0, no model. Split out of the stage on 2026-11-18
(the module-size ceiling); the stage re-exports ``projected_usd``.

Round one is priced from ITS OWN REQUESTS (2026-11-22): the parts the stage
plans (``review_parts.plan``), each built as the stage builds it, with the
delivered and same-date entries it carries, its added entries, the duplicate
and text parts, and the size splits, summed by the stage's own estimate
(``review_parts.request_tokens``) and divided by ESTIMATE_SHARE, the low end
of what that estimate measured against the real input. Measured before: the
old figure (every page once, doubled, no entries) was $22.02 where round one
alone cost $41.00, and $7.13 where the review cost about $23.8.

Every page is priced as the review will SHOW it, by the one rule both
checkers share (``medchron/page_mode.py``): a page shown as its own text at
its characters over four, a page shown as a picture at the per-picture figure
(``review_pages.PAGE_TOKENS``). A matter whose page index cannot open is
priced all pictures, as before the rule existed. Before the audit document
and the delivered entries exist, the pages alone are priced, with each part
carrying CONTEXT_MAX delivered entries of ENTRY_TOKENS each, under the same
correction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .. import budget, llm
from ..medchron import page_mode as PM
from ..medchron.audit.page_text import exhibit_paths
from ..medchron.update import finisher as FN, review_pages as RV, review_parts as RP, review_repair as RR
from .base import read_json
from .update_base import headings
from .update_review_io import _sha, already_in, page_counts

#: The share of round one the rest adds: the finish, the narrow re-check (a
#: subset of round one's pages) and its one finish; kept at a full round, an
#: upper bound now that there is no third review.
LATER_SHARE = 1.0
PAGE_TOKENS = RV.PAGE_TOKENS
RECORD = "review_repair.json"
#: ``review_parts.request_tokens`` measured 0.46 to 0.65 of the real input on
#: a real update's 148 parts (``review_parts.REQUEST_TOKENS_MAX``): divided by
#: the low end, the projection is never below what was measured.
ESTIMATE_SHARE = 0.46
#: Output per part, as before: small beside the input.
OUTPUT_PER_PART = 4_000
#: Before the delivered entries can be read: what one delivered entry is
#: priced at in a part's context (about 2,400 characters, a long visit).
ENTRY_TOKENS = 600


def review_model(cfg: Any) -> str:
    tiers = cfg.get("models", "tiers") or {}
    return str(tiers.get("review") or llm.model_for(cfg, "judgment"))


def page_input_tokens(exs: list[int], npages: Callable[[int], int], modes: Callable[[int, int], PM.Mode] | None) -> int:
    """Round one's page input: every page of `exs` once, as the rule shows it."""
    return sum(RV.mode_tokens(modes(x, p) if modes else None) for x in exs for p in range(1, npages(x) + 1))


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """What the stage is projected to cost, from this matter's own requests:
    round one's planned parts as built (``projection``), corrected to the real
    input, the same again for the rest (two finishes and the narrow re-check,
    at most), priced at the review model's rates."""
    doc = slug_dir / "runs" / unit / "final-chronology.md"
    rec = read_json(slug_dir / "runs" / unit / "update" / RECORD, {})
    if rec.get("status") == "clean" and doc.is_file() and rec.get("doc_sha_out") == _sha(doc.read_text("utf-8")):
        # ``_reuse`` returns at once on this document (a resume after SMD's
        # correction, ``jobs.medchron.update_decide``): projecting a full review
        # here would hold a resumed job on the cap for money it never spends.
        return 0.0
    modes = PM.Modes.open(slug_dir, unit)
    try:
        return projection(slug_dir, unit, cfg, pricing, modes)
    finally:
        modes.close()


def new_exhibits(slug_dir: Path, unit: str) -> list[int]:
    rows = read_json(slug_dir / "runs" / unit / "update" / "new_exhibits.final.json", [])
    return [x for x in (r.get("exhibit") for r in rows if isinstance(r, dict)) if isinstance(x, int)]


def projection(
    slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing, modes: Callable[[int, int], PM.Mode] | None
) -> float | None:
    """The projection on the matter's pages under `modes` (None: every page
    a picture), whatever the stage's record says (``review_measure`` reads it
    on a finished job); None when the update adds no exhibit."""
    exs = new_exhibits(slug_dir, unit)
    if not exs:
        return None
    n = page_counts(slug_dir / "out" / unit)
    planned = planned_tokens(slug_dir, unit, cfg, n, modes)
    if planned is None:  # no audit document or delivered entries yet: the pages and a full context per part
        parts = max(1, sum(n(x) for x in exs) // RP.WINDOW_PAGES + 1)
        planned = (page_input_tokens(exs, n, modes) + parts * RP.CONTEXT_MAX * ENTRY_TOKENS, parts)
    return _usd(*planned, cfg, pricing)


def _usd(est: int, parts: int, cfg: Any, pricing: budget.Pricing) -> float:
    """Round one's estimated input corrected to the real (ESTIMATE_SHARE), the
    rest of the loop added (LATER_SHARE), and each request's output."""
    rate = pricing.rate_for(review_model(cfg))
    tokens_in = est / ESTIMATE_SHARE * (1 + LATER_SHARE)
    return (tokens_in * rate.input_per_million_cents + parts * OUTPUT_PER_PART * rate.output_per_million_cents) / 1e8


def bound_usd(pages: int, cfg: Any, pricing: budget.Pricing) -> float | None:
    """The review's bound before ``update_collect`` names the new exhibits
    (``medchron/projection.bound_usd``): every page the update read, shown as
    a picture, each part with a full context. None when it read no page."""
    if pages <= 0:
        return None
    parts = pages // RP.WINDOW_PAGES + 1
    return _usd(pages * PAGE_TOKENS + parts * RP.CONTEXT_MAX * ENTRY_TOKENS, parts, cfg, pricing)


def _picture(_ex: int, _pg: int) -> dict[str, Any]:
    return {"type": "image"}  # priced at the picture figure by ``request_tokens``


def planned_tokens(
    slug_dir: Path, unit: str, cfg: Any, npages: Callable[[int], int], modes: Callable[[int, int], PM.Mode] | None
) -> tuple[int, int] | None:
    """(estimated input tokens, requests) of round one as the stage would send
    it: its planned parts, each built with what it carries, a part too large
    to send counted as the halves it is sent as. None before the audit
    document and the delivered entries exist."""
    ud, doc = slug_dir / "runs" / unit / "update", slug_dir / "runs" / unit / "final-chronology.md"
    if not doc.is_file() or not (ud / "base.md").is_file():
        return None
    keep = set(exhibit_paths(slug_dir / "out" / unit))
    ents = RR.anchor_entries(doc.read_text(encoding="utf-8"), keep, headings(cfg))
    base = FN.delivered_of((ud / "base.md").read_text(encoding="utf-8"))
    already = already_in(ud)
    total = calls = 0
    todo = RP.plan(ents, keep, npages, already)
    while todo:
        part = todo.pop()
        content = RP.review_content(part, "CLIENT", _picture, ents, base, already, modes)
        est = RP.request_tokens(content)
        halves = RP.size_halves(part, content) if est > RP.REQUEST_TOKENS_MAX else None
        if halves:
            todo += halves
            continue
        total, calls = total + est, calls + 1
    return total, calls
