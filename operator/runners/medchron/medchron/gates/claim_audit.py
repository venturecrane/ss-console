"""medchron_claim_audit: the document is deliverable only when every live
claim has a final verdict and it is SUPPORTED, and no control was wrongly
supported. Registered probe: a document whose claim is UNSUPPORTED (or never
audited) fails the gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..audit import coverage
from ..audit.run import AuditPaths


def check(slug_dir: Path, unit: str, log: Callable[[str], None] = lambda _m: None) -> tuple[bool, dict[str, Any]]:
    return coverage.check(AuditPaths(slug_dir, unit), log)
