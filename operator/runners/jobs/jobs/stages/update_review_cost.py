"""What the update review (``stages/update_review.py``) is projected to cost,
for the job's spend cap. $0, no model. Split out of the stage on 2026-11-18
(the module-size ceiling); the stage re-exports ``projected_usd``.

Round one is priced from ITS OWN REQUESTS (2026-11-22): the parts the stage
plans (``review_parts.plan``), in its order, each built as the stage builds it
with the delivered and same-date entries it carries, its added entries, the
duplicate and text parts and the size splits, summed by the stage's own
estimate (``review_parts.request_tokens``) and divided by ESTIMATE_SHARE, the
low end of what that estimate measured against the real input. A nearby-
entries block the stage marks for the cache (``review_parts.cache_blocks``)
is priced as written the first time and read after, by the ledger's own rule
(``Pricing.price_row``). Measured before: the old figure (every page once,
doubled, no entries) was $22.02 where round one alone cost $41.00, and $7.13
where the review cost about $23.8.

The rest of the loop is a share of round one (FINISH_SHARE, RECHECK_SHARE),
and only what is still to run is projected: a resumed loop whose round one is
paid (``review_checkpoint.json``, or its review in ``rec["pending"]``) is not
charged for it again; that money is already in the job's spend.

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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import budget, llm
from ..medchron import page_mode as PM
from ..medchron.audit.page_text import exhibit_paths
from ..medchron.update import finisher as FN, review_pages as RV, review_parts as RP, review_repair as RR
from .base import read_json
from .update_base import headings
from .update_review_io import CHECKPOINT, _sha, already_in, compose_sha, page_counts

#: What the rest of the loop re-sends, as a share of round one's projection.
#: Calibrated 2026-11-22 at $0 on four finished updates against their usage
#: ledgers, at the seat's pricing and firm config. Projected over real, round
#: one: 1.05 (152 requests, the count exact), 1.13 (53), 1.25 (9), 1.24 (6).
#: The real finish over real round one: 0.28, 0.31, 0.36 and 0.65; the real
#: re-check: 0.10 to 0.23. FINISH_SHARE covers the finish of the three larger
#: jobs. It does not cover the 6-request job's 0.65, which is cents: its whole
#: round one was about $1.43, so its finish is short by about $0.22, and the
#: two shares together still cover what it spent after round one.
#: At these shares the whole review is above real on all four (from those
#: ratios: $82.03 / $71.74, $30.00 / $23.06, $4.61 / $3.27, $2.93 / $2.56).
FINISH_SHARE = 0.40
RECHECK_SHARE = 0.25
LATER_SHARE = FINISH_SHARE + RECHECK_SHARE
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
BY_REQUESTS, BY_PAGES = "its planned requests", "its pages"


@dataclass(frozen=True)
class Round1:
    """Round one's review requests in the stage's estimate (before the
    ESTIMATE_SHARE correction): input not cached, written to the cache, read
    from it; the requests; what they were sized from."""

    plain: int
    written: int
    read: int
    requests: int
    basis: str


@dataclass(frozen=True)
class Projection:
    usd: float
    round1_usd: float
    round1: Round1
    round1_left: bool
    later_share: float  # of round one, for what runs after it

    def line(self) -> str:
        """The components a replay compares with the ledger's rows (SMD's log only)."""
        r = self.round1
        total, read, written = (int(t / ESTIMATE_SHARE) for t in (r.plain + r.written + r.read, r.read, r.written))
        state = "to run" if self.round1_left else "already paid, not projected"
        return (
            f"update_review projection {self.usd:.2f} USD: round one {self.round1_usd:.2f} USD ({state}), "
            f"{r.requests} request(s) sized from {r.basis}, input {total:,} tokens of which {read:,} read "
            f"from the cache and {written:,} written to it (estimate / {ESTIMATE_SHARE}); "
            f"later rounds {self.later_share:.2f} of round one"
        )


def review_model(cfg: Any) -> str:
    tiers = cfg.get("models", "tiers") or {}
    return str(tiers.get("review") or llm.model_for(cfg, "judgment"))


def page_input_tokens(exs: list[int], npages: Callable[[int], int], modes: Callable[[int, int], PM.Mode] | None) -> int:
    """Round one's page input: every page of `exs` once, as the rule shows it."""
    return sum(RV.mode_tokens(modes(x, p) if modes else None) for x in exs for p in range(1, npages(x) + 1))


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """What the stage is projected to cost from here (``breakdown``)."""
    b = breakdown(slug_dir, unit, cfg, pricing)
    return b.usd if isinstance(b, Projection) else b


