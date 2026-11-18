"""What the update review (``stages/update_review.py``) is projected to cost,
for the job's spend cap. $0, no model. Split out of the stage on 2026-11-18
(the module-size ceiling); the stage re-exports ``projected_usd``.

Every page of the new exhibits is priced as the review will SHOW it, by the
one rule both checkers share (``medchron/page_mode.py``): a page shown as its
own text at its characters over four, a page shown as a picture at the
per-picture figure (``review_pages.PAGE_TOKENS``). A matter whose page index
cannot open is priced all pictures, as before the rule existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .. import budget, llm
from ..medchron import page_mode as PM
from ..medchron.update import review_pages as RV, review_parts as RP
from .base import read_json
from .update_review_io import _sha, page_counts

#: The share of round one the rest adds: the finish, the narrow re-check (a
#: subset of round one's pages) and its one finish; kept at a full round, an
#: upper bound now that there is no third review.
LATER_SHARE = 1.0
PAGE_TOKENS = RV.PAGE_TOKENS
RECORD = "review_repair.json"


def review_model(cfg: Any) -> str:
    tiers = cfg.get("models", "tiers") or {}
    return str(tiers.get("review") or llm.model_for(cfg, "judgment"))


def page_input_tokens(exs: list[int], npages: Callable[[int], int], modes: Callable[[int, int], PM.Mode] | None) -> int:
    """Round one's page input: every page of `exs` once, as the rule shows it."""
    return sum(RV.mode_tokens(modes(x, p) if modes else None) for x in exs for p in range(1, npages(x) + 1))


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """What the stage is projected to cost, from this matter's own pages:
    every page of the new exhibits shown once to the review, as text or as a
    picture, the same again for the rest (two finishes and the narrow
    re-check, at most), priced at the review model's input rate (output is
    bounded by the parts and small beside the pages)."""
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
    tokens_in = page_input_tokens(exs, n, modes) * (1 + LATER_SHARE)
    rate = pricing.rate_for(review_model(cfg))
    parts = max(1, sum(n(x) for x in exs) // RP.WINDOW_PAGES + 1)
    return (tokens_in * rate.input_per_million_cents + parts * 4_000 * rate.output_per_million_cents) / 1e8
