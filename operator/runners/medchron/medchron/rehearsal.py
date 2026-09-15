"""`medchron rehearse <job_dir>`: every deterministic gate, against a copy of
the job's workdir, for $0.

WHY (2026-09-15). A client's chronology was submitted three times and died
three times, each time on a gate that had never seen the matter: two documents
the vendor no longer stored ($13), a billing pass that deleted another stage's
disposition ($13), and a router, a prompt and a falsifier that disagreed about
what a duplicate is ($62). Every one of those was decidable from artifacts
already on disk. This is the command that decides them before a paid stage
spends.

WHAT IT DOES. Copies the slug dir (raw/ symlinked: read-only inputs, 100 MB+;
text/ and out/ byte-copied: extract writes under text/, and page_map.json is
written with write_text, which would truncate a shared inode), rewrites the
envelope's data_root to the copy, keeps install_root real (controls and ICD
are read there, never written), and runs the real Driver in rehearse mode:
walk from the top, done-skipping on, execute every $0 in-process stage and
decision, stop at the first paid or external stage that is not done -- after
running every standalone probe from there onward against whatever exists.

WHAT IT REFUSES. A seat whose daemon is running a job: a hand run is outside
the daemon's cgroup and shares the Machine with the gateway.

The copy is left in place as the evidence; the daemon wipes it with the job
dir at 72 h, so the report is the durable artifact.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import budget as budget_mod, dag, job as job_mod, limits as limits_mod


class RehearsalError(RuntimeError):
    pass


def prepare(job_dir: Path) -> Path:
    """The copied job dir: `<job_dir>/rehearsal/<UTC stamp>/` with its own
    job.yaml pointing at its own data/. Returns that dir."""
    job = job_mod.load(job_dir)
    hb = Path(job.install_root) / "heartbeat.json"
    if hb.is_file():
        try:
            running = json.loads(hb.read_text(encoding="utf-8")).get("running")
        except ValueError:
            running = None
        if running:
            raise RehearsalError(f"the seat is running job {running}; rehearse a parked job, not a live seat")
    # Microseconds in the stamp: two rehearsals in one second (a test loop, or
    # a person re-running after a fix) must not land in one directory.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    copy = job_dir / "rehearsal" / stamp
    copy_root = copy / "data"
    real_slug = job.data_root / job.slug
    dst_slug = copy_root / job.slug
    if real_slug.is_dir():
        shutil.copytree(real_slug, dst_slug, ignore=shutil.ignore_patterns("raw"), symlinks=True)
        if (real_slug / "raw").is_dir():
            os.symlink(real_slug / "raw", dst_slug / "raw", target_is_directory=True)
    else:
        dst_slug.mkdir(parents=True)
    orphan = job.data_root / "usage-ledger-orphan.jsonl"
    if orphan.is_file():
        shutil.copy2(orphan, copy_root / orphan.name)
    env: dict[str, Any] = yaml.safe_load((job_dir / "job.yaml").read_text(encoding="utf-8")) or {}
    env["data_root"] = str(copy_root)
    env["install_root"] = str(job.install_root)
    (copy / "job.yaml").write_text(yaml.safe_dump(env, sort_keys=False), encoding="utf-8")
    return copy


def run(
    job_dir: Path,
    *,
    firm_config: str | None = None,
    pricing: str | None = None,
    log=print,
    redo: tuple[str, ...] = (),
    **driver_kw: Any,
) -> tuple[Path, list[Any]]:
    from . import driver as driver_mod  # lazy: driver imports this module

    copy = prepare(Path(job_dir))
    log(f"[rehearse] workdir {copy}")
    d = driver_mod.Driver(
        copy, firm_config=firm_config, pricing=pricing, log=log, rehearse=True, redo=redo, **driver_kw
    )
    outcomes = d.run()
    for o in outcomes:
        o.notes.append(f"rehearse summary: workdir {copy}")
    return copy, outcomes


# ---- the driver's rehearse half, kept here so driver.py stays under the 500-line module ceiling ----
def stop(drv: Any, stage: dag.Stage, ctx: dag.Ctx, extracted: Path, notes: list[str]) -> Any:
    """A paid or external stage that is not done ends the walk. Before it
    does: the limits are probed (a note, never a hold), the projection is
    stated or declared unprojected, and every standalone probe from this
    stage onward runs against whatever artifacts exist. Coverage and audit
    sit behind the paid merge on every real dead tree, and they are the
    gates that matter -- so they are answered here, for free, instead of
    at the price of reaching them."""
    kind = "paid" if stage.paid else "external"
    if stage.paid:
        try:
            drv._check_limits(stage, ctx, extracted)
        except limits_mod.LimitHold as hold:
            notes.append(f"WOULD HOLD at {stage.name}: {hold.reason}")
        proj = drv._projection(stage, ctx, extracted)
        money = "unprojected" if proj is None else f"~{proj:.2f} USD"
    else:
        money = "would write to the firm's matter" if stage.name == "upload" else "would touch the seat or network"
    notes.append(f"rehearse {stage.name}: STOP {kind}, not done ({money})")
    sr = drv._stage_run(ctx.unit, lambda _m: None)
    for s in dag.stages_from(stage.name):
        if s.rehearse is None:
            continue
        try:
            notes.extend(f"rehearse {s.name}: {line}" for line in s.rehearse(sr))
        except Exception as exc:  # noqa: BLE001 - a probe that cannot read its inputs is a line, not a crash
            notes.append(f"rehearse {s.name}: probe could not run: {type(exc).__name__}: {str(exc)[:120]}")
    pages = budget_mod.pages_read(extracted)
    reason = f"{kind} stage not done; the walk ends here"
    from .driver import Outcome  # lazy: driver imports this module

    return Outcome(ctx.unit.unit, "rehearsed", reason, stage.name, drv.budget.refresh(), pages, notes)


def summary(drv: Any, ctx: dag.Ctx, out: Any) -> Any:
    """The block a person reads: where it stopped, what the ledger says was
    spent against the cap, what is projected and what has no rate. Dollar
    figures live in notes only; `Outcome.reason` is relayed by the daemon
    and must carry none (limits.py)."""
    ext = drv.slug_dir / "extracted.jsonl"
    where = f"stopped at {out.stage} ({out.outcome})" if out.stage else "walk complete: every stage done or $0"
    projected: list[str] = []
    unprojected: list[str] = []
    for s in dag.STAGES:
        if not s.paid:
            continue
        p = drv._projection(s, ctx, ext)
        (unprojected.append(s.name) if p is None else projected.append(f"{s.name} {p:.2f}"))
    out.notes += [
        f"rehearse summary: {where}",
        f"rehearse summary: spent {drv.budget.refresh():.2f} USD from the ledger; cap {drv.limits.cap_usd:.2f}",
        "rehearse summary: projected " + (" | ".join(projected) or "nothing"),
        "rehearse summary: unprojected (no measured rate): " + (", ".join(unprojected) or "none"),
        "rehearse summary: allowance figures are as stamped in job.yaml when that job was last run",
    ]
    return out
