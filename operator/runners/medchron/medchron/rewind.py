"""A stage that asks to rebuild what came before it, in the same run.

WHY (2026-10-23). The coverage gate found a file the composer drew entries
from that the document did not cite, held, and SMD rebuilt the document by hand
(``redo_doc``) and resumed the job. That is the Curtis chronology's shape of
stop: one that needed nobody's judgement, only a rebuild. ``dag.Stage.rewind``
names, per exit code, the stages a stage may reopen; the driver resumes at the
earliest of them instead of ending the run. The stage bounds it to once with
its own marker; ``MAX`` bounds it again here, so a marker that did not hold
can never loop a run.

No stage uses it since 2026-10-24. The coverage gate's rebuild was a no-op:
the gate reads ``entries_scoped_final.md`` and ``page_map.json``, which are
written before ``build_doc``, so rebuilding from ``build_doc`` could never
change its answer. The gate now explains by rule a file whose every entry a
``filter`` rule removed, and holds on the rest at once.
"""

from __future__ import annotations

from typing import Callable

from . import dag
from .state import RunState

MAX = 2


class RewindLoop(RuntimeError):
    """A stage asked to rewind more than ``MAX`` times in one process."""


def open_stages(stage: dag.Stage, code: int, st: RunState, dollars: float | None, pages: int | None) -> bool:
    """True when ``code`` is a rewind of ``stage``: it is left pending (not an
    outcome) and the stages it names are reopened."""
    if code not in stage.rewind:
        return False
    st.finish(stage.name, status="pending", exit_code=code, dollars=dollars, pages=pages, note=f"rewound ({code})")
    st.invalidate(list(stage.rewind[code]))
    return True


def restart(
    stage: dag.Stage, names: tuple[str, ...], update: bool, count: int, notes: list[str], log: Callable[[str], None]
) -> list[dag.Stage]:
    """The stages to run next: from the earliest reopened stage on. Every
    stage still done is skipped by the caller, even under ``--from``, so a
    rebuild never re-pays a classifier it did not reopen."""
    if count > MAX:
        raise RewindLoop(f"{stage.name}: asked to rewind {count} times in one run; refusing to loop")
    order = [s.name for s in dag.stages(update)]
    target = order[min(order.index(n) for n in names if n in order)]
    notes.append(f"{stage.name}: rebuilding from {target} once")
    log(f"[rewind] {stage.name}: reopened {', '.join(names)}; resuming at {target}")
    return dag.stages_from(target, update=update)
