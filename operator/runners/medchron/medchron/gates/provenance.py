"""medchron_provenance_gate: every file pulled from the matter is accounted
for in the final document, cited or on an explicit exclusion list with a
stated reason, or the run holds. Set arithmetic over artifacts that exist;
no step satisfies it by claiming success. Registered probe: a pulled file in
no unit, not a duplicate, not excluded, not a documented orphan, holds."""

from __future__ import annotations

from ..stages import coverage
from ..stages.base import StageRun


def check(sr: StageRun) -> int:
    return coverage.run(sr)
