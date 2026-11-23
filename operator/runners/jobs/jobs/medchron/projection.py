"""What a paid stage is projected to add, from THIS matter's own artifacts,
for the driver's limits (``driver._projection``). Split out of ``driver.py``
so that module stays under the size ceiling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import budget, run_record


def read_usd(extracted: Path, usd_per_scanned_page: float, meter: budget.Budget) -> float:
    """The cost of reading the extracted file: its scanned pages at the firm's
    measured per-page transcription rate plus its text at the measured
    per-character rate. The pre-vision projection, and the figure SMD's
    allowance-hold alert prices a unit's pages at (``allowance.py``)."""
    return budget.scanned_pages(extracted) * usd_per_scanned_page + meter.projection(budget.extracted_chars(extracted))


def claims_to_verify(slug_dir: Path, unit: str, cfg: Any = None) -> int | None:
    """Claims in the built chronology STILL TO VERIFY, counted with the
    audit gate's OWN extractor, so the projection counts what the audit
    will actually call on rather than a remembered ratio from some other
    matter. The audit resumes by key, so a claim with a real verdict on
    disk for this very body costs nothing again: live 2026-09-16 a resume
    with 1,998 verdicts already paid for was held at the cap on a
    projection that re-counted all 1,998. None before the chronology
    exists: no document is "no count yet", not zero claims (a rehearsal
    that stops before build_doc would otherwise print the costliest late
    stage as $0)."""
    from .audit import claims as claims_mod
    from .audit.page_text import exhibit_paths

    doc = slug_dir / "runs" / unit / "final-chronology.md"
    if not doc.is_file():
        return None
    out = slug_dir / "out" / unit
    body = claims_mod.body_of(doc.read_text(encoding="utf-8"))
    claims = claims_mod.extract_claims(body, set(exhibit_paths(out)))
    sha = claims_mod.doc_sha_of(body)
    results = out / "audit-results.jsonl"
    rows = claims_mod.read_rows(results) if results.is_file() else []
    verified = {r["key"] for r in rows if r.get("kind") == "real" and r.get("doc_sha") == sha}
    verified -= claims_mod.pending_reaudit(rows)  # a stale verdict the next round pays for again
    if cfg is None or str(cfg.get("levers", "audit_mode", "image")) == "image":
        verified -= claims_mod.text_verdict_keys(rows)  # a text verdict the picture audit reads again
    return sum(1 for c in claims if c["key"] not in verified)


def entry_audit_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """The full chronology's entry read (``stages/entry_audit``): the entries
    not yet read, each read and confirmed, priced at the judgment tier."""
    from ..stages import entry_audit_io

    return entry_audit_io.projected_usd(slug_dir, unit, cfg, pricing)


def expected_finish(drv: Any, st: Any, extracted: Path) -> None:
    """The run's expected finish, written once per attempt after extract
    (``eta.stamp``): a resume or a redo stamps it again from its own start,
    over the stages still to run; a dry run or a rehearsal writes none."""
    from . import eta

    if not (drv.dry_run or drv.rehearse):
        eta.stamp(st, extracted, drv.log, since=getattr(drv, "started", None))


# ---- the check before a paid stage (moved out of ``driver.py``, 2026-11-22) -------
#: The word a stage with no projection carries: its count on the run record
#: (``unprojected_<stage>``), the log, the verdict's notes and a cost hold's reason.
UNPROJECTED = "unprojected"


def stage_usd(drv: Any, stage: Any, ctx: Any, extracted: Path) -> float | None:
    """What this paid stage is projected to add, in dollars, from THIS
    matter's own artifacts. None for a stage with no measured rate -- never
    0.0, which a report would print as "$0" (``checked_usd`` marks such a
    stage UNPROJECTED and checks it at ``bound_usd`` where one exists)."""
    if stage.name == "vision":
        return read_usd(extracted, drv.limits.usd_per_scanned_page, drv.budget)
    if stage.name == "audit" and not drv.job.update:  # the entries still to read (``stages/entry_audit_io``)
        return entry_audit_usd(drv.slug_dir, ctx.unit.unit, drv.cfg, drv.pricing)
    if stage.name == "audit":  # the claims still to verify
        n = claims_to_verify(drv.slug_dir, ctx.unit.unit, drv.cfg)
        return None if n is None else n * drv.limits.usd_per_audit_claim
    if stage.name == "update_review":
        return _update_review(drv, ctx)
    return None


