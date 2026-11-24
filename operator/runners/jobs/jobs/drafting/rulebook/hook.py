"""Where the lanes call the firm-rules stage: the end of a first draft's gate
(``finish.Finish._gate``) and a set revision's render (``revise/set_run.py``).

Each call is a no-op, returning the markdown it was given, unless the firm
authored ``phrase_book`` with that mode in its ``stages`` (``book.stages``).
"""

from __future__ import annotations

from typing import Any

from . import book, stage
from .types import Mode, StageDoc, StageJob


def enabled(firm: Any, mode: Mode) -> bool:
    return mode in book.stages(getattr(firm, "data", None))


def _job(run: Any, data: Any, mode: Mode) -> StageJob:
    return StageJob(
        data=data, firm=run.firm, ask=None, log=run.log, request_text=str(run.job.request_text or ""), mode=mode
    )


def at_gate(run: Any, unit: Any, md: str) -> str:
    """A first-draft unit's markdown after the gate, through the stage when on."""
    if not enabled(run.firm, "first_draft"):
        return md
    doc = StageDoc(key=unit.key, md=md, doc_type=unit.variant or run.cls)
    return stage.run(_job(run, unit.data, "first_draft"), doc).md


def at_revise(run: Any, k: int, md: str, hint: str | None) -> str:
    """A set revision's base markdown for unit ``k``, through the stage when on."""
    if not enabled(run.firm, "revise"):
        return md
    doc = StageDoc(key=f"{k:02d}", md=md, doc_type=hint or run.cls, hint=hint)
    return stage.run(_job(run, run.data, "revise"), doc).md
