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

from . import driver as driver_mod, job as job_mod


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
) -> tuple[Path, list[driver_mod.Outcome]]:
    copy = prepare(Path(job_dir))
    log(f"[rehearse] workdir {copy}")
    d = driver_mod.Driver(
        copy, firm_config=firm_config, pricing=pricing, log=log, rehearse=True, redo=redo, **driver_kw
    )
    outcomes = d.run()
    for o in outcomes:
        o.notes.append(f"rehearse summary: workdir {copy}")
    return copy, outcomes
