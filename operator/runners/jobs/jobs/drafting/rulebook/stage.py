"""The firm-rules stage: read the rules into clauses, decide each item, write
the firm's words by code, check again (design C). This is the interface only:
``run`` returns the document unchanged, so a firm that switches the stage on
before it is built gets today's documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .types import Fresh, StageDoc, StageJob, StageResult


def run(job: StageJob, doc: StageDoc) -> StageResult:
    """One document through the stage; for now, unchanged."""
    del job
    return StageResult(md=doc.md)


def plan_chars(job: StageJob, docs: Iterable[StageDoc]) -> int:
    """The characters the stage would send for these documents, for the
    estimate before spend; 0 while it sends nothing."""
    del job, docs
    return 0


def fresh(data: Path, cls: str, facts: dict[str, Any]) -> Fresh:
    """The rule ids that moved since the job froze its rules; none yet."""
    del data, cls, facts
    return Fresh()
