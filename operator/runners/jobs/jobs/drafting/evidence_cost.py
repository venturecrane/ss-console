"""The ledger path's price, projected before anything is paid.

The digest path is priced by characters through the summary plus a flat
drafting tail (``budget.usd_drafting_fixed``). The ledger path reads the WHOLE
corpus ten pages at a time, reads some pages as images, composes over the fact
index, checks every cited sentence against its pages, writes the notes and
reviews: a flat tail cannot stand for that, and a 3,631-page file is not a
30-page one. This projects each stage's tokens from the file's real page and
character counts (``extracted.jsonl``) and prices them at the rate card the
lane bills from (``budget.PRICING_DEFAULT`` on a seat; the tree's copy of the
same file, ``operator/adapter/cost_telemetry/anthropic_pricing.json``, off
one). It replaces the digest, condense and flat tail on this path;
transcription stays priced per scanned page (``demand/preflight.py``). There
is no digest audit on this path (``evidence_lane.py``), so none is priced.

Characters per token default to 2.0, measured on a real A&P corpus
(2026-11-04: dense medical and billing text tokenizes far denser than prose);
pass a measured ratio when one exists. The projection is a range: ``high``
(the given ratio, every loop and repair taken) is what the cap is checked
against, and ``over_cap`` says when it exceeds ``budget.per_job_cap_usd``;
``low`` takes 1.5x the ratio and no repairs.

``python -m jobs.drafting.evidence_cost <job_dir> [--inputs DIR]
[--pricing FILE] [--class CLS] [--chars-per-token R]`` prints the projection
over a job's ``data/extracted.jsonl``: the $0 step, no model call, no seat,
and no exemplar files needed (only the config is read; pins are not checked).
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import evidence_config
from .evidence import prompts as ev_prompts
from .verify import config as verify_config

CHARS_PER_TOKEN = 2.0
LOW_RATIO = 1.5
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
REPAIR_OUT, REVIEW_OUT, NOTES_OUT = 20_000, 10_000, 8_000
LINT_REPAIRS = 2
INDEX_BUDGET_CHARS = 1_600_000  # run.DIGEST_BUDGET_CHARS
#: The rate card off a seat: the tree's copy of the file the seat bills from.
TREE_CARD = ("adapter", "cost_telemetry", "anthropic_pricing.json")


class PricingNotFound(FileNotFoundError):
    """No rate card to price with: the caller names one (``--pricing``)."""


def tree_pricing(start: Path | None = None) -> Path | None:
    """The tree's copy of the card the seat bills from, found by walking up
    from this file to the ``operator/`` directory that holds it; None from an
    installed runner (a venv holds no ``adapter/``)."""
    here = (start or Path(__file__)).resolve()
    for d in here.parents:
        cand = d.joinpath(*TREE_CARD)
        if cand.is_file():
            return cand
    return None


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


def _parts(c: dict[str, int], cls: str, firm_data: dict[str, Any], pricing: Any, cpt: float, full: bool) -> dict:
    ev = evidence_config.settings(firm_data, cls)
    models = firm_data["models"]
    extract_in = (c["chars"] + c["windows"] * (len(ev_prompts.EXTRACT_PROMPT) + 200)) / cpt
    images = min(ev.image_pages, math.ceil(c["pages"] * IMAGE_SHARE))
    index_tokens = min(INDEX_BUDGET_CHARS, c["pages"] * FACTS_PER_PAGE * INDEX_CHARS_PER_FACT) / cpt
    frame = (COMPOSE_FRAME_CHARS + len(ev_prompts.MARKUP_BLOCK)) / cpt
    lints = LINT_REPAIRS if full else 0
    review = models.get("review") or models["audit"]
    parts = {
        "extract": _usd(pricing, models["digest"], extract_in, c["windows"] * EXTRACT_OUT_TOKENS),
        "images": _usd(pricing, models["transcription"], images * IMAGE_IN, images * IMAGE_OUT),
        "compose": _usd(pricing, models["compose"], index_tokens + frame, DRAFT_TOKENS),
        "lint_repair": _usd(pricing, models["repair"], lints * (frame + DRAFT_TOKENS), lints * REPAIR_OUT),
        "notes": _usd(pricing, models["audit"], index_tokens + DRAFT_TOKENS, NOTES_OUT),
        "review": _usd(pricing, review, index_tokens + 2 * DRAFT_TOKENS, REVIEW_OUT),
    }
    vs = verify_config.settings(firm_data, cls)
    parts["check"] = (
        verify_config.projected_for(pricing, vs if full else replace(vs, max_loops=0)) if vs.enabled else 0.0
    )
    return {k: round(v, 2) for k, v in parts.items()}


def project(
    extracted: list[dict[str, Any]],
    firm_data: dict[str, Any],
    pricing: Any,
    cls: str,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> dict[str, Any]:
    """Every paid stage of the ledger path, in dollars: ``usd`` is ``high``."""
    ev = evidence_config.settings(firm_data, cls)
    c = corpus_counts(extracted, ev.page_window)
    high = _parts(c, cls, firm_data, pricing, chars_per_token, full=True)
    low = _parts(c, cls, firm_data, pricing, chars_per_token * LOW_RATIO, full=False)
    cap = float((firm_data.get("budget") or {}).get("per_job_cap_usd") or 0)
    hi = round(sum(high.values()), 2)
    return {
        "usd": hi,
        "high_usd": hi,
        "low_usd": round(sum(low.values()), 2),
        "parts": high,
        "low_parts": low,
        "chars_per_token": chars_per_token,
        "per_job_cap_usd": cap,
        "over_cap": bool(cap) and hi > cap,
        "pages": c["pages"],
        "windows": c["windows"],
        "corpus_chars": c["chars"],
        "image_pages": min(ev.image_pages, math.ceil(c["pages"] * IMAGE_SHARE)),
    }


def load_pricing(path: Path) -> Any:
    """The rate card, in either shape the tree keeps: ``budget.Pricing``'s own
    (``models`` + ``_meta.multipliers``), the same without multipliers (the
    overlay's copy), or the drafting templates' flat card (model rows at the
    top level, multipliers in ``_meta``). Only rates are needed to estimate."""
    from .. import budget as budget_mod

    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("_meta") or {}
    mult = meta.get("multipliers") or meta
    models = data.get("models") or {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
    rates = {}
    for model, row in models.items():
        inp = row.get("input_per_million_cents", row.get("in"))
        out = row.get("output_per_million_cents", row.get("out"))
        if inp is None or out is None:
            continue
        crm = row.get("cache_read_multiplier", row.get("cache_read"))
        rates[model] = budget_mod.Rate(int(inp), int(out), float(crm) if crm is not None else None)
    if not rates:
        raise budget_mod.BudgetError(f"{path}: no model rates read")
    return budget_mod.Pricing(
        rates=rates,
        batch_multiplier=float(mult.get("batch", 0.5)),
        cache_write_multiplier=float(mult.get("cache_write_5m", 1.25)),
        cache_read_multiplier=float(mult.get("cache_read", 0.1)),
        source=str(path),
    )


def pricing_path(explicit: str | None) -> Path:
    """The card the lane bills from: given, the seat's env or default, else the tree's copy."""
    from .. import budget as budget_mod

    if explicit:
        if not Path(explicit).is_file():
            raise PricingNotFound(f"--pricing {explicit}: no such file")
        return Path(explicit)
    for p in (os.environ.get(budget_mod.PRICING_ENV), budget_mod.PRICING_DEFAULT):
        if p and Path(p).is_file():
            return Path(p)
    got = tree_pricing()
    if got is None:
        raise PricingNotFound(
            f"no rate card: not at ${budget_mod.PRICING_ENV}, {budget_mod.PRICING_DEFAULT}, or in a checkout's "
            "operator/adapter/cost_telemetry/; pass --pricing <the anthropic_pricing.json the lane bills from>"
        )
    return got


def firm_config(inputs: str | None) -> dict[str, Any]:
    """The drafting config, schema-checked; its pinned files need not be present."""
    import yaml

    from . import firm as firm_mod

    path = firm_mod.resolve_dir(inputs) / firm_mod.CONFIG_NAME
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems = firm_mod.validate(data)
    if problems:
        raise firm_mod.DraftingConfigError(f"{path}: " + "; ".join(problems))
    return data


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m jobs.drafting.evidence_cost")
    ap.add_argument("job_dir")
    ap.add_argument("--inputs", default=None, help="the drafting inputs dir (default: the seat's)")
    ap.add_argument("--pricing", default=None, help="the rate card (default: the one the lane bills from)")
    ap.add_argument("--class", dest="cls", default="mediation_brief")
    ap.add_argument("--chars-per-token", type=float, default=CHARS_PER_TOKEN, help="a measured ratio, if any")
    a = ap.parse_args(argv)
    data = firm_config(a.inputs)
    try:
        card = pricing_path(a.pricing)
    except PricingNotFound as exc:
        print(f"evidence_cost: {exc}", file=sys.stderr)
        return 2
    rows_path = Path(a.job_dir) / "data" / "extracted.jsonl"
    rows = [json.loads(ln) for ln in rows_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = project(rows, data, load_pricing(card), a.cls, a.chars_per_token)
    scanned = sum(
        (int(r.get("pages") or 1) if r.get("transcribe") == "all" else len(r.get("transcribe") or [])) for r in rows
    )
    out["rate_card"] = str(card)
    out["transcription_usd"] = round(scanned * float(data["budget"]["usd_per_scanned_page"]), 2)
    out["total_low_usd"] = round(out["low_usd"] + out["transcription_usd"], 2)
    out["total_usd"] = round(out["high_usd"] + out["transcription_usd"], 2)
    out["over_cap"] = bool(out["per_job_cap_usd"]) and out["total_usd"] > out["per_job_cap_usd"]
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
