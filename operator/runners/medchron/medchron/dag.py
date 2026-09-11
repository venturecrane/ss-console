"""The fixed stage order and each stage's contract.

Every ordering rule here was learned from a delivered defect (the RUNBOOK's
invariants): billing_extract before build_units; build_units after vision;
repair_truncated always before assemble, never gated on map output; filter
before exhibits; condense before summarize; strip as falsify, dry run, apply;
the audit last, and reopened by any later edit to the document. The driver
executes this list; it does not decide order at run time.

A stage is either a subprocess of the frozen pipeline (`script`) or an
in-process function (`runner`), and the order does not know which. Its `argv`
builder reproduces the script's positional contract exactly, so a stage flips
from one to the other without touching the order. `exit_map` turns a stage's
exit code into the outcome vocabulary; anything unmapped and non-zero is
`failed`. Ported so far: list_matter, download, extract, index_msg, fold_msg,
extract_after_fold (PR 2); vision, billing_extract, build_units (PR 4); map,
repair_truncated, assemble, merge (PR 5); group, filter, exhibits,
condense, summarize (PR 6); the audit loop (PR 7); build_doc, the classifiers,
the strip, the coverage gate, the billing chart and worksheet (PR 8); identity, the ICD
fetch, render and manifest (PR 9). Nothing runs as a subprocess any more;
`script` stays on the Stage for the strangler's history and is always "".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .job import Job, Unit
from .stages import (
    assemble as _assemble,
    audit_loop as _audit,
    billing as _billing,
    billing_chart as _bchart,
    billing_docx as _bdocx,
    build_doc as _build_doc,
    classify as _classify,
    compose as _compose,
    condense as _condense,
    coverage as _coverage,
    icd_fetch as _icd,
    identity as _identity,
    manifest as _manifest,
    render as _render,
    strip as _strip,
    upload as _upload,
    download as _download,
    exhibits as _exhibits,
    extract as _extract,
    group as _group,
    listing as _listing,
    merge as _merge,
    msg as _msg,
    repair as _repair,
    scope as _scope,
    summarize as _summarize,
    units as _units,
    vision as _vision,
)

# Outcome vocabulary shared with state.py. HOLD and REFUSE never write to the
# matter; they end the run with a reason a person can act on.
DONE, REFUSED, HELD, FAILED = "done", "refused", "held", "failed"

ArgvFn = Callable[["Ctx"], list[str]]


@dataclass(frozen=True)
class Ctx:
    job: Job
    unit: Unit
    date_stamp: str  # MM-DD-YY for the deliverable names

    @property
    def slug(self) -> str:
        return self.job.slug


@dataclass(frozen=True)
class Stage:
    name: str
    script: str  # file in the pipeline dir; "" for decision stages
    argv: ArgvFn
    paid: bool = False  # True -> the budget check runs first
    scope: str = "unit"  # "slug" runs once per job, "unit" once per client
    requires: tuple[str, ...] = ()
    invalidates: tuple[str, ...] = ()  # stages reopened when this one re-runs
    exit_map: dict[int, tuple[str, str]] = field(default_factory=dict)
    decision: str | None = None  # decisions.py hook name for authoring stages
    once_per_machine: bool = False  # e.g. ICD table fetch
    runner: Callable[..., int] | None = None  # in-process stage; `script` is then ""


def _slug(ctx: Ctx) -> list[str]:
    return [ctx.slug]


def _slug_unit(ctx: Ctx) -> list[str]:
    return [ctx.slug, ctx.unit.unit]


def _slug_unit_date(ctx: Ctx) -> list[str]:
    return [ctx.slug, ctx.unit.unit, ctx.job.incident_date]


STAGES: tuple[Stage, ...] = (
    # ---- $0: selection, pull, extract ------------------------------------
    Stage("list_matter", "", lambda c: [c.job.matter_id], scope="slug", runner=_listing.run),
    Stage("decide_selection", "", _slug, scope="slug", requires=("list_matter",), decision="selection"),
    Stage(
        "download",
        "",
        lambda c: [c.slug, c.job.matter_id],
        scope="slug",
        requires=("decide_selection",),
        runner=_download.run,
        exit_map={
            1: (FAILED, "download: targets are still not pulled after the pass"),
            2: (HELD, "append: a named document id is not on the matter"),
        },
    ),
    Stage("extract", "", _slug, scope="slug", requires=("download",), runner=_extract.run),
    Stage(
        "index_msg", "", lambda c: [c.slug, c.job.matter_id], scope="slug", requires=("extract",), runner=_msg.run_index
    ),
    Stage("decide_fold", "", _slug, scope="slug", requires=("index_msg",), decision="fold"),
    Stage(
        "fold_msg",
        "",
        lambda c: [c.slug, c.job.matter_id, "--fold=@decided"],
        scope="slug",
        requires=("decide_fold",),
        runner=_msg.run_fold,
    ),
    Stage("extract_after_fold", "", _slug, scope="slug", requires=("fold_msg",), runner=_extract.run),
    # ---- paid: transcription, units, billing -----------------------------
    Stage(
        "vision",
        "",
        _slug,
        paid=True,
        scope="slug",
        requires=("extract_after_fold",),
        runner=_vision.run,
        exit_map={
            1: (
                FAILED,
                "vision: a file is incomplete (a page never returned or its batch is still "
                "processing); a rerun resumes it",
            )
        },
    ),
    Stage("decide_billing_docs", "", _slug, scope="slug", requires=("vision",), decision="billing_docs"),
    Stage(
        "billing_extract",
        "",
        _slug,
        paid=True,
        scope="slug",
        requires=("decide_billing_docs",),
        runner=_billing.run,
        exit_map={1: (FAILED, "billing pages could not be transcribed; the totals would be incomplete")},
    ),
    Stage("decide_units", "", _slug, scope="slug", requires=("billing_extract",), decision="units"),
    Stage(
        "build_units",
        "",
        _slug,
        scope="slug",
        requires=("decide_units",),
        runner=_units.run,
        exit_map={2: (REFUSED, "build_units refused: a scanned file is untranscribed or billing_extract is missing")},
    ),
    Stage("identity", "", _slug, scope="slug", requires=("build_units",), runner=_identity.run),
    # ---- paid: composition ------------------------------------------------
    Stage(
        "map",
        "",
        lambda c: [c.slug, c.unit.unit, f"units/{c.unit.unit}.json"],
        paid=True,
        requires=("identity",),
        runner=_compose.run,
        exit_map={
            1: (
                FAILED,
                "map incomplete: a chunk produced no output (refused, emptied, or its batch is "
                "still processing); a rerun resumes it",
            )
        },
    ),
    Stage(
        "repair_truncated",
        "",
        _slug_unit,
        paid=True,
        requires=("map",),
        runner=_repair.run,
        exit_map={1: (FAILED, "a truncated chunk could not be repaired at 2, 3 or 5 parts")},
    ),
    Stage(
        "assemble",
        "",
        _slug_unit,
        requires=("repair_truncated",),
        runner=_assemble.run,
        exit_map={1: (REFUSED, "assemble refused: truncated or model-refused chunks")},
    ),
    Stage(
        "merge",
        "",
        _slug_unit,
        paid=True,
        requires=("assemble",),
        runner=_merge.run,
        exit_map={
            1: (FAILED, "merge: the model could not merge a routed cluster"),
            3: (REFUSED, "merge falsifier: a citation was lost"),
            4: (REFUSED, "merge falsifier: a paragraph was lost"),
            5: (REFUSED, "merge falsifier: an entry was lost"),
        },
    ),
    Stage("group", "", _slug_unit, requires=("merge",), runner=_group.run),
    Stage(
        "filter",
        "",
        lambda c: [*_slug_unit_date(c), c.job.injuries],
        paid=True,
        requires=("group",),
        runner=_scope.run,
        exit_map={1: (REFUSED, "filter refused: merged clusters missing")},
    ),
    Stage(
        "exhibits",
        "",
        _slug_unit,
        requires=("filter",),
        runner=_exhibits.run,
        exit_map={
            1: (REFUSED, "exhibits: a citation could not be remapped, or a cited file sits in the unresolved lane")
        },
    ),
    Stage("condense", "", _slug_unit, paid=True, requires=("exhibits",), runner=_condense.run),
    Stage(
        "summarize",
        "",
        _slug_unit_date,
        paid=True,
        requires=("condense",),
        runner=_summarize.run,
        exit_map={1: (REFUSED, "summary reached beyond the source record")},
    ),
    # ---- document, classification, strip, gates ---------------------------
    Stage(
        "icd_tables",
        "",
        lambda c: [],
        scope="slug",
        once_per_machine=True,
        requires=("summarize",),
        runner=_icd.run,
        exit_map={1: (FAILED, "the CMS ICD tables could not be fetched")},
    ),
    Stage(
        "build_doc",
        "",
        lambda c: [c.slug, c.unit.unit, c.unit.client_name, c.job.incident_date],
        requires=("icd_tables",),
        invalidates=("audit", "coverage_gate", "strip_apply"),
        runner=_build_doc.run,
        exit_map={
            1: (
                REFUSED,
                "build_doc refused: text ahead of the first entry is not a Prior Medical History "
                "heading, or the ICD tables are missing",
            )
        },
    ),
    Stage(
        "classify_nonrecord",
        "",
        _slug_unit,
        requires=("build_doc",),
        runner=_classify.run_nonrecord,
        exit_map={1: (REFUSED, "classify_nonrecord: the chronology carries no citations to guard against")},
    ),
    Stage("decide_control", "", _slug_unit, requires=("classify_nonrecord",), decision="control"),
    Stage(
        "classify_scanned",
        "",
        lambda c: [*_slug_unit(c), "--apply"],
        paid=True,
        requires=("decide_control",),
        runner=_classify.run_scanned,
        exit_map={1: (REFUSED, "classify_scanned: the controls are not authored, or the classifier failed them")},
    ),
    Stage(
        "strip_falsify",
        "",
        lambda c: [*_slug_unit(c), "--falsify"],
        requires=("classify_scanned",),
        runner=_strip.run_falsify,
        exit_map={1: (REFUSED, "strip falsifier: the content check cannot fail, so it proves nothing")},
    ),
    Stage(
        "strip_dry",
        "",
        _slug_unit,
        requires=("strip_falsify",),
        runner=_strip.run_dry,
        exit_map={
            1: (REFUSED, "strip dry run refused: a citation would lose its page, or the classifier's controls failed")
        },
    ),
    Stage(
        "strip_apply",
        "",
        lambda c: [*_slug_unit(c), "--apply"],
        requires=("strip_dry",),
        runner=_strip.run_apply,
        exit_map={1: (REFUSED, "strip apply refused (a prior strip exists, or citations moved)")},
    ),
    Stage("decide_orphans", "", _slug_unit, requires=("strip_apply",), decision="orphans"),
    Stage(
        "coverage_gate",
        "",
        _slug_unit,
        requires=("decide_orphans",),
        runner=_coverage.run,
        exit_map={1: (HELD, "coverage gate: a pulled file is neither cited nor explained")},
    ),
    Stage(
        "billing_chart",
        "",
        lambda c: [c.slug, c.unit.unit, "--patient", c.unit.client_name],
        requires=("coverage_gate",),
        runner=_bchart.run,
    ),
    Stage(
        "billing_docx",
        "",
        lambda c: [
            c.slug,
            c.unit.client_name,
            f"out/{c.unit.unit}/{c.unit.client_name} - Medical Billing Worksheet {c.date_stamp}.docx",
            c.unit.unit,
        ],
        requires=("billing_chart",),
        runner=_bdocx.run,
        exit_map={1: (REFUSED, "billing worksheet refused (a suspect amount, or no patient filter on a joint matter)")},
    ),
    # ---- audit last; render only after it passes ---------------------------
    Stage(
        "audit",
        "",
        lambda c: ["3"],
        paid=True,
        requires=("billing_docx",),
        runner=_audit.run,
        exit_map={
            1: (HELD, "audit coverage: a live claim is not finally SUPPORTED"),
            2: (
                FAILED,
                "audit INVALID: a control was wrongly SUPPORTED, no exhibit bytes, or a repair did not reconcile",
            ),
            3: (
                FAILED,
                "audit refused at the double-sweep guard: prior rows for this body carry keys no longer produced",
            ),
        },
    ),
    Stage(
        "render",
        "",
        lambda c: [
            f"runs/{c.unit.unit}/final-chronology.md",
            f"out/{c.unit.unit}/{c.unit.client_name} - Medical Chronology {c.date_stamp}.docx",
        ],
        requires=("audit",),
        runner=_render.run,
        exit_map={1: (FAILED, "render: the limitations section produced no paragraphs")},
    ),
    Stage(
        "manifest",
        "",
        lambda c: [c.slug, c.unit.unit, c.unit.client_name, c.date_stamp],
        requires=("render",),
        runner=_manifest.run,
        exit_map={1: (FAILED, "manifest: a required deliverable is missing")},
    ),
    # ss#2614: the deliverable set onto the matter. The only stage that writes
    # to the firm's system; idempotent through the recorded folder id.
    Stage(
        "upload",
        "",
        lambda c: [c.slug, c.unit.unit],
        requires=("manifest",),
        runner=_upload.run,
        exit_map={
            1: (FAILED, "upload: refused (a folder of that name is not ours, or the local bytes changed)"),
            2: (
                HELD,
                "upload: the folder read back short after the retries; the files may still be "
                "materializing, nothing was deleted",
            ),
        },
    ),
)

BY_NAME = {s.name: s for s in STAGES}
ORDER = [s.name for s in STAGES]


def validate_dag() -> list[str]:
    """Every `requires` and `invalidates` names a real stage that comes,
    respectively, before and after it."""
    problems: list[str] = []
    pos = {n: i for i, n in enumerate(ORDER)}
    for s in STAGES:
        for r in s.requires:
            if r not in pos:
                problems.append(f"{s.name}: requires unknown stage {r!r}")
            elif pos[r] >= pos[s.name]:
                problems.append(f"{s.name}: requires {r!r} which comes later")
        for inv in s.invalidates:
            if inv not in pos:
                problems.append(f"{s.name}: invalidates unknown stage {inv!r}")
            elif pos[inv] <= pos[s.name]:
                problems.append(f"{s.name}: invalidates {inv!r} which comes earlier")
    if len(set(ORDER)) != len(ORDER):
        problems.append("duplicate stage names")
    return problems


def stages_from(start: str | None) -> list[Stage]:
    if start is None:
        return list(STAGES)
    if start not in BY_NAME:
        raise KeyError(f"unknown stage {start!r}; known: {ORDER}")
    return list(STAGES[ORDER.index(start) :])
