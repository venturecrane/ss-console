"""Chronology runs from a seat's audit rows: how many finished with nobody stepping in.

WHY (2026-11-17). No real chronology run of 29 had finished unaided, and
nothing computed it: the per-job row keeps only its latest state and the job
dir is wiped at 72 hours. Every transition's audit row now carries a ``run``
block (``operator/workspace_broker/run_meta.py``, built by
``operator/runners/jobs/jobs/run_record.py``), and this module folds a seat's
rows into runs and grades each one.

UNAIDED, defined here and nowhere else. A run is unaided only when ALL hold:

1. it is DELIVERED;
2. every run block on its rows has ``by: code`` (an ``infra_requeue`` is the
   runner's own retry and is ``by: code``; it is counted separately and does
   not make a run aided);
3. no ``replaces`` points at it or from it;
4. no RUNNING row follows a HELD or FAILED row (structural: a step that forgot
   its block still counts as aided, because only a person moves a held or
   failed chronology on);
5. no other job on the same matter was submitted within 7 days of it while
   either of the two was not delivered (a replacement nobody linked).

A run with no ``run`` block on any row is ``unrecorded`` and never unaided:
rows written before the block existed cannot be graded, and history is not
rebuilt (``docs/handbook/work-health.md``).

STOPS, split by cause. A stop at a check (``kind: check``: a gate, a proof, a
review) is the product doing its job and is reported apart from a stop at
plumbing (the pull, the reads, the render, the upload), so a new check that
holds runs is never read as a plumbing regression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from bin.lib import work_health as wh

LANE_PREFIX = "MEDCHRON_JOB_"
SAME_MATTER_WINDOW = timedelta(days=7)
DEFAULT_DAYS = 30


@dataclass
class Run:
    job_id: str
    matter: Optional[str] = None
    rows: list[tuple[datetime, str, dict[str, Any]]] = field(default_factory=list)

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return [md["run"] for _ts, _st, md in self.rows if isinstance(md.get("run"), dict)]

    @property
    def state(self) -> str:
        return self.rows[-1][1]

    @property
    def first(self) -> datetime:
        return self.rows[0][0]


def runs_from_rows(rows: list[dict]) -> list[Run]:
    """Every chronology job the rows show, its rows in ledger order. A row the
    broker wrote for a resume request (``resume_requested``) is a RUNNING row."""
    runs: dict[str, Run] = {}
    for ts, row in wh.stamped(rows):
        kind = str(row.get("action_type") or "")
        if not kind.startswith(LANE_PREFIX):
            continue
        md = wh.meta(row)
        job_id = md.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            continue
        run = runs.setdefault(job_id, Run(job_id=job_id))
        run.rows.append((ts, kind[len(LANE_PREFIX) :], md))
        matter = row.get("matter_ref") or md.get("matter_number")
        if run.matter is None and isinstance(matter, str) and matter:
            run.matter = matter[:64]
    return list(runs.values())


def aided_reasons(run: Run, runs: list[Run]) -> list[str]:
    """Why ``run`` needed someone (empty: it did not)."""
    reasons: list[str] = []
    for b in run.blocks:
        by = b.get("by")
        if by != "code":
            reasons.append(f"by_{by if by in ('firm', 'smd') else 'unknown'}")
        if b.get("replaces"):
            reasons.append("replaces")
        if b.get("event") == "record_gave_up":
            reasons.append("record_gave_up")
    if any(b.get("replaces") == run.job_id for other in runs if other is not run for b in other.blocks):
        reasons.append("replaced")
    seen_bad = False
    for _ts, state, _md in run.rows:
        if state in ("HELD", "FAILED"):
            seen_bad = True
        elif state == "RUNNING" and seen_bad:
            reasons.append("restarted")
            break
    if run.matter:
        for other in runs:
            if other is run or other.matter != run.matter:
                continue
            near = abs(other.first - run.first) <= SAME_MATTER_WINDOW
            if near and (other.state != "DELIVERED" or run.state != "DELIVERED"):
                reasons.append("same_matter")
                break
    return sorted(set(reasons))


def grade(run: Run, runs: list[Run]) -> dict[str, Any]:
    """One run's grade: its state, whether it was recorded, and why it was aided."""
    recorded = bool(run.blocks)
    reasons = aided_reasons(run, runs)
    return {
        "job_id": run.job_id,
        "state": run.state.lower(),
        "recorded": recorded,
        "unaided": recorded and run.state == "DELIVERED" and not reasons,
        "aided": reasons,
    }


