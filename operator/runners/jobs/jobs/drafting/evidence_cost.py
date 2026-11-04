"""The ledger path's price, projected before anything is paid.

The digest path is priced by characters through the summary plus a flat
drafting tail (``budget.usd_drafting_fixed``). The ledger path reads the WHOLE
corpus ten pages at a time, reads some pages as images, composes over the fact
index, audits, checks every cited sentence against its pages and reviews: a
flat tail cannot stand for that, and a 3,631-page file is not a 30-page one.
This projects each stage's tokens from the file's real page and character
counts (``extracted.jsonl``) and prices them at the run's own rate card
(``budget.Pricing``). It replaces the digest, condense and flat tail on this
path; transcription stays priced per scanned page (``demand/preflight.py``).

The shape constants are deliberately on the high side: an estimate that comes
in under the bill is the failure this exists to prevent.

``python -m jobs.drafting.evidence_cost <job_dir> [--inputs DIR]
[--pricing FILE] [--class CLS]`` prints the projection over a job's
``data/extracted.jsonl``: the $0 step, no model call, no seat.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from . import evidence_config
from .evidence import prompts as ev_prompts
from .verify import config as verify_config

CHARS_PER_TOKEN = 3.5
#: A transcribed page enters the corpus at about this many characters.
TRANSCRIBED_CHARS = 2_500
#: The extract read's answer per window (facts as JSON, about 30 per window).
EXTRACT_OUT_TOKENS = 3_500
FACTS_PER_PAGE = 2.5
INDEX_CHARS_PER_FACT = 420
#: Share of pages whose image is read (signed, form and thin pages).
IMAGE_SHARE = 0.04
IMAGE_IN, IMAGE_OUT = 2_500, 300
#: The house style, the skeleton, an exemplar and the context around the index.
COMPOSE_FRAME_CHARS = 160_000
DRAFT_TOKENS = 30_000
AUDIT_ROUNDS, REPAIR_ROUNDS = 3, 2
AUDIT_OUT, REPAIR_OUT, REVIEW_OUT = 12_000, 20_000, 10_000
LINT_REPAIRS = 2
INDEX_BUDGET_CHARS = 1_600_000  # run.DIGEST_BUDGET_CHARS


def _usd(pricing: Any, model: str, tin: float, tout: float) -> float:
    r = pricing.rate_for(model)
    return (tin * r.input_per_million_cents + tout * r.output_per_million_cents) / 1e8


def corpus_counts(extracted: list[dict[str, Any]], window: int) -> dict[str, int]:
    """Pages, windows and characters the extract read sends, from the
    preflight rows: a page still to transcribe counts at its transcribed size."""
    pages = windows = chars = 0
    for r in extracted:
        n = int(r.get("pages") or 0) or (1 if r.get("text_path") else 0)
        if not n:
            continue
        t = r.get("transcribe")
        scanned = n if t == "all" else len(t or [])
        pages += n
        windows += math.ceil(n / window)
        chars += int(r.get("chars") or 0) + scanned * TRANSCRIBED_CHARS
    return {"pages": pages, "windows": windows, "chars": chars}


def project(extracted: list[dict[str, Any]], firm_data: dict[str, Any], pricing: Any, cls: str) -> dict[str, Any]:
    """Every paid stage of the ledger path, in dollars, and the total."""
    ev = evidence_config.settings(firm_data, cls)
    models = firm_data["models"]
    c = corpus_counts(extracted, ev.page_window)
    prompt_chars = len(ev_prompts.EXTRACT_PROMPT)
    extract_in = (c["chars"] + c["windows"] * (prompt_chars + 200)) / CHARS_PER_TOKEN
    images = min(ev.image_pages, math.ceil(c["pages"] * IMAGE_SHARE))
    index_tokens = min(INDEX_BUDGET_CHARS, c["pages"] * FACTS_PER_PAGE * INDEX_CHARS_PER_FACT) / CHARS_PER_TOKEN
    frame = COMPOSE_FRAME_CHARS / CHARS_PER_TOKEN + len(ev_prompts.MARKUP_BLOCK) / CHARS_PER_TOKEN
    compose_in = index_tokens + frame
    parts = {
        "extract": _usd(pricing, models["digest"], extract_in, c["windows"] * EXTRACT_OUT_TOKENS),
        "images": _usd(pricing, models["transcription"], images * IMAGE_IN, images * IMAGE_OUT),
        "compose": _usd(pricing, models["compose"], compose_in, DRAFT_TOKENS),
        "lint_repair": _usd(
            pricing, models["repair"], LINT_REPAIRS * (frame + DRAFT_TOKENS), LINT_REPAIRS * REPAIR_OUT
        ),
        "audit": _usd(pricing, models["audit"], AUDIT_ROUNDS * (index_tokens + DRAFT_TOKENS), AUDIT_ROUNDS * AUDIT_OUT),
        "repair": _usd(
            pricing, models["repair"], REPAIR_ROUNDS * (compose_in + DRAFT_TOKENS), REPAIR_ROUNDS * REPAIR_OUT
        ),
        "review": _usd(pricing, models.get("review") or models["audit"], 2 * (index_tokens + DRAFT_TOKENS), REVIEW_OUT),
    }
    vs = verify_config.settings(firm_data, cls)
    parts["check"] = verify_config.projected_for(pricing, vs) if vs.enabled else 0.0
    return {
        "usd": round(sum(parts.values()), 2),
        "parts": {k: round(v, 2) for k, v in parts.items()},
        "pages": c["pages"],
        "windows": c["windows"],
        "corpus_chars": c["chars"],
        "image_pages": images,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .. import budget as budget_mod
    from . import firm as firm_mod

    ap = argparse.ArgumentParser(prog="python -m jobs.drafting.evidence_cost")
    ap.add_argument("job_dir")
    ap.add_argument("--inputs", default=None, help="the drafting inputs dir (default: the seat's)")
    ap.add_argument("--pricing", default=None, help="the rate card (default: the seat's)")
    ap.add_argument("--class", dest="cls", default="mediation_brief")
    a = ap.parse_args(argv)
    firm = firm_mod.load(a.inputs)
    import os

    pricing = budget_mod.Pricing.load(
        Path(a.pricing or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)
    )
    rows_path = Path(a.job_dir) / "data" / "extracted.jsonl"
    rows = [json.loads(ln) for ln in rows_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = project(rows, firm.data, pricing, a.cls)
    scanned = sum(
        (int(r.get("pages") or 1) if r.get("transcribe") == "all" else len(r.get("transcribe") or [])) for r in rows
    )
    out["transcription_usd"] = round(scanned * float(firm.data["budget"]["usd_per_scanned_page"]), 2)
    out["total_usd"] = round(out["usd"] + out["transcription_usd"], 2)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