def breakdown(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> Projection | float | None:
    """The projection with its components; 0.0 on a document the stage already
    settled clean; None when the update adds no exhibit."""
    doc = slug_dir / "runs" / unit / "final-chronology.md"
    rec = read_json(slug_dir / "runs" / unit / "update" / RECORD, {})
    if rec.get("status") == "clean" and doc.is_file() and rec.get("doc_sha_out") == _sha(doc.read_text("utf-8")):
        # ``_reuse`` returns at once on this document (a resume after SMD's
        # correction, ``jobs.medchron.update_decide``): projecting a full review
        # here would hold a resumed job on the cap for money it never spends.
        return 0.0
    modes = PM.Modes.open(slug_dir, unit)
    try:
        return projection_of(slug_dir, unit, cfg, pricing, modes)
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
    b = projection_of(slug_dir, unit, cfg, pricing, modes)
    return None if b is None else b.usd


def projection_of(
    slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing, modes: Callable[[int, int], PM.Mode] | None
) -> Projection | None:
    exs = new_exhibits(slug_dir, unit)
    if not exs:
        return None
    n = page_counts(slug_dir / "out" / unit)
    r1 = planned(slug_dir, unit, cfg, n, modes)
    if r1 is None:  # no audit document or delivered entries yet: the pages and a full context per part
        parts = max(1, sum(n(x) for x in exs) // RP.WINDOW_PAGES + 1)
        r1 = Round1(page_input_tokens(exs, n, modes) + parts * RP.CONTEXT_MAX * ENTRY_TOKENS, 0, 0, parts, BY_PAGES)
    left, share = still_to_run(slug_dir / "runs" / unit / "update")
    return priced(r1, cfg, pricing, left, share)


def still_to_run(ud: Path) -> tuple[bool, float]:
    """(round one's review still to run, the share of round one the rest
    adds), from the stage's checkpoint as ``update_review_io._resume`` and
    ``update_review._review_once`` read it."""
    row = read_json(ud / CHECKPOINT, {})
    if not isinstance(row, dict) or not row.get("md") or row.get("compose_sha") != compose_sha(ud):
        return True, LATER_SHARE
    done, rec = row.get("done"), row.get("rec")
    if not isinstance(done, int) or not isinstance(rec, dict):
        return True, LATER_SHARE
    if done >= 1:  # round one and its finish are paid: the re-check and its finish remain
        return False, RECHECK_SHARE
    pend = rec.get("pending")
    if isinstance(pend, dict) and pend.get("round") == 1 and pend.get("md_sha") == _sha(str(row["md"])):
        return False, LATER_SHARE  # round one's review is paid: its finish and round two remain
    return True, LATER_SHARE


def priced(r1: Round1, cfg: Any, pricing: budget.Pricing, left: bool = True, share: float = LATER_SHARE) -> Projection:
    """Round one's requests corrected to the real input (ESTIMATE_SHARE) and
    priced by the ledger's own rule, then what is still to run of the loop."""
    row = {
        "model": review_model(cfg),
        "in": int(r1.plain / ESTIMATE_SHARE),
        "cache_write": int(r1.written / ESTIMATE_SHARE),
        "cache_read": int(r1.read / ESTIMATE_SHARE),
        "out": r1.requests * OUTPUT_PER_PART,
    }
    one = pricing.price_row(row)
    return Projection((one if left else 0.0) + one * share, one, r1, left, share)


def bound_usd(pages: int, cfg: Any, pricing: budget.Pricing) -> float | None:
    """The review's bound before ``update_collect`` names the new exhibits
    (``medchron/projection.bound_usd``): every page the update read, shown as
    a picture, each part with a full context, the whole loop. None when it
    read no page."""
    if pages <= 0:
        return None
    parts = pages // RP.WINDOW_PAGES + 1
    r1 = Round1(pages * PAGE_TOKENS + parts * RP.CONTEXT_MAX * ENTRY_TOKENS, 0, 0, parts, BY_PAGES)
    return priced(r1, cfg, pricing).usd


def _picture(_ex: int, _pg: int) -> dict[str, Any]:
    return {"type": "image"}  # priced at the picture figure by ``request_tokens``


def planned(
    slug_dir: Path, unit: str, cfg: Any, npages: Callable[[int], int], modes: Callable[[int, int], PM.Mode] | None
) -> Round1 | None:
    """Round one as the stage would send it: its planned parts in its order,
    each built with what it carries, a part too large to send counted as the
    halves it is sent as, and a nearby-entries block the stage marks for the
    cache written once and read after. None before the audit document and the
    delivered entries exist."""
    ud, doc = slug_dir / "runs" / unit / "update", slug_dir / "runs" / unit / "final-chronology.md"
    if not doc.is_file() or not (ud / "base.md").is_file():
        return None
    keep = set(exhibit_paths(slug_dir / "out" / unit))
    ents = RR.anchor_entries(doc.read_text(encoding="utf-8"), keep, headings(cfg))
    base = FN.delivered_of((ud / "base.md").read_text(encoding="utf-8"))
    already = already_in(ud)
    todo = RP.plan(ents, keep, npages, already)
    repeats = RP.repeated_contexts(todo, ents, base, already)
    plain = written = read = calls = 0
    cached: set[str] = set()
    while todo:
        part = todo.pop(0)
        content = RP.review_content(part, "CLIENT", _picture, ents, base, already, modes)
        est = RP.request_tokens(content)
        halves = RP.size_halves(part, content) if est > RP.REQUEST_TOKENS_MAX else None
        if halves:
            todo[:0] = halves
            continue
        calls += 1
        if "user:1" not in RP.cache_blocks(content, repeats):
            plain += est
            continue
        prefix, ctx = RP.request_tokens(content[:2]), str(content[1].get("text"))
        plain += est - prefix
        read, written = (read + prefix, written) if ctx in cached else (read, written + prefix)
        cached.add(ctx)
    return Round1(plain, written, read, calls, BY_REQUESTS)
