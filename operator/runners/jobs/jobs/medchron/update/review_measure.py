"""What the update review's round one sends, measured at $0 on a saved job.

    python -m jobs.medchron.update.review_measure <slug data dir> --unit <unit> [--config PATH] [--pricing PATH]

`<slug data dir>` is the matter's data directory (``<data_root>/<slug>``: the
one holding ``out/<unit>``, ``runs/<unit>/update`` and ``text/``). It BUILDS,
and never sends, every round-one request of the update review twice: under
the page rule (``medchron/page_mode.py``: a page shown as its own text where
that text can be trusted, and nearby entries two parts share read from the
prompt cache) and under the old rule (every page a picture, nothing cached
past the system prompt), and prints COUNTS ONLY: no name, no record text.

It makes no model call and writes nothing: the page index reads the PDFs and
text the job already holds, and a page shown as a picture is counted, never
rendered (the stage renders into ``out/<unit>/auditpages``; this does not).

EXACT: parts, pages as text, pages as pictures, characters of page text,
nearby-entries characters and which of them are cache reads, other text
characters. ESTIMATED: every dollar figure. A picture is priced at the
projection's per-picture figure (``review_pages.PAGE_TOKENS``), not its real
bytes; text at four characters a token; output is left out; a cache entry is
assumed to live through the round. The client line is a placeholder of the
same shape, so no client name is read into the request.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config as config_mod, page_mode as PM, prompts
from ..audit.page_text import exhibit_paths
from . import finisher as FN, review_pages as RV, review_parts as RP, review_repair as RR

CLIENT = "CLIENT: (placeholder); date of birth not known; incident not known"


@dataclass
class Tally:
    parts: int = 0
    text_pages: int = 0
    image_pages: int = 0
    page_chars: int = 0
    context_chars: int = 0
    context_cached_chars: int = 0
    other_chars: int = 0
    usd: float = 0.0
    reasons: Counter = field(default_factory=Counter)


@dataclass
class _Unit:
    unit: str


@dataclass
class _Matter:
    """What ``update_review_io.already_pages`` reads off a stage run: the
    matter's directory and its unit, nothing else."""

    slug_dir: Path
    unit: _Unit


def _placeholder(pdfs: dict[int, Path], npages: Any) -> RV.Images:
    """A picture block of the right type for every page that exists; nothing rendered."""

    def get(ex: int, page: int) -> dict[str, Any] | None:
        if ex not in pdfs or not 1 <= page <= npages(ex):
            return None
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}}

    return get


def requests(slug_dir: Path, unit: str, cfg: Any, modes: Any) -> list[tuple[list[dict[str, Any]], tuple[str, ...]]]:
    """Every round-one review request (content, cache markers), as the
    stage builds them, with `modes` (None: every page a picture, no context
    cache). Reads only."""
    from ...stages.update_base import headings
    from ...stages.update_review_io import already_pages, page_counts

    out, ud = slug_dir / "out" / unit, slug_dir / "runs" / unit / "update"
    md = (slug_dir / "runs" / unit / "final-chronology.md").read_text(encoding="utf-8")
    base = FN.delivered_of((ud / "base.md").read_text(encoding="utf-8"))
    pdfs, npages = exhibit_paths(out), page_counts(out)
    keep = set(pdfs)
    ents = RR.anchor_entries(md, keep, headings(cfg))
    already = already_pages(_Matter(slug_dir, _Unit(unit)))  # type: ignore[arg-type]
    parts = RP.plan(ents, keep, npages, already)
    repeats = RP.repeated_contexts(parts, ents, base, already) if modes is not None else set()
    images = _placeholder(pdfs, npages)
    got = []
    for p in parts:
        content = RP.review_content(p, CLIENT, images, ents, base, already, modes)
        got.append((content, RP.cache_blocks(content, repeats)))
    return got


