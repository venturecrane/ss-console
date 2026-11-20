"""The counts a chronology update's run record carries about its own core.

Derived from the files the stages already save, when the run block is built,
never summed as stages run: a stage that runs twice on a resume would double a
summed count (``run_record.note_counts`` sums), and these are read back by the
hourly triage as facts about one run.

What they answer: how many citations named a copy of the page that was read,
how many findings were one visit written twice (the small reading windows can
split a visit between two requests), what the record-only proofs found, and
what the entry read applied and recorded. The update review's counts went
with that stage on 2026-11-20.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import graded_proof as GP
from .update import cite_read as CR


def _read(path: Path) -> dict[str, Any]:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _num(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _proofs(ud: Path) -> dict[str, int]:
    """The record-only proof findings of the unit (``graded_proof.recorded``:
    proofs 12 to 15, a filed document changed after its proof, a crash in the
    final proofs), named as the run record accepts a count name. Until
    2026-11-18 nothing read them: they were written beside the proofs and
    reached neither the run record nor the triage."""
    slug_dir, unit = ud.parents[2], ud.parent.name
    out: dict[str, int] = {}
    for code, n in GP.recorded(slug_dir, unit).items():
        key = "proof_" + "".join(c if c.isalnum() else "_" for c in str(code).lower())
        out[key[:48]] = out.get(key[:48], 0) + _num(n)
    return out


def _unit(ud: Path) -> dict[str, int]:
    out: dict[str, int] = _proofs(ud)
    rep = _read(ud / "compose_report.json")
    for key in (CR.MOVED, CR.DELIVERED, CR.MIXED):
        if key in rep:
            out[key] = _num(rep.get(key))
    return out


def _entry_read(path: Path) -> dict[str, int]:
    """A full chronology's entry read (``stages/entry_audit``): its findings
    applied from the page, and those recorded only."""
    rec = _read(path)
    return {"entry_read_applied": _num(rec.get("applied")), "entry_read_recorded": _num(rec.get("recorded"))}


def derived(job_dir: str | Path) -> dict[str, int]:
    """The ``checks`` counts for this job, summed over its units; {} for a
    job with no update files and no entry-read record."""
    total: dict[str, int] = {}
    units = [_unit(ud) for ud in sorted(Path(job_dir).glob("data/*/runs/*/update"))]
    units += [_entry_read(p) for p in sorted(Path(job_dir).glob("data/*/runs/*/entry_read.json"))]
    for counts in units:
        for k, n in counts.items():
            total[k] = total.get(k, 0) + n
    return total


def with_derived(counts: dict[str, dict[str, int]], job_dir: str | Path) -> dict[str, dict[str, int]]:
    """``run_record.counts`` with this job's derived core counts laid over its ``checks``."""
    mine = derived(job_dir)
    if not mine:
        return counts
    return {**counts, "checks": {**(counts.get("checks") or {}), **mine}}


__all__ = ["DUPLICATE", "OMITTED", "derived", "with_derived"]
