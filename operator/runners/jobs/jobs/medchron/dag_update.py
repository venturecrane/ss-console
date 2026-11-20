"""The order of an UPDATE INTO THE DELIVERED CHRONOLOGY (2026-10-13).

Split out of ``dag.py`` (which holds the run's order and the Stage contract)
so neither module crosses the size ceiling; ``dag.stages(update=True)`` is the
one door to it.

The pull, extraction, email, vision and composition stages are the run's own;
what differs is everything that decides what is read and what is filed: the
delivered document is pulled and proven first, only the new documents'
uncovered pages are composed, only the new entries are read against their
pages (the entry read, as the full chronology's, since 2026-11-20), and the filing
replaces the delivered document in its own folder. The names that mean the
same act keep their names (``audit``, ``review``, ``upload``), so the proof-run
stop, the rehearsal and the paged reasons read an update the same.
"""

from __future__ import annotations

from dataclasses import replace

from .dag import BY_NAME, CURRENT, FAILED, HELD, REFUSED, Stage, _slug_unit
from ..stages import (
    billing_update as _bupd,
    download as _download,
    entry_audit as _entry_audit,
    update_base as _ubase,
    update_collect as _ucollect,
    update_final_set as _ufinal,
    update_finish as _ufinish,
    update_pages as _upages,
    update_select as _uselect,
    update_upload as _uupload,
)
from .update import outcome as _UO


def _reuse(name: str, **changes: object) -> Stage:
    return replace(BY_NAME[name], **changes)  # type: ignore[arg-type]


_HELD_UPDATE = (HELD, "update_held: the update into the delivered chronology cannot go on as asked")
#: Nothing new is an answer, not a hold (2026-10-25): delivered already
#: current with nothing filed, once ``update/outcome.end`` proves it.
_CURRENT = (CURRENT, "already current: the delivered chronology already includes what the named documents record")

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
    _reuse("download", exit_map={**_download.EXITS, _download.EXIT_ALL_EXCLUDED: _CURRENT}),
    BY_NAME["extract"],
    BY_NAME["index_msg"],
    BY_NAME["decide_fold"],
    BY_NAME["fold_msg"],
    BY_NAME["extract_after_fold"],
    # Page identity by picture before any page is transcribed: a re-filed
    # scan is never paid for (``update/pageid``).
    Stage(
        "update_pages",
        "",
        _slug_unit,
        requires=("extract_after_fold",),
        runner=_upages.run,
        exit_map={1: (FAILED, "update_pages: no update plan; update_base has not run")},
    ),
    _reuse("vision", requires=("update_pages",)),
    _reuse("identity", requires=("vision",)),
    Stage(
        "update_select",
        "",
        _slug_unit,
        requires=("identity",),
        runner=_uselect.run,
        exit_map={2: _HELD_UPDATE, _UO.EXIT: _CURRENT},
    ),
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
        exit_map={1: (FAILED, "update_collect: the ICD tables are missing"), 2: _HELD_UPDATE, _UO.EXIT: _CURRENT},
    ),
    Stage(
        "update_summary",
        "",
        _slug_unit,
        paid=True,
        requires=("update_collect",),
        invalidates=("audit",),
        runner=_ucollect.run_summary,
        exit_map={
            1: (
                REFUSED,
                "update_summary: the added prior history was refused (beyond its source, no heading, or no text)",
            )
        },
    ),
    # The entry read, as the full chronology's (``stages/entry_audit.py``,
    # 2026-11-20): each new entry read against its own cited pages, a wrong
    # line corrected from the page, and a line found wrong and not corrected
    # held for SMD (``stages/entry_audit_update``). It replaced the per-claim
    # audit and the update review. What reads its document is reopened HERE,
    # not by the final set: an update that already sent its files runs the
    # final set as a no-op on resume (``stages/update_final_set``), and its
    # success must never rebuild what was sent (the send record would hold
    # other bytes). Exit 1 keeps the "audit coverage:" prefix the broker
    # resumes a hold by.
    _reuse(
        "audit",
        requires=("update_summary",),
        invalidates=("update_final_set", "update_assemble", "update_render", "update_proofs", "update_manifest"),
        exit_map={
            1: (
                HELD,
                "audit coverage: an entry was not read against its pages, a line found wrong was not corrected, "
                "or the edit tripwire held",
            ),
            **_entry_audit.EXITS,
        },
    ),
    # The final set of the added entries (``stages/update_final_set``, $0,
    # delete-only): stray citation-only lines joined, a repeat the other entry
    # covers dropped, every heading checked against its pages (recorded only).
    # It reopens nothing: a redo of the audit reopens it and what follows it.
    Stage(
        "update_final_set",
        "",
        _slug_unit,
        requires=("audit",),
        runner=_ufinal.run,
        exit_map=_ufinal.EXITS,
    ),
    Stage(
        "update_assemble",
        "",
        _slug_unit,
        requires=("update_final_set",),
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
    # A request that names a billing record refreshes the billing worksheet
    # (``stages/billing_update``, 2026-11-17); with none named, each is a no-op.
    Stage(
        "update_billing_docs",
        "",
        _slug_unit,
        requires=("update_proofs",),
        runner=_bupd.run_docs,
        exit_map={
            _bupd.EXIT_UNKNOWN: (HELD, "update_held: billing refresh not run: other clients on this matter are unknown")
        },
    ),
    Stage(
        "update_billing_extract",
        "",
        _slug_unit,
        paid=True,
        requires=("update_billing_docs",),
        runner=_bupd.run_extract,
        rehearse=_bupd.projection_lines,
        exit_map={1: (FAILED, "billing_extract: a pipeline defect (an unread page is a named gap, never this exit)")},
    ),
    Stage("update_dos_check", "", _slug_unit, requires=("update_billing_extract",), runner=_bupd.run_dos),
    Stage("update_billing_chart", "", _slug_unit, requires=("update_dos_check",), runner=_bupd.run_chart),
    Stage(
        "update_billing_docx",
        "",
        _slug_unit,
        requires=("update_billing_chart",),
        runner=_bupd.run_docx,
        exit_map={1: (REFUSED, "billing worksheet refused (a suspect amount, or no patient filter on a joint matter)")},
    ),
    Stage(
        "update_manifest",
        "",
        _slug_unit,
        requires=("update_billing_docx",),
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
        exit_map=_uupload.EXITS,
    ),
)