def tally(reqs: list[tuple[list[dict[str, Any]], tuple[str, ...]]], system: str, model: str, pricing: Any) -> Tally:
    """Counts over built requests, and their input priced as the module
    docstring says."""
    t = Tally(parts=len(reqs))
    sys_tokens = len(system) // RV.CHARS_PER_TOKEN
    cached: set[str] = set()
    for i, (content, marks) in enumerate(reqs):
        reads = RV.page_reads(content)
        t.text_pages += reads["text_pages"]
        t.image_pages += reads["image_pages"]
        t.page_chars += reads["page_chars"]
        ctx = content[1].get("text", "") if len(content) > 1 else ""
        ctx = ctx if ctx.startswith("NEARBY ENTRIES") else ""
        hit = "user:1" in marks and ctx in cached
        t.context_chars += len(ctx)
        t.context_cached_chars += len(ctx) if hit else 0
        texts = sum(len(b.get("text", "")) for b in content if b.get("type") == "text")
        t.other_chars += texts - len(ctx) - reads["page_chars"]
        row = {"model": model, "out": 0, "cache_read": sys_tokens if i else 0, "cache_write": 0 if i else sys_tokens}
        plain = (texts - (len(ctx) if "user:1" in marks else 0)) // RV.CHARS_PER_TOKEN
        row["in"] = plain + reads["image_pages"] * RV.PAGE_TOKENS
        if "user:1" in marks:
            row["cache_read" if hit else "cache_write"] += len(ctx) // RV.CHARS_PER_TOKEN
            cached.add(ctx)
        t.usd += pricing.price_row(row)
    return t


def reasons(slug_dir: Path, unit: str, modes: PM.Modes) -> Counter:
    """Why each page of the new exhibits is a picture, counted (``page_mode``)."""
    from ...stages.update_review_cost import new_exhibits
    from ...stages.update_review_io import page_counts

    n = page_counts(slug_dir / "out" / unit)
    return Counter(modes(x, p).reason for x in new_exhibits(slug_dir, unit) for p in range(1, n(x) + 1))


def report(new: Tally, old: Tally, proj: tuple[float | None, float | None], why: Counter) -> list[str]:
    def row(name: str, a: Any, b: Any) -> str:
        return f"{name:<44} {a!s:>14} {b!s:>14}"

    def usd(v: float | None) -> str:
        return "none" if v is None else f"{v:.2f}"

    return [
        "update review round 1: built, not sent (no model call, nothing written)",
        row("", "page rule", "all pictures"),
        row("parts", new.parts, old.parts),
        row("pages as text", new.text_pages, old.text_pages),
        row("pages as pictures", new.image_pages, old.image_pages),
        row("characters of page text", new.page_chars, old.page_chars),
        row("nearby-entries characters sent", new.context_chars, old.context_chars),
        row("  of which read from the prompt cache", new.context_cached_chars, old.context_cached_chars),
        row("other text characters (part, entries)", new.other_chars, old.other_chars),
        row("round 1 input, USD (estimate)", usd(new.usd), usd(old.usd)),
        row("stage projection for the cap, USD", usd(proj[0]), usd(proj[1])),
        "new-exhibit pages by rule outcome: " + ", ".join(f"{k}={v}" for k, v in sorted(why.items())),
    ]


def measure(slug_dir: Path, unit: str, cfg: Any, pricing: Any) -> list[str]:
    from ...stages.update_review_cost import projection, review_model

    system, model = prompts.load("update-review-system", cfg), review_model(cfg)
    modes = PM.Modes.open(slug_dir, unit)
    try:
        new = tally(requests(slug_dir, unit, cfg, modes), system, model, pricing)
        old = tally(requests(slug_dir, unit, cfg, None), system, model, pricing)
        proj = (projection(slug_dir, unit, cfg, pricing, modes), projection(slug_dir, unit, cfg, pricing, None))
        return report(new, old, proj, reasons(slug_dir, unit, modes))
    finally:
        modes.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m jobs.medchron.update.review_measure", description=__doc__.split("\n")[0]
    )
    ap.add_argument("slug_dir", type=Path, help="the matter's data directory (<data_root>/<slug>)")
    ap.add_argument("--unit", required=True)
    ap.add_argument("--config", default=None, help="the firm config (default: the seat's)")
    ap.add_argument("--pricing", default=None, help="the pricing table (default: the seat's)")
    a = ap.parse_args(argv)
    from ...paid import load_pricing

    if not (a.slug_dir / "runs" / a.unit / "update" / "base.md").is_file():
        print(f"no saved update for unit {a.unit} under {a.slug_dir}", file=sys.stderr)
        return 2
    for line in measure(a.slug_dir, a.unit, config_mod.load(a.config), load_pricing(a.pricing)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