def _update_review(drv: Any, ctx: Any) -> float | None:
    """The review's projection, its components logged for a replay. It plans
    the stage's requests from the audit document and the delivered entries
    BEFORE the stage runs, outside its error handling: anything that fails
    here is logged and the stage is UNPROJECTED (checked at its bound), never
    a driver crash with no stage row."""
    from ..stages import update_review_cost as UC

    try:
        b = UC.breakdown(drv.slug_dir, ctx.unit.unit, drv.cfg, drv.pricing)
    except Exception as exc:  # noqa: BLE001 - a projection that cannot be computed is unprojected, never fatal
        drv.log(f"[limits] update_review: the projection could not be computed ({type(exc).__name__})")
        return None
    if isinstance(b, UC.Projection):
        drv.log(f"[limits] {b.line()}")
        return b.usd
    return b


def bound_usd(drv: Any, stage: Any, ctx: Any, extracted: Path) -> float | None:
    """A conservative figure for a paid stage that has no projection yet,
    where one can be computed: the update review before ``update_collect``
    has named the new exhibits is bounded by every page the update read,
    reviewed as a picture (``update_review_cost.bound_usd``); the full
    build's review by the page looks it already knows it needs (the entry
    reader's flagged pages, ``whose_page.projected_usd``; its own call and the
    pages its check 1 names are not known before it runs, and the doorway's
    per-call check bounds those). None elsewhere."""
    if stage.name == "update_review" and extracted.is_file():
        from ..stages import update_review_cost

        return update_review_cost.bound_usd(budget.pages_read(extracted), drv.cfg, drv.pricing)
    if stage.name == "review" and not drv.job.update:
        from ..stages import whose_page

        return whose_page.projected_usd(drv.slug_dir, ctx.unit.unit, drv.cfg, drv.pricing) or None
    return None


def checked_usd(drv: Any, stage: Any, ctx: Any, extracted: Path, notes: list[str] | None) -> tuple[float, bool]:
    """(the dollars the limits are asked about, whether they are a projection).
    A paid stage with no projection is checked at its bound where one exists;
    where none does, the limits are asked about 0.0, so only what is already
    spent can hold it. Either way it is marked UNPROJECTED and it PROCEEDS: no
    job was ever held by a missing projection, so a hold would be a new way
    for a job to stop, and the per-call check still stops it at the cap.

    THE MARK SMD CAN READ is the run record's ``checks`` count
    ``unprojected_<stage>`` (``run_record.note_counts``), which the lane puts
    on the audit row that ends the attempt. `notes` reach only the verdict
    file and the CLI's report: no job row and no alert carries them."""
    usd = stage_usd(drv, stage, ctx, extracted)
    if usd is not None:
        return usd, True
    bound = bound_usd(drv, stage, ctx, extracted)
    how = f"checked at a bound of {bound:.2f} USD" if bound else "no bound computable; checked against what is spent"
    line = f"{stage.name}: {UNPROJECTED} ({how})"
    drv.log(f"[limits] {line}")
    if notes is not None:
        notes.append(line)
    if not (drv.dry_run or drv.rehearse):  # neither writes to the job's own data root
        run_record.note_counts(drv.job.data_root, "checks", {f"{UNPROJECTED}_{stage.name}": 1})
    return bound or 0.0, False


def check_limits(drv: Any, stage: Any, ctx: Any, extracted: Path, notes: list[str] | None = None) -> None:
    """Before a paid stage (``Driver._check_limits``). Each unit's first paid
    stage in this process also asks the page question, which costs nothing to
    answer. A cost hold (the job's cap or the month's budget) on a stage with
    no projection says so in its reason, with no dollar figure (the daemon
    relays the reason); a page-allowance hold, which no projection moves, does not."""
    from .. import limits as limits_mod
    from .update import charged as update_charged

    spent = drv.budget.refresh()
    projected, measured = checked_usd(drv, stage, ctx, extracted, notes)
    try:
        if ctx.unit.unit in drv._page_checked:
            drv.limits.check_before_paid(projected_usd=projected, spent_usd=spent, stage=stage.name)
            return
        drv._page_checked.add(ctx.unit.unit)
        try:
            drv.limits.check_before_first_paid(
                pages=update_charged.question_pages(drv.slug_dir, ctx.unit.unit, extracted, drv.job.update),
                projected_usd=projected,
                spent_usd=spent,
                stage=stage.name,
                approved_pages=drv._approved_pages,
            )
        except limits_mod.LimitHold as hold:
            if hold.allowance is not None:
                usd = read_usd(extracted, drv.limits.usd_per_scanned_page, drv.budget)
                hold.allowance["projected_cents"] = int(round(usd * 100))
            raise
    except limits_mod.LimitHold as hold:
        if not measured and hold.setting in (limits_mod.CAP_SETTING, limits_mod.BUDGET_SETTING):  # dollar holds only
            hold.reason += f" ({stage.name} had no projection: {UNPROJECTED})"
        raise
