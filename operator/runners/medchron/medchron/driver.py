"""`medchron run <job_dir>`: the orchestrator that used to be a Claude session
reading a runbook.

For each unit, in DAG order: skip stages the state file says are done, run the
decision hook or the pipeline script, enforce the cap before every paid stage,
record the outcome, and stop at the first HOLD, REFUSE, or failure. A kill
mid-stage resumes at that stage on the next run. Every subprocess gets the full
env block, so no spend ever lands in the orphan ledger.

HOLD and REFUSE write nothing to the matter. The run's outcome is one word plus
one reason, printed as JSON and as a sentence, because the person reading it
cannot see any artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    __version__,
    budget as budget_mod,
    config as config_mod,
    dag,
    decisions,
    icd_tables,
    job as job_mod,
    limits as limits_mod,
    rehearsal,
    seat as seat_mod,
)
from .covered import covered_sets
from .stages.base import StageRefusal, StageRun
from .state import RunState, state_path

PIPELINE_ENV = "MEDCHRON_PIPELINE_DIR"


class DriverError(RuntimeError):
    pass


@dataclass
class Outcome:
    unit: str
    outcome: str  # delivered | held | refused | failed | dry_run | rehearsed (driver-level, never in state.json)
    reason: str | None
    stage: str | None
    dollars: float
    pages: int
    notes: list[str] = field(default_factory=list)
    # ss#2614: what the seat's ledger records per job. documents = matter
    # files pulled this run (the allowance metric); folder_id + files = the
    # upload stage's read-back, when it ran.
    documents: int = 0
    folder_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    # 2026-09-17: what this unit's delivery COVERED, in document ids, from the
    # coverage gate's own accounting (`covered.py`). The ledger stores it so a
    # later UPDATE can read only what the delivery did not cover, which is what
    # the agreement's definition of an update requires. None when the run left
    # no coverage artifacts: nothing is guessed, and the skill says the record
    # is unknown rather than approximating a delta.
    covered: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def sentence(self) -> str:
        head = f"{self.unit}: {self.outcome}"
        if self.stage:
            head += f" at {self.stage}"
        if self.reason:
            head += f": {self.reason}"
        return f"{head} ({self.dollars:.2f} USD, {self.pages} pages read)"


def _pipeline_dir() -> Path | None:
    """The frozen pipeline checkout, only when a stage still names a script.
    Every stage is in-process since ss#2613 PR 9; the env stays honoured so
    a stage can be flipped back to its script for a diff."""
    raw = os.environ.get(PIPELINE_ENV)
    if not raw:
        if any(s.script for s in dag.STAGES):
            raise DriverError(f"{PIPELINE_ENV} is not set and a stage still runs as a script")
        return None
    p = Path(raw).expanduser()
    if not p.is_dir():
        raise DriverError(f"{PIPELINE_ENV}={p} is not a directory")
    return p


def _pipeline_sha(pipeline: Path) -> str:
    """The git sha of the pipeline checkout when available, else a content sha
    over its scripts, so the state file names the code the run was made with."""
    try:
        out = subprocess.run(  # noqa: S603 - literal git argv, no shell; pipeline is the configured checkout path
            ["git", "-C", str(pipeline), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    h = hashlib.sha256()
    for f in sorted(pipeline.glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return "content:" + h.hexdigest()[:16]


def _env_block(job: job_mod.Job, cfg: config_mod.FirmConfig, unit: job_mod.Unit) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SMD_MC_DATA": str(job.data_root),
            "SMD_SLUG": job.slug,
            "SMD_UNIT": unit.unit,
            "SMD_INCIDENT_DATE": job.incident_date,
            "SMD_BATCH_STAGES": ",".join(cfg.batch_stages),
            "SMD_CACHE": "1" if cfg.get("levers", "cache", True) else "0",
            "SMD_AUDIT_MODE": str(cfg.get("levers", "audit_mode", "image")),
            "SMD_COMPOSE_MAX_TOKENS": str(cfg.get("levers", "compose_max_tokens", 128000)),
        }
    )
    for tier, model in (cfg.get("models", "tiers") or {}).items():
        env[f"SMD_MODEL_{tier.upper()}"] = str(model)
    for key in list(env):
        if key.startswith("SMD_EFFORT_"):
            del env[key]  # effort levers are never set by the driver
    return env


def _resolve_argv(stage: dag.Stage, ctx: dag.Ctx, slug_dir: Path, decided: dict[str, Any]) -> list[str]:
    args = []
    for a in stage.argv(ctx):
        if a == "--fold=@decided":
            shas = decided.get("fold") or []
            a = "--fold=" + ",".join(shas)
        elif a.startswith(("runs/", "out/", "units/")):
            a = str(slug_dir / a)
        args.append(a)
    return args


def _authored_inputs_sha(slug_dir: Path) -> str | None:
    """A fingerprint of the five AUTHORED input files, recorded on every stage.

    Read the name literally: this is not a per-stage fingerprint and never was.
    It took a `stage` argument it never used, under a docstring promising that
    "per-stage input lists arrive with each in-process port" -- every stage is
    in-process now (`dag.py:20-21`) and the lists never arrived. Live proof, off
    a delivered run's `state.json` (2026-09-24): 20 of 39 stages carried the
    identical value and six carried none, because the only thing that moves it
    is an authoring decision landing partway through the walk.

    It is also never compared. `is_done` is `status == "done"` and nothing else
    (`state.py:153`), which is why a resume has to be told explicitly which
    stages to redo (`resume.py`) instead of working it out from this.

    So: nothing may key staleness off this value. Deciding whether a done stage
    is still current needs a real per-stage input list plus the pipeline sha,
    and neither exists. The name now says what the value is rather than what it
    was meant to become, because the old name read as a promise and a session
    building on it in 2026-09 had to be warned off.
    """
    names = ["include.json", "units.json", "billing_docs.json", "msg_fold.json", "orphans.json"]
    h = hashlib.sha256()
    for n in names:
        p = slug_dir / n
        if p.is_file():
            h.update(n.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


class Driver:
    def __init__(
        self,
        job_dir: Path,
        *,
        firm_config: str | None = None,
        pricing: str | None = None,
        dry_run: bool = False,
        start: str | None = None,
        log=print,
        seat_factory=None,
        client=None,
        rehearse: bool = False,
        redo: tuple[str, ...] = (),
    ) -> None:
        self.job = job_mod.load(job_dir)
        self.cfg = config_mod.load(firm_config)
        self.dry_run = dry_run
        self.start = start
        self.log = log
        # `medchron rehearse`: walk from the top with done-skipping ON, execute
        # only $0 in-process stages and decisions, stop at the first paid or
        # external stage that is not done. `--from` would disable done-skipping
        # (line ~304) and turn every done paid stage into a stop, so they are
        # exclusive. `redo` reopens named $0 stages in the (copied) state so a
        # fix can be seen without editing the real state by hand.
        self.rehearse = rehearse
        self.redo = tuple(redo)
        if rehearse and start:
            raise DriverError("rehearse walks from the top; --from would re-run every done stage")
        for name in self.redo:
            s = dag.BY_NAME.get(name)
            if s is None:
                raise DriverError(f"--redo {name!r}: unknown stage")
            # A REHEARSAL is $0 by definition, so it may not reopen a stage that
            # would spend or touch the firm's matter. A real run may: that is
            # what a resume after a fix is for (ss#2903), and refusing it here
            # would make the feature unable to reopen the stage the defect lived
            # in -- which is usually a paid one.
            if self.rehearse and (s.paid or s.external):
                raise DriverError(f"--redo {name!r}: a paid or external stage cannot be rehearsed")
        # The seat is opened lazily by the first stage that reads the matter,
        # so a dry run and a resume past the pull never touch the firm's system.
        # One seat and one SDK client per run, opened lazily by the first stage
        # that needs them and shared by every stage after (a seat opened per
        # stage would list a matter under one session and pull it under another).
        self._seat_factory = seat_factory or (lambda: seat_mod.open_seat(str(self.cfg.get("firm", "slug"))))
        self._seat = None
        self._client = client
        self.pipeline = None if (dry_run or rehearse) else _pipeline_dir()
        pricing_path = Path(pricing or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)
        self.pricing = budget_mod.Pricing.load(pricing_path)
        # The envelope can only LOWER the firm's cap. It used to win outright,
        # which made the cap advisory: anything that could author an envelope
        # could author its way past the firm's posture.
        firm_cap = self.cfg.per_job_cap_usd
        cap = firm_cap if self.job.cap_usd is None else min(self.job.cap_usd, firm_cap)
        if self.job.cap_usd is not None and self.job.cap_usd > firm_cap:
            self.log("[cap] the envelope asked for a higher cap than the firm's; the firm's cap stands")
        self.slug_dir = self.job.data_root / self.job.slug
        ledgers = [self.slug_dir / "runs" / u.unit / "usage-ledger.jsonl" for u in self.job.units]
        ledgers.append(self.job.data_root / "usage-ledger-orphan.jsonl")
        self.budget = budget_mod.Budget(
            self.pricing, cap, ledgers, float(self.cfg.get("budget", "usd_per_million_chars"))
        )
        self.limits = self._build_limits(cap)
        self._first_paid_checked = False
        self.date_stamp = time.strftime("%m-%d-%y")
        self.decided: dict[str, Any] = {}
        problems = dag.validate_dag()
        if problems:
            raise DriverError("DAG invalid: " + "; ".join(problems))

    def _build_limits(self, cap: float) -> limits_mod.Limits:
        """The job's copy of the firm's four controls plus the month's state.

        SEAT MODE FAILS CLOSED. On a client seat the daemon stamps the month's
        counts into every job.yaml before every run; if they are absent the
        broker could not be reached or the envelope is from an older shape,
        and the honest answer is a refusal, not a run metered by the job cap
        alone. A laptop run (no MEDCHRON_SEAT) is allowed to run without them.
        """
        job = self.job
        if os.environ.get("MEDCHRON_SEAT") == "client" and (
            job.allowance_remaining_pages is None or job.month_cents_used is None
        ):
            raise DriverError(
                "seat mode: the job envelope carries no month state "
                "(allowance_remaining_pages, month_cents_used); refusing to run unmetered"
            )
        return limits_mod.Limits(
            cap_usd=cap,
            monthly_budget_usd=self.cfg.monthly_budget_usd,
            usd_per_scanned_page=self.cfg.usd_per_scanned_page,
            usd_per_audit_claim=self.cfg.usd_per_audit_claim,
            month_cents_used=job.month_cents_used,
            allowance_remaining_pages=job.allowance_remaining_pages,
            allowance_month=job.allowance_month,
            allowance_cycle_label=job.allowance_cycle_label,
        )

    def _projection(self, stage: dag.Stage, ctx: dag.Ctx, extracted: Path) -> float | None:
        """What this paid stage is projected to add, in dollars, from THIS
        matter's own artifacts. None for a stage with no measured rate -- never
        0.0, which a report would print as "$0" (the limits treat None as 0 and
        still catch a run that already reached a line)."""
        if stage.name == "vision":
            return budget_mod.scanned_pages(extracted) * self.limits.usd_per_scanned_page + self.budget.projection(
                budget_mod.extracted_chars(extracted)
            )
        if stage.name == "audit":
            n = self._claims(ctx)
            return None if n is None else n * self.limits.usd_per_audit_claim
        return None

    def _claims(self, ctx: dag.Ctx) -> int | None:
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

        doc = self.slug_dir / "runs" / ctx.unit.unit / "final-chronology.md"
        if not doc.is_file():
            return None
        out = self.slug_dir / "out" / ctx.unit.unit
        body = claims_mod.body_of(doc.read_text(encoding="utf-8"))
        claims = claims_mod.extract_claims(body, set(exhibit_paths(out)))
        sha = claims_mod.doc_sha_of(body)
        results = out / "audit-results.jsonl"
        rows = claims_mod.read_rows(results) if results.is_file() else []
        verified = {r["key"] for r in rows if r.get("kind") == "real" and r.get("doc_sha") == sha}
        return sum(1 for c in claims if c["key"] not in verified)

    def _check_limits(self, stage: dag.Stage, ctx: dag.Ctx, extracted: Path) -> None:
        """Before a paid stage. The first paid stage of the process also asks
        the two page questions, which cost nothing to answer."""
        spent = self.budget.refresh()
        projected = self._projection(stage, ctx, extracted) or 0.0
        if not self._first_paid_checked:
            self._first_paid_checked = True
            self.limits.check_before_first_paid(
                pages=budget_mod.pages_read(extracted), projected_usd=projected, spent_usd=spent, stage=stage.name
            )
            return
        self.limits.check_before_paid(projected_usd=projected, spent_usd=spent, stage=stage.name)

    def _before_request(self, stage: str) -> None:
        """The live-mode doorway hook: the cap and the month's budget re-read
        before every paid call, so an overshoot is one call and not one stage.
        Batch mode's twin is `_before_batch`; between them the bound is one
        call or one batch."""
        self.limits.check_each_call(self.budget.refresh(), stage)

    def _before_batch(self, stage: str, items: int, chars: int) -> None:
        """The batch-mode twin. A batch is one commitment -- nothing checks
        between its items and the whole thing is billed -- so the limits see
        the batch's PROJECTED cost before it is submitted, and an overshoot is
        bounded to one batch. Vision batches one item per page, so its rate is
        per item; the other batchable stages are priced from their characters."""
        projected = items * self.limits.usd_per_scanned_page if stage == "vision" else self.budget.projection(chars)
        self.limits.check_before_paid(projected_usd=projected, spent_usd=self.budget.refresh(), stage=stage, batch=True)

    # ---- one unit ---------------------------------------------------------
    def run_unit(self, unit: job_mod.Unit, slug_done: set[str]) -> Outcome:
        st = RunState.load_or_new(
            state_path(self.job.data_root, self.job.slug, unit.unit), slug=self.job.slug, unit=unit.unit
        )
        st.runner_version = __version__
        st.pipeline_sha = _pipeline_sha(self.pipeline) if self.pipeline else f"medchron-{__version__}"
        ctx = dag.Ctx(job=self.job, unit=unit, date_stamp=self.date_stamp)
        notes: list[str] = []
        extracted = self.slug_dir / "extracted.jsonl"
        # A real run honours --redo too (ss#2903). This is the whole invalidation
        # story for a resume: `is_done` is `status == "done"` and nothing else,
        # and `input_sha` is recorded but never compared, so a resume after a fix
        # would otherwise skip the very stage the fix changed and deliver a
        # document built by the old code -- cheaply, and with a green row.
        if self.redo:
            st.invalidate(list(self.redo))
        for stage in dag.stages_from(self.start):
            if stage.scope == "slug" and stage.name in slug_done:
                continue
            if st.is_done(stage.name) and self.start is None:
                self._note_skip(stage, slug_done, notes)
                continue
            if stage.decision:
                out = self._decide(stage, unit, st, notes)
            else:
                out = self._execute(stage, ctx, st, extracted, notes)
            if stage.scope == "slug" and out is None:
                slug_done.add(stage.name)
            if out is not None:
                return rehearsal.summary(self, ctx, out) if self.rehearse else out
            if self.rehearse:
                notes.append(f"rehearse {stage.name}: ok")
        pages = budget_mod.pages_read(extracted)
        if self.dry_run or self.rehearse:
            st_outcome = "dry_run" if self.dry_run else "rehearsed"
        else:
            st.end("delivered", "every stage done; package staged under out/")
            st_outcome = "delivered"
        out = Outcome(unit.unit, st_outcome, None, None, self.budget.refresh(), pages, notes)
        return rehearsal.summary(self, ctx, out) if self.rehearse else out

    def _note_skip(self, stage: dag.Stage, slug_done: set[str], notes: list[str]) -> None:
        """A done stage is skipped. A done SLUG-scope stage is done for every
        unit, so it joins `slug_done` here too: before this, a joint matter's
        second unit found `list_matter` not done in its own state and re-ran it
        -- and paid `vision` -- for that unit, on every resume."""
        if stage.scope == "slug":
            slug_done.add(stage.name)
        if self.rehearse:
            notes.append(f"rehearse {stage.name}: skipped (done)")

    def _decide(self, stage: dag.Stage, unit: job_mod.Unit, st: RunState, notes: list[str]) -> Outcome | None:
        hook_name = stage.decision or ""
        if hook_name in decisions.HOOKS:
            d = decisions.HOOKS[hook_name](self.job, self.cfg, self.slug_dir, dry_run=self.dry_run)
        else:
            d = decisions.UNIT_HOOKS[hook_name](self.job, self.cfg, self.slug_dir, unit, dry_run=self.dry_run)
        notes.extend(f"{d.hook}: {n}" for n in d.notes)
        if d.hook == "fold":
            self.decided["fold"] = (d.payload or {}).get("fold", [])
        if d.held:
            reason = "; ".join(d.holds)
            if self.dry_run:
                # A dry run keeps going so every hook's hold is measured, which
                # is how a rule's hold rate is read off delivered matters.
                notes.append(f"WOULD HOLD at {stage.name}: {reason}")
                return None
            st.finish(stage.name, status="held", exit_code=None, dollars=self.budget.refresh(), pages=None, note=reason)
            st.end("held", reason)
            return Outcome(
                unit.unit,
                "held",
                reason,
                stage.name,
                self.budget.refresh(),
                budget_mod.pages_read(self.slug_dir / "extracted.jsonl"),
                notes,
            )
        if not self.dry_run:
            st.finish(stage.name, status="done", exit_code=0, dollars=self.budget.refresh(), pages=None)
        self.log(f"[decide] {stage.name}: ok" + (f" ({'; '.join(d.notes)})" if d.notes else ""))
        return None

    def _execute(
        self, stage: dag.Stage, ctx: dag.Ctx, st: RunState, extracted: Path, notes: list[str]
    ) -> Outcome | None:
        unit = ctx.unit
        if self.dry_run:
            if stage.paid:
                # A dry run measures the limits without spending: the hold is a
                # note, and the run carries on so every later hold is measured
                # too (the same shape the decision hooks use).
                try:
                    self._check_limits(stage, ctx, extracted)
                except limits_mod.LimitHold as hold:
                    notes.append(f"WOULD HOLD at {stage.name}: {hold.reason}")
            self.log(f"[dry-run] would run {stage.name}: {stage.script} {' '.join(stage.argv(ctx))}")
            return None
        if stage.once_per_machine and (icd_tables.icd_dir(self.job.install_root) / icd_tables.VERSION_FILE).is_file():
            st.finish(stage.name, status="skipped", exit_code=0, dollars=None, pages=None, note="present")
            return None
        if self.rehearse and (stage.paid or stage.external):
            return rehearsal.stop(self, stage, ctx, extracted, notes)
        if stage.paid:
            try:
                self._check_limits(stage, ctx, extracted)
            except limits_mod.LimitHold as hold:
                return self._hold(hold, stage, unit, st, extracted, notes)
        if stage.runner is not None:
            return self._execute_in_process(stage, ctx, st, extracted, notes)
        script = (self.pipeline or Path(".")) / stage.script
        if self.pipeline is None or not script.is_file():
            st.finish(
                stage.name, status="failed", exit_code=None, dollars=None, pages=None, note=f"script missing: {script}"
            )
            st.end("failed", f"pipeline script missing: {stage.script}")
            return Outcome(
                unit.unit,
                "failed",
                f"pipeline script missing: {stage.script}",
                stage.name,
                self.budget.spent(),
                budget_mod.pages_read(extracted),
                notes,
            )
        argv = _resolve_argv(stage, ctx, self.slug_dir, self.decided)
        cmd = (
            ["bash", str(script)]
            if script.suffix == ".sh"
            else [str(self.cfg.get("pipeline", "python") or sys.executable), str(script), *argv]
        )
        st.start(stage.name, input_sha=_authored_inputs_sha(self.slug_dir))
        self.log(f"[run] {stage.name}: {' '.join(cmd[1:])}")
        proc = subprocess.run(  # noqa: S603 - argv is the stage script under bash or the configured interpreter, no shell
            cmd,
            cwd=self.slug_dir,
            env=_env_block(self.job, self.cfg, unit),
            capture_output=True,
            text=True,
            check=False,
        )
        tail = (proc.stdout + proc.stderr)[-2000:]
        (self.slug_dir / "runs" / unit.unit).mkdir(parents=True, exist_ok=True)
        (self.slug_dir / "runs" / unit.unit / f"log-{stage.name}.txt").write_text(
            proc.stdout + proc.stderr, encoding="utf-8"
        )
        dollars = self.budget.refresh()
        pages = budget_mod.pages_read(extracted)
        if proc.returncode == 0:
            st.finish(stage.name, status="done", exit_code=0, dollars=dollars, pages=pages)
            if stage.invalidates:
                st.invalidate(list(stage.invalidates))
            return None
        outcome, reason = stage.exit_map.get(proc.returncode, ("failed", f"exit {proc.returncode}"))
        reason = f"{reason}; last output: {tail.strip()[-400:]}"
        st.finish(stage.name, status=outcome, exit_code=proc.returncode, dollars=dollars, pages=pages, note=reason)
        st.end(outcome, reason)
        return Outcome(unit.unit, outcome, reason, stage.name, dollars, pages, notes)

    def _hold(
        self,
        hold: limits_mod.LimitHold,
        stage: dag.Stage,
        unit: job_mod.Unit,
        st: RunState,
        extracted: Path,
        notes: list[str],
    ) -> Outcome:
        """A limit held the run. This is a HOLD, not a refusal: the firm's own
        posture stopped the package, nothing went wrong, and the daemon relays
        it unprefixed so the seat's reply names the setting rather than an
        error."""
        pages = budget_mod.pages_read(extracted)
        st.finish(stage.name, status="held", exit_code=None, dollars=self.budget.spent(), pages=pages, note=hold.reason)
        st.end("held", hold.reason)
        return Outcome(unit.unit, "held", hold.reason, stage.name, self.budget.spent(), pages, notes)

    def _open_seat(self):
        if self.rehearse:
            raise DriverError("rehearsal: a stage asked for the seat; nothing external runs in a rehearsal")
        if self._seat is None:
            self._seat = self._seat_factory()
        return self._seat

    def _sdk_client(self):
        if self.rehearse:
            raise DriverError("rehearsal: a stage asked for the model; nothing is spent in a rehearsal")
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=600.0, max_retries=0)
        return self._client

    def _stage_run(self, unit: job_mod.Unit, log) -> StageRun:
        return StageRun(
            job=self.job,
            cfg=self.cfg,
            unit=unit,
            slug_dir=self.slug_dir,
            decided=self.decided,
            log=log,
            seat_factory=self._open_seat,
            client_factory=self._sdk_client,
            date_stamp=self.date_stamp,
            before_request=self._before_request,
            before_batch=self._before_batch,
        )

    def _execute_in_process(
        self, stage: dag.Stage, ctx: dag.Ctx, st: RunState, extracted: Path, notes: list[str]
    ) -> Outcome | None:
        """A ported stage: same state record, same log file, same exit-code
        reading as a subprocess stage, so nothing downstream can tell."""
        unit = ctx.unit
        lines: list[str] = []

        def log(msg: str) -> None:
            lines.append(msg)
            self.log(f"  {msg}")

        sr = self._stage_run(unit, log)
        st.start(stage.name, input_sha=_authored_inputs_sha(self.slug_dir))
        self.log(f"[run] {stage.name}: in-process")
        refusal: str | None = None
        runner = getattr(self, "_runner_override", {}).get(stage.name, stage.runner)
        try:
            code = int(runner(sr))
        except limits_mod.LimitHold as hold:
            # A limit tripped mid-stage, through the doorway hook. Whatever the
            # stage had written stays on disk for the resume; the run stops here.
            lines.append(f"HELD: {hold.reason}")
            (self.slug_dir / "runs" / unit.unit).mkdir(parents=True, exist_ok=True)
            (self.slug_dir / "runs" / unit.unit / f"log-{stage.name}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            return self._hold(hold, stage, unit, st, extracted, notes)
        except StageRefusal as exc:
            code, refusal = -1, str(exc)
            lines.append(f"REFUSED: {exc}")
        except Exception:  # noqa: BLE001 - a crash is a failed stage with its trace on file
            code = 1
            lines.append(traceback.format_exc())
        (self.slug_dir / "runs" / unit.unit).mkdir(parents=True, exist_ok=True)
        (self.slug_dir / "runs" / unit.unit / f"log-{stage.name}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        dollars = self.budget.refresh()
        pages = budget_mod.pages_read(extracted)
        if code == 0:
            st.finish(stage.name, status="done", exit_code=0, dollars=dollars, pages=pages)
            if stage.invalidates:
                st.invalidate(list(stage.invalidates))
            return None
        if refusal is not None:
            outcome, reason = "refused", refusal
        else:
            outcome, reason = stage.exit_map.get(code, ("failed", f"exit {code}"))
            reason = f"{reason}; last output: {' | '.join(lines)[-400:]}"
        st.finish(stage.name, status=outcome, exit_code=code, dollars=dollars, pages=pages, note=reason)
        st.end(outcome, reason)
        return Outcome(unit.unit, outcome, reason, stage.name, dollars, pages, notes)

    # ---- the job ----------------------------------------------------------
    def run(self) -> list[Outcome]:
        outcomes: list[Outcome] = []
        slug_done: set[str] = set()
        for unit in self.job.units:
            o = self.run_unit(unit, slug_done)
            o.documents = _documents_pulled(self.slug_dir)
            delivery = self.slug_dir / "runs" / unit.unit / "delivery.json"
            if delivery.is_file():
                try:
                    d = json.loads(delivery.read_text(encoding="utf-8"))
                    o.folder_id = d.get("folder_id")
                    o.files = list(d.get("files") or [])
                except (OSError, ValueError):
                    pass
            if o.outcome in ("delivered", "dry_run"):
                try:
                    o.covered = covered_sets(self.slug_dir, unit.unit, self.cfg)
                except (OSError, ValueError, KeyError) as exc:
                    # A coverage record we could not derive is reported absent,
                    # never approximated: an over-inclusive covered set drops
                    # records from every later update, silently.
                    self.log(f"[run] covered: no record derived for {unit.unit}: {exc}")
            outcomes.append(o)
        return outcomes


def _documents_pulled(slug_dir: Path) -> int:
    """Matter files pulled this run (`raw_manifest.jsonl` rows that landed)."""
    p = slug_dir / "raw_manifest.jsonl"
    if not p.is_file():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("ok"):
                n += 1
        except ValueError:
            continue
    return n


def report(outcomes: list[Outcome]) -> str:
    lines = [o.sentence() for o in outcomes]
    for o in outcomes:
        lines.extend(f"  note: {n}" for n in o.notes)
    return "\n".join(lines)


def to_json(outcomes: list[Outcome]) -> str:
    return json.dumps([o.to_dict() for o in outcomes], indent=1)
