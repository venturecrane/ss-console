"""Where the lanes call the firm-rules stage: the end of a first draft's gate
(``finish.Finish._gate``) and a set revision's render (``revise/set_run.py``).

Each call is a no-op, returning the markdown it was given, unless the firm
authored ``phrase_book`` with that mode in its ``stages`` (``book.stages``).
The model call is the run's own doorway (``ReviseRun._ask``), so a replay
answers it from recordings at $0.
"""

from __future__ import annotations

from typing import Any, cast

from .. import render
from . import book, stage
from .types import Ask, Mode, StageDoc, StageJob


def enabled(firm: Any, mode: Mode) -> bool:
    return mode in book.stages(getattr(firm, "data", None))


def _ask(run: Any) -> Ask | None:
    """The run's model call on the firm's audit model, or None when it has none."""
    make: Any = getattr(run, "_ask", None)
    model: Any = getattr(run.firm, "model", None)
    if make is None or model is None:
        return None
    return cast(Ask, make(model("audit")))


def _job(run: Any, data: Any, mode: Mode) -> StageJob:
    return StageJob(
        data=data, firm=run.firm, ask=_ask(run), log=run.log, request_text=str(run.job.request_text or ""), mode=mode
    )


def at_gate(run: Any, unit: Any, md: str) -> str:
    """A first-draft unit's markdown after the gate, through the stage when on.
    The stage reads the job's frozen rules (``run.data``) and sees only the
    document; its notes lines join the attorney notes. A propounded set comes
    back as given; its findings are kept at ``<data>/rulebook/<unit key>.json``
    (``findings``) for the set lane's repair."""
    if not enabled(run.firm, "first_draft"):
        return md
    body, notes = render.split_notes(md)
    doc = StageDoc(key=unit.key or "doc", md=body, doc_type=unit.variant or run.cls, hint=unit.variant)
    res = stage.run(_job(run, run.data, "first_draft"), doc)
    if res.md == body and not res.notes:
        return md
    notes = "\n".join([notes, *(f"- {n}" for n in res.notes)]).strip()
    return res.md + ("\n" + render.NOTES_MARK + "\n\n" + notes + "\n" if notes else "")


def at_revise(run: Any, k: int, md: str, hint: str | None) -> str:
    """A set revision's base markdown for unit ``k``, through the stage when on.
    The stage's outcome per response is kept at ``<data>/rulebook/<k>.json``."""
    if not enabled(run.firm, "revise"):
        return md
    doc = StageDoc(key=f"{k:02d}", md=md, doc_type=hint or run.cls, hint=hint)
    return stage.run(_job(run, run.data, "revise"), doc).md
