"""A re-run starts where the job it supersedes stopped.

``demand_job_rerun`` (ss#3082) queues a NEW job, with its own id, for a finished
one, and the broker proves the envelope is the same one (its digest). Without
this module the new job would start from an empty ``data/`` and pay again for
the pull, the transcription, the digest and every finished drafting stage of a
job that was held for one of OUR faults (a live demand job, 2026-10-07: held at
the section audit after $20.77 had been spent on stages that were all still
good).

What carries over is everything paid for and keyed to its own inputs: the
stage markers through the drafting stages, the pulled file, the transcription
partials, the digest and its chunks, the gap-audit batches, the drafts and the
per-section audit caches (each keyed by the draft's own hash, so a changed
draft can never collect a stale audit).

What does NOT carry over, so the new job decides it afresh:

* the frozen dates (the re-run files under today's date),
* the drafting gate's results and the render (cheap, and they decide what is
  filed),
* the rendered files, the upload manifest and any delivery record,
* the spend ledger: the new job's spend is its own, and the old job's cents are
  already in the month's total.

Absolute paths inside the copied records (each document's ``text_path``)
are rewritten to the new job dir, so nothing reads the old dir, which is wiped
on its own schedule.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

DROP_STATE = ("dates", "render", "gate.json", "gate-gap-audit.json", "gate-coverage.json")
DROP_PATHS = ("out", "runs", "usage-ledger.jsonl", "gate.json", "gate-gap-audit.json", "gate-coverage.json")
REWRITE_SUFFIXES = (".json", ".jsonl")


def seed(new_job: Path, old_job: Path) -> list[str]:
    """Copy ``old_job/data`` into ``new_job/data`` minus what must be decided
    afresh. Returns the stage markers carried over (empty when nothing was
    seeded). Never overwrites a data dir that already has a state file."""
    src, dst = old_job / "data", new_job / "data"
    if not (src / "state.json").is_file() or (dst / "state.json").is_file():
        return []
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in DROP_PATHS or item.name.startswith(".") or item.name.startswith("state.json"):
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    old, new = str(old_job), str(new_job)
    for p in dst.rglob("*"):
        if p.is_file() and p.suffix in REWRITE_SUFFIXES:
            text = p.read_text(encoding="utf-8", errors="surrogateescape")
            if old in text:
                p.write_text(text.replace(old, new), encoding="utf-8", errors="surrogateescape")
    state = json.loads((src / "state.json").read_text(encoding="utf-8"))
    for k in DROP_STATE:
        state.pop(k, None)
    tmp = dst / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(dst / "state.json")
    return sorted(k for k, v in state.items() if isinstance(v, dict) and v.get("status") == "done")
