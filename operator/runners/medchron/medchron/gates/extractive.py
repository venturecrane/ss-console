"""medchron_extractive_gate: the delivered exhibits carry the actual records
only, and a page removed never moves a citation onto the wrong paper. The
strip's remap is verified by content (text and pixels) against the original;
its falsifier proves the check can fail. Registered probe: a citation whose
every page is dropped refuses; a one-page offset is detected."""

from __future__ import annotations

from ..stages import strip
from ..stages.base import StageRun


def falsify(sr: StageRun) -> int:
    return strip.run_falsify(sr)


def dry_run(sr: StageRun) -> int:
    return strip.run_dry(sr)


def apply(sr: StageRun) -> int:
    return strip.run_apply(sr)
