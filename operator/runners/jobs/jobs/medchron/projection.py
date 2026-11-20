"""What a paid stage is projected to add, from THIS matter's own artifacts,
for the driver's limits (``driver._projection``). Split out of ``driver.py``
so that module stays under the size ceiling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import budget


def read_usd(extracted: Path, usd_per_scanned_page: float, meter: budget.Budget) -> float:
    """The cost of reading the extracted file: its scanned pages at the firm's
    measured per-page transcription rate plus its text at the measured
    per-character rate. The pre-vision projection, and the figure SMD's
    allowance-hold alert prices a unit's pages at (``allowance.py``)."""
    return budget.scanned_pages(extracted) * usd_per_scanned_page + meter.projection(budget.extracted_chars(extracted))


def entry_audit_usd(
    slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing, update: bool = False, client: str = ""
) -> float | None:
    """The entry read (``stages/entry_audit``), either lane's: the entries
    not yet read for their request as it now stands (`client`: the client
    line the reads open with), each read and confirmed, at the judgment tier."""
    from ..stages import entry_audit_io

    return entry_audit_io.projected_usd(slug_dir, unit, cfg, pricing, update, client)


def expected_finish(drv: Any, st: Any, extracted: Path) -> None:
    """The run's expected finish, written once after extract (``eta.stamp``);
    a dry run or a rehearsal writes none."""
    from . import eta

    if not (drv.dry_run or drv.rehearse):
        eta.stamp(st, extracted, drv.log)
