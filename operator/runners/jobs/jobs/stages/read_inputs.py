"""What a page read is shown, and what it costs, from a job's own files. $0.

The entry read (``stages/entry_audit.py``) and the chronology review read
these: the client line every request opens with, each exhibit page as a
picture, each exhibit's page count, and a list of usage rows priced in
dollars. They lived in the update review's modules until that stage was
removed (2026-11-20); the behaviour is unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .. import budget
from ..medchron.audit.page_text import exhibit_paths
from ..medchron.audit.render import img_block, render
from .base import StageRun

Images = Callable[[int, int], dict[str, Any] | None]


def client_line(sr: StageRun) -> str:
    u = sr.unit
    return f"CLIENT: {u.client_name}; date of birth {u.dob or 'not known'}; incident {sr.job.incident_date}"


def kept_exhibits(sr: StageRun) -> set[int]:
    """The exhibit numbers whose files the unit holds."""
    return set(exhibit_paths(sr.slug_dir / "out" / sr.unit.unit))


def image_source(sr: StageRun) -> Images:
    """(exhibit, page) -> the page's picture block, or None."""
    out = sr.slug_dir / "out" / sr.unit.unit
    pdfs = exhibit_paths(out)

    def get(ex: int, page: int) -> dict[str, Any] | None:
        if ex not in pdfs:
            return None
        p = render(pdfs[ex], page, out / "auditpages", f"ex{ex}")
        return img_block(p) if p else None

    return get


def page_counts(out: Path) -> Callable[[int], int]:
    """exhibit -> its page count (0 for an exhibit the unit does not hold)."""
    from pypdf import PdfReader

    pdfs, cache = exhibit_paths(out), {}

    def n(ex: int) -> int:
        if ex not in cache:
            try:
                cache[ex] = len(PdfReader(str(pdfs[ex])).pages) if ex in pdfs else 0
            except Exception:  # noqa: BLE001 - an unreadable exhibit shows no pages; its entries are still read
                cache[ex] = 0
        return cache[ex]

    return n


def dollars(usage: list[dict[str, Any]]) -> str:
    """The rows' dollars from the pricing table the driver reads: the env
    override, the image's path, else the source tree's own copy."""
    tree = Path(__file__).resolve().parents[4] / "adapter" / "cost_telemetry" / "anthropic_pricing.json"
    for path in (os.environ.get(budget.PRICING_ENV), budget.PRICING_DEFAULT, str(tree)):
        try:
            p = budget.Pricing.load(Path(path)) if path else None
            if p is not None:
                return f"{sum(p.price_row(u) for u in usage):.2f} USD"
        except (OSError, ValueError, KeyError, budget.BudgetError):
            continue
    return "not priced here"
