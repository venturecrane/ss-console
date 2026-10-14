"""The order of an UPDATE INTO THE DELIVERED CHRONOLOGY (2026-10-13).

Split out of ``dag.py`` (which holds the run's order and the Stage contract)
so neither module crosses the size ceiling; ``dag.stages(update=True)`` is the
one door to it.

The pull, extraction, email, vision and composition stages are the run's own;
what differs is everything that decides what is read and what is filed: the
delivered document is pulled and proven first, only the new documents'
uncovered pages are composed, only the new claims are audited, and the filing
replaces the delivered document in its own folder. The names that mean the
same act keep their names (``audit``, ``review``, ``upload``), so the proof-run
stop, the rehearsal and the paged reasons read an update the same.
"""

from __future__ import annotations

from dataclasses import replace

from .dag import BY_NAME, FAILED, HELD, REFUSED, Stage, _slug_unit
from .stages import (
    update_base as _ubase,
    update_collect as _ucollect,
    update_finish as _ufinish,
    update_select as _uselect,
    update_upload as _uupload,
)


def _reuse(name: str, **changes: object) -> Stage:
    return replace(BY_NAME[name], **changes)  # type: ignore[arg-type]


_HELD_UPDATE = (HELD, "update_held: the update into the delivered chronology cannot go on as asked")

UPDATE_STAGES: tuple[Stage, ...] = (
    BY_NAME["list_matter"],
    Stage(
        "update_base",
        "",
        _slug_unit,
        requires=("list_matter",),
        runner=_ubase.run,
        external=True,
        exit_map={
            1: (FAILED, "update_base: the delivered package could not be pulled; a rerun pulls it again"),
            2: _HELD_UPDATE,
        },
    ),
    _reuse("decide_selection", requires=("update_base",)),
    BY_NAME["download"],
    BY_NAME["extract"],
    BY_NAME["index_msg"],
    BY_NAME["decide_fold"],
    BY_NAME["fold_msg"],
    BY_NAME["extract_after_fold"],
    BY_NAME["vision"],
    _reuse("identity", requires=("vision",)),
    Stage("update_select", "", _slug_unit, requires=("identity",), runner=_uselect.run, exit_map={2: _HELD_UPDATE}),
    _reuse("map", requires=("update_select",)),
    BY_NAME["repair_truncated"],
    BY_NAME["assemble"],
    BY_NAME["merge"],
    # Before collect: the diagnosis claims the audit reads carry the code the
    # tables read for the record's date (``update/icd.py``).
    _reuse("icd_tables", requires=("merge",)),
    Stage(
        "update_collect",
        "",
        _slug_unit,
        requires=("icd_tables",),
        invalidates=("update_summary", "audit"),
        runner=_ucollect.run_collect,
        exit_map={1: (FAILED, "update_collect: the ICD tables are missing"), 2: _HELD_UPDATE},
    ),
    Stage(
        "update_summary",
        "",
        _slug_unit,
        paid=True,
        requires=("update_collect",),
        invalidates=("audit",),
        runner=_ucollect.run_summary,
        exit_map={1: (REFUSED, "update_summary: the added prior history reached beyond its source")},
    ),
    _reuse("audit", requires=("update_summary",)),
    Stage(
        "update_assemble",
        "",
        _slug_unit,
        requires=("audit",),
        runner=_ufinish.run_assemble,
        exit_map={1: (FAILED, "update_assemble: the ICD tables are missing")},
    ),
    Stage(
        "update_render",
        "",
        _slug_unit,
        requires=("update_assemble",),
        runner=_ufinish.run_render,
        exit_map={1: (FAILED, "render: the limitations section produced no paragraphs")},
    ),
    Stage(
        "update_proofs",
        "",
        _slug_unit,
        requires=("update_render",),
        runner=_ufinish.run_proofs,
        exit_map={
            1: (FAILED, "update proofs: a delivered line was lost, a claim went unaudited, or a value was dropped")
        },
    ),
    Stage(
        "update_manifest",
        "",
        _slug_unit,
        requires=("update_proofs",),
        invalidates=("review",),
        runner=_ufinish.run_manifest,
        exit_map={1: (FAILED, "manifest: a required deliverable is missing")},
    ),
    _reuse("review", requires=("update_manifest",)),
    Stage(
        "upload",
        "",
        lambda c: [c.slug, c.unit.unit],
        requires=("review",),
        runner=_uupload.run,
        external=True,
        exit_map={
            1: (
                FAILED,
                "upload: refused (a name collision in the chronology folder, changed local bytes, or other "
                "bytes read back)",
            ),
            2: (
                HELD,
                "upload: the update could not be filed and proven yet; the delivered chronology is untouched "
                "or both are on the matter",
            ),
        },
    ),
)