def submitted_at(run: Run) -> Optional[datetime]:
    """When the run's SUBMITTED row was written, or None when it is not in the rows."""
    return next((ts for ts, state, _md in run.rows if state == "SUBMITTED"), None)


def read_from(since: datetime) -> datetime:
    """Where a reader must start for a window starting ``since``: early enough
    for the same-matter clause to see a job up to 7 days before it."""
    return since - SAME_MATTER_WINDOW


def summarize(rows: list[dict], since: Optional[datetime] = None) -> dict[str, Any]:
    """The seat's counts for the runs SUBMITTED at or after ``since``. ``rate``
    is unaided over recorded runs (None with none).

    A window cuts history: a run that held and was resumed by SMD before the
    window starts, and delivered inside it, shows only its clean last rows. So
    only a run whose SUBMITTED row is inside the window is graded; any other
    is ``out_of_window``, counted apart and in neither numerator nor
    denominator. ``rows`` must reach back :func:`read_from` ``since`` so the
    same-matter clause sees the job before (2026-11-17 review: both cut-window
    cases graded an aided run unaided). ``since`` None: ``rows`` are a run's
    whole history (a test, an export)."""
    every = runs_from_rows(rows)
    runs = [r for r in every if since is None or ((submitted_at(r) or since - SAME_MATTER_WINDOW) >= since)]
    out: dict[str, Any] = {
        "runs": len(runs),
        "out_of_window": len(every) - len(runs),
        "unaided": 0,
        "aided": 0,
        "held_now": 0,
        "failed": 0,
        "running": 0,
        "unrecorded": 0,
        "infra_requeues": 0,
        "stops_check": 0,
        "stops_plumbing": 0,
        "uncoded": 0,
        "aided_by": {},
    }
    for run in runs:
        g = grade(run, every)
        state = g["state"]
        if not g["recorded"]:
            out["unrecorded"] += 1
        elif g["unaided"]:
            out["unaided"] += 1
        else:
            out["aided"] += 1
        out["held_now"] += state == "held"
        out["failed"] += state == "failed"
        out["running"] += state in ("running", "submitted")
        for reason in g["aided"]:
            out["aided_by"][reason] = out["aided_by"].get(reason, 0) + 1
        for b in run.blocks:
            out["infra_requeues"] += b.get("event") == "infra_requeue"
            out["uncoded"] += b.get("code") == "uncoded"
            if b.get("event") == "stop":
                out["stops_check" if b.get("kind") == "check" else "stops_plumbing"] += 1
    recorded = out["runs"] - out["unrecorded"]
    out["rate"] = round(out["unaided"] / recorded, 3) if recorded else None
    return out


def combine(per_seat: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Every seat's counts summed, the rate recomputed."""
    total: dict[str, Any] = {"aided_by": {}}
    for counts in per_seat.values():
        for k, v in counts.items():
            if k == "aided_by":
                for r, n in v.items():
                    total["aided_by"][r] = total["aided_by"].get(r, 0) + n
            elif isinstance(v, int):
                total[k] = total.get(k, 0) + v
    recorded = total.get("runs", 0) - total.get("unrecorded", 0)
    total["rate"] = round(total.get("unaided", 0) / recorded, 3) if recorded else None
    return total


def line(label: str, c: dict[str, Any]) -> str:
    """One plain line a person reads: counts only, no matter, no job id."""
    rate = "no recorded runs" if c.get("rate") is None else f"{round(100 * c['rate'])}% unaided"
    return (
        f"{label}: {c.get('runs', 0)} chronology run(s), {c.get('unaided', 0)} unaided, {c.get('aided', 0)} aided, "
        f"{c.get('held_now', 0)} held now, {c.get('failed', 0)} failed, {c.get('unrecorded', 0)} unrecorded "
        f"({rate}); stops at a check {c.get('stops_check', 0)}, at plumbing {c.get('stops_plumbing', 0)}; "
        f"infrastructure retries {c.get('infra_requeues', 0)}; uncoded {c.get('uncoded', 0)}; "
        f"started before the window {c.get('out_of_window', 0)}"
    )


def report(per_seat: dict[str, dict[str, Any]], days: int) -> str:
    out = [f"Chronology runs, last {days} day(s):"]
    out += [line(slug, c) for slug, c in sorted(per_seat.items())]
    if len(per_seat) > 1:
        out.append(line("all seats", combine(per_seat)))
    return "\n".join(out)


__all__ = [
    "DEFAULT_DAYS",
    "SAME_MATTER_WINDOW",
    "Run",
    "aided_reasons",
    "combine",
    "grade",
    "line",
    "report",
    "runs_from_rows",
    "summarize",
]
