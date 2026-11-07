"""A repair SMD asks for after the pre-delivery review held the letter on its
content.

On 2026-11-07 the review held a finished 10,590-page demand on three content
findings the three check passes had not raised: imaging described as ordered
where the record says recommended, an attending physician named as admitting,
a payment counted in one total and excluded from another. The runner had two
roads from a hold, redo the review or release past it, and neither corrects a
sentence. This is the third: SMD writes the findings in the audit's own shape
(``## AUDIT: <section>`` then ``- <claim> | DRIFTS | <why, naming the
documents>``) at ``data/repair-request-v<n>.md`` for the draft version the
review read, through the root-only resume, and the next attempt repairs
exactly those sections (``Drafter.repair``), audits the new draft with every
unchanged section's audit kept (``Drafter._prior_audit``), settles and gates
it again (``run._final_pass``), renders and reviews afresh. One request buys
one repaired draft and its audit; a resume that finds the repaired draft reads
it back and pays nothing more.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def request_path(data: Path, version: int) -> Path:
    return data / f"repair-request-v{version}.md"


def apply(
    d: Any, data: Path, digest: str, corpus: str, version: int, result: dict[str, Any], log: Callable[[str], None]
) -> tuple[int, dict[str, Any]]:
    """``(version, audit)`` after SMD's request for this version, if any."""
    req = request_path(data, version)
    if not req.is_file():
        return version, result
    log(f"  repair asked by SMD on draft v{version} (review findings): {req.name}")
    d.repair(digest, version, audit_md=req.read_text(encoding="utf-8"))
    version += 1
    return version, d.audit(version, digest, corpus)


__all__ = ["apply", "request_path"]
