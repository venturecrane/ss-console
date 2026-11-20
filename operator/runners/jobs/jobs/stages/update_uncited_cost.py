"""What the uncited-page stage costs before it runs ($0): the reads still to
pay for and the extra writing pass, priced for the driver's cap check
(``driver._projection``) and, from the real requests, for the stage's own
check before its reads (``stages/update_uncited``).

The figures, and where each comes from:

* a page shown as a PICTURE is PICTURE_TOKENS (2,650: an image rendered at the
  extract's long side, measured, ``litigation/rehearse_report``); a page shown
  as text is its characters / 4 (``review_pages.mode_tokens``);
* a read's OUTPUT is the entry reader's projection figure,
  ``entry_audit_io.READ_OUT_EXPECTED`` (2,500: the measured median of its real
  reads on 2026-11-18 was about 1,700 output tokens with thinking at high
  effort, the effort this read uses);
* the extra pass, before any read has answered, is priced as if every open
  page were sent to it: its text as input, and output at the writer's largest
  measured yield (``chunking.YIELD_CEILING`` of its input); once reads have
  answered, only their dated-record pages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import budget, llm, paid
from ..medchron import prompts
from ..medchron.update import review_pages as RV, uncited_read as UR
from . import entry_audit_io as IO, update_uncited_plan as UP
from .chunking import CHARS_PER_TOKEN, YIELD_CEILING

STAGE = "update_uncited"
RECORD = "uncited_pages.json"
PICTURE_TOKENS = 2_650
READ_OUT = IO.READ_OUT_EXPECTED
DIR = "uncited_read"


def _rate_usd(pricing: budget.Pricing, model: str, tin: int, tout: int) -> float:
    rate = pricing.rate_for(model)
    return (tin * rate.input_per_million_cents + tout * rate.output_per_million_cents) / 1e8


def _pricing() -> budget.Pricing | None:
    try:
        return paid.load_pricing()
    except (OSError, ValueError, KeyError, budget.BudgetError):
        return None


def reads_usd(cfg: Any, reqs: list[tuple[str, list[dict[str, Any]]]]) -> float | None:
    """The reads of these requests, priced; None with no rate card on this host."""
    pricing = _pricing()
    if pricing is None:
        return None
    tin = 0
    for system, content in reqs:
        tin += len(system) // CHARS_PER_TOKEN
        for b in content:
            tin += PICTURE_TOKENS if b.get("type") == "image" else len(str(b.get("text", ""))) // CHARS_PER_TOKEN
    return _rate_usd(pricing, IO.model(cfg), tin, READ_OUT * len(reqs))


def _saved(d: Path, run: UR.Run) -> dict[int, UR.Answer] | None:
    for kind in ("read1", "read2"):
        f = d / DIR / f"{run.name}.{kind}.json"
        try:
            return UR.parse(json.loads(f.read_text(encoding="utf-8"))["text"], run)
        except (OSError, ValueError, KeyError, TypeError, UR.Unparsed):
            continue
    return None


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """The reads still to pay for and the extra pass; None before any map exists."""
    from .update_uncited_write import read_extras

    d = slug_dir / "runs" / unit
    if not d.is_dir():
        return None
    sw = UP.sweep(slug_dir, unit, read_extras(d))
    modes = UP.modes_for(slug_dir, unit, sw.docs)
    try:
        UP.prefilter(sw, modes)
        read_in = read_n = write_chars = 0
        system = len(prompts.load(UR.PROMPT, cfg)) // CHARS_PER_TOKEN
        for x, pages in UP.runs(sw.open):
            got = _saved(d, UR.Run(x, pages))
            if got is None:
                read_n += 1
                read_in += system + sum(
                    RV.mode_tokens(modes(x, p)) if modes(x, p).kind == "text" else PICTURE_TOKENS for p in pages
                )
            routed = [p for p in pages if got is None or (p in got and got[p].klass == UR.DATED)]
            write_chars += sum(len(sw.settler.text(x, p)) for p in routed)
    finally:
        modes.close()
    usd = _rate_usd(pricing, IO.model(cfg), read_in, READ_OUT * read_n)
    if write_chars:
        w_in = write_chars // CHARS_PER_TOKEN + len(prompts.load("uncited-write-system", cfg)) // CHARS_PER_TOKEN
        usd += _rate_usd(pricing, llm.model_for(cfg, "composition"), w_in, int(w_in * YIELD_CEILING))
    return usd
