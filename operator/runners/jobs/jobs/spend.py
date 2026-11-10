"""What a job spent, and what its ledger row records: one rule for every lane.

A job's paid calls each write a row to a usage ledger under the job's dir
(``llm.Doorway``). The verdict a runner writes at its end carries the same
figure, priced from those rows (``budget.Budget``). A runner killed partway (a
release's -15, a memory kill) writes no verdict, and a lane that recorded the
verdict's figure alone recorded 0: two A&P drafting jobs read $0 after $2.22
and $7.07 (2026-11-06), and six of the seven paid lanes still did until
2026-11-10. So every lane records :func:`recorded_cents`: the larger of what
the runner reported and what its usage ledgers price to. An ordinary job's two
figures are equal, so its row is unchanged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("jobs.daemon")

LEDGER_NAME = "usage-ledger.jsonl"


def priced_cents(job_dir: Path | str) -> int | None:
    """What the job's paid calls cost, from every usage ledger under its dir,
    priced the way the cap prices them (``budget.Pricing``). None when the
    price table cannot be read: a stop or a record is never held up by a figure."""
    from .budget import PRICING_DEFAULT, PRICING_ENV, Pricing
    from .ledger import read_rows

    try:
        pricing = Pricing.load(Path(os.environ.get(PRICING_ENV) or PRICING_DEFAULT))
        dollars = 0.0
        for path in sorted(Path(job_dir).rglob(LEDGER_NAME)):
            for row in read_rows(path):
                dollars += float(pricing.price_row(row))
    except Exception as exc:  # noqa: BLE001 - a missing figure is said, never fatal
        logger.error("could not price the spend of %s: %s", job_dir, exc)
        return None
    return int(round(dollars * 100))


def recorded_cents(job_dir: Path | str, reported: int | None) -> int:
    """The cents a job's row records: the runner's reported figure, or its
    usage ledgers priced by the seat's rate card when they show more. An
    unreadable price table keeps the reported figure."""
    return max(int(reported or 0), priced_cents(job_dir) or 0)


__all__ = ["LEDGER_NAME", "priced_cents", "recorded_cents"]
