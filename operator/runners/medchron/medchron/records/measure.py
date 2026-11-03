"""What a page read really costs on this seat, measured from finished jobs.

WHY (2026-11-03, the second A&P dry run). The firm yaml priced a page at
$0.009; the run spent about $5 on 23 pages read and 23 the model declined, a
declined page costing a batch attempt and a single-page attempt, both billed.
A pre-flight that prices pages ten times too low lets a job start that cannot
finish. So the pre-flight uses the HIGHER of the firm's figure and this lane's
own measurement, and it counts a declined page as a second paid attempt.

THE MEASUREMENT, kept in the lane's state dir (``price.json``), added once per
job when it ends:

* ``dollars`` and ``attempts``: every page-reading request in the job's usage
  ledger (rows with ``pages`` > 0, priced by ``budget.Pricing``, the same
  pricing the cap uses), and the page images those requests carried;
* ``pages`` and ``refused``: the distinct pages the job finished (read, or
  recorded unreadable) and how many of them the model declined.

``per_attempt = dollars / attempts`` and ``refused_share = refused / pages``.
A page not yet read is projected at ``(1 + refused_share)`` attempts once
measured. With fewer than :data:`MIN_ATTEMPTS` measured attempts the firm's
figure stands, at :data:`WORST_ATTEMPTS` attempts a page: one declined page
re-sends its whole batch of eight a page at a time, so the worst case bills
every page twice (the suite's 47-page file with pages 15-37 declined costs 79
attempts, 1.68x; one declined page in every batch costs 2.0x).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ledger import read_rows

NAME = "price.json"
JOB_NAME = "measure.json"
MIN_ATTEMPTS = 50
#: Attempts a page is projected at before the lane has measured its own.
WORST_ATTEMPTS = 2.0


@dataclass(frozen=True)
class Price:
    per_attempt: float
    attempts_per_page: float
    source: str

    @property
    def per_page(self) -> float:
        return self.per_attempt * self.attempts_per_page

    def describe(self) -> str:
        return f"${self.per_page:.4f} a page ({self.source}; {self.attempts_per_page:.2f} attempts a page)"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def price(state: Path, firm_per_page: float) -> Price:
    """The pre-flight's price of one unread page: the higher of the firm's
    figure and the measured one, with the measured share of declined pages."""
    m = _read(state / NAME)
    attempts, pages = int(m.get("attempts") or 0), int(m.get("pages") or 0)
    share = (int(m.get("refused") or 0) / pages) if pages else 0.0
    if attempts < MIN_ATTEMPTS:
        return Price(firm_per_page, WORST_ATTEMPTS, "the firm's usd_per_page, not yet measured")
    measured = float(m.get("dollars") or 0.0) / attempts
    if measured > firm_per_page:
        return Price(measured, 1.0 + share, f"measured over {attempts} page attempts")
    return Price(firm_per_page, 1.0 + share, f"the firm's usd_per_page, measured over {attempts} page attempts")


def note_pages(data: Path, read: int, refused: int) -> None:
    """Add one pass's finished pages to the job's own count (``data/measure.json``)."""
    path = data / JOB_NAME
    m = _read(path)
    m["pages"] = int(m.get("pages") or 0) + int(read) + int(refused)
    m["refused"] = int(m.get("refused") or 0) + int(refused)
    path.write_text(json.dumps(m, sort_keys=True), encoding="utf-8")


def job_spend(ledger: Path, pricing: Any) -> tuple[float, int]:
    """(dollars, page attempts) of the job's page-reading requests."""
    dollars, attempts = 0.0, 0
    for row in read_rows(ledger):
        pages = int(row.get("pages") or 0)
        if pages > 0:
            dollars += pricing.price_row(row)
            attempts += pages
    return dollars, attempts


def record_job(state: Path, data: Path, ledger: Path, pricing: Any) -> bool:
    """Add this job's measurement to the lane's, once (a marker in the job's
    data dir). Returns whether anything was added."""
    marker = data / ".measured"
    if marker.exists():
        return False
    dollars, attempts = job_spend(ledger, pricing)
    job = _read(data / JOB_NAME)
    if not attempts:
        return False
    total = _read(state / NAME)
    total["dollars"] = float(total.get("dollars") or 0.0) + dollars
    total["attempts"] = int(total.get("attempts") or 0) + attempts
    total["pages"] = int(total.get("pages") or 0) + int(job.get("pages") or 0)
    total["refused"] = int(total.get("refused") or 0) + int(job.get("refused") or 0)
    state.mkdir(parents=True, exist_ok=True)
    tmp = state / f".{NAME}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(total, sort_keys=True), encoding="utf-8")
    tmp.replace(state / NAME)
    marker.write_text("1", encoding="utf-8")
    return True


__all__ = ["MIN_ATTEMPTS", "WORST_ATTEMPTS", "NAME", "Price", "job_spend", "note_pages", "price", "record_job"]
