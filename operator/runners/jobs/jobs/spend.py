"""What a job spent, and what its ledger row records: one rule for every lane.

A job's paid calls each write a row to a usage ledger under the job's dir
(``llm.Doorway``). The verdict a runner writes at its end carries the same
figure, priced from those rows (``budget.Budget``). A runner killed partway (a
release's -15, a memory kill) writes no verdict, and a lane that recorded the
verdict's figure alone recorded 0: two drafting jobs on a client seat read $0
after $2.22 and $7.07 (2026-11-06), and six of the seven paid lanes still did
until 2026-11-10. So every lane records :func:`recorded_cents`: the larger of
what the runner reported and what its usage ledgers price to. An ordinary job's
two figures are equal, so its row is unchanged.

WHICH LEDGERS (:func:`ledgers`). Exactly the ones the lane's own runner
budgets, named per lane, never a glob of the job dir: ``jobs rehearse`` copies
a medchron job's ledgers into ``<job_dir>/rehearsal/<stamp>/data/``, and a glob
counted every copy as the job's spend, debited to the firm's month.

THE SWEEP (:func:`sweep`). The money is watched by code, not by a session: at
most once an hour each lane compares every job that ENDED since the sweep was
first installed on the seat (the watermark file, ``seat_layout.JOBS_SPEND_WATCH``)
with its row: usage that prices above $0 under a row that records $0 raises ONE
``spend_unrecorded`` proof alert (an audit row the hourly work-health triage
lists), carrying the lane, the job id and the two amounts only. Each ending is
checked once (``spend_checked`` in its ``daemon.json``), so the alert never
repeats for the same job and ending. It reads and alerts; it never corrects a
row: a figure SMD bills on is changed by a person.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from . import seat_layout

logger = logging.getLogger("jobs.daemon")

LEDGER_NAME = "usage-ledger.jsonl"


def _medchron_ledgers(job_dir: Path) -> list[Path]:
    """The chronology driver's budgeted ledgers (``medchron.job.usage_ledgers``),
    read from the job's own envelope. A job with no envelope never started a run."""
    from .medchron import job as job_mod

    if not (job_dir / "job.yaml").is_file():
        return []
    return job_mod.usage_ledgers(job_mod.load(job_dir))


def ledgers(job_dir: Path | str, lane: str) -> list[Path]:
    """The usage ledgers lane ``lane``'s runner budgets for this job, named,
    never globbed. Two lanes differ from the rest: the chronology, whose driver
    budgets one ledger per unit plus an orphan ledger, and the note lane, whose
    runner makes no paid calls. Every other lane's runner budgets the one
    ledger under its job's ``data/`` (``<lane>/run.py``: ``self.job.data /
    "usage-ledger.jsonl"``)."""
    jd = Path(job_dir)
    if not lane:
        raise ValueError("a lane name is required to say which ledgers a job's runner budgets")
    if lane == "medchron":
        return _medchron_ledgers(jd)
    if lane == "note":
        return []
    return [jd / "data" / LEDGER_NAME]


def priced_cents(job_dir: Path | str, lane: str) -> int | None:
    """What the job's paid calls cost, from the ledgers its lane's runner
    budgets (:func:`ledgers`), priced the way the cap prices them
    (``budget.Pricing``). None when a figure cannot be read: a stop or a record
    is never held up by a figure."""
    from .budget import PRICING_DEFAULT, PRICING_ENV, Pricing
    from .ledger import read_rows

    try:
        pricing = Pricing.load(Path(os.environ.get(PRICING_ENV) or PRICING_DEFAULT))
        dollars = 0.0
        for path in ledgers(job_dir, lane):
            if path.is_file():
                for row in read_rows(path):
                    dollars += float(pricing.price_row(row))
    except Exception as exc:  # noqa: BLE001 - a missing figure is said, never fatal
        logger.error("could not price the spend of %s job %s: %s", lane, job_dir, exc)
        return None
    return int(round(dollars * 100))


def recorded_cents(job_dir: Path | str, lane: str, reported: int | None) -> int:
    """The cents a job's row records: the runner's reported figure, or its
    budgeted usage ledgers priced by the seat's rate card when they show more.
    An unreadable figure keeps the reported one."""
    return max(int(reported or 0), priced_cents(job_dir, lane) or 0)


#: The watermark's file name, under the lane's run dir (the generated seat path's).
WATERMARK = Path(seat_layout.JOBS_SPEND_WATCH).name
#: How often each lane sweeps.
SWEEP_SECONDS = 3600.0
#: The proof code a job whose usage shows spend while its row shows $0 raises.
ALERT_CODE = "spend_unrecorded"


def watermark(run_dir: Path | str, now: float) -> float:
    """The instant the sweep was first installed on this seat. Written once,
    atomically, by whichever lane gets there first (a link never replaces);
    an unreadable file is written again from ``now``, so history is never
    flagged by a reset."""
    p = Path(run_dir) / WATERMARK
    for _ in range(2):
        try:
            return float(json.loads(p.read_text(encoding="utf-8"))["since"])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("the spend watermark %s is unreadable (%s); starting it again from now", p, exc)
            p.unlink(missing_ok=True)
        tmp = p.with_name(f".{WATERMARK}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"since": now}), encoding="utf-8")
        try:
            os.link(tmp, p)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    return now


def _check(d: Any, job_id: str, ending: str) -> bool:
    """One ended job: True once it has been judged (flagged or clean), False
    when a figure could not be read and the next sweep should try again."""
    from .proof import watch

    priced = priced_cents(d.job_dir(job_id), d.LANE)
    if priced is None:
        return False
    if priced == 0:
        return True
    try:
        row = d.broker.status(job_id)
    except Exception as exc:  # noqa: BLE001 - a broker error is retried next sweep, never fatal
        logger.warning("%s job %s: could not read its row for the spend sweep: %s", d.LANE, job_id, exc)
        return False
    if not isinstance(row, dict):
        logger.warning("%s job %s: no row to compare its spend with", d.LANE, job_id)
        return True
    if int(row.get("cents") or 0) > 0:
        return True
    detail = f"{d.LANE} job {job_id}: usage {priced} cents, recorded 0 cents"
    return watch.alert(d, job_id, ALERT_CODE, detail, unit=f"spend:{ending}")


def sweep(d: Any) -> list[str]:
    """The ended jobs of lane ``d`` whose usage shows spend while their row
    shows $0, alerted once per ending. At most once per ``SWEEP_SECONDS``;
    never raises. Returns the ids alerted this sweep."""
    from .proof import watch

    now = d.clock()
    if now - float(getattr(d, "_spend_swept_at", 0.0)) < SWEEP_SECONDS:
        return []
    d._spend_swept_at = now
    if watch.in_proof_run(d):
        return []
    try:
        since = watermark(d.run_dir, now)  # first, so a seat's first sweep sets it before any job ends
    except OSError as exc:
        logger.error("could not read or write the spend watermark: %s", exc)
        return []
    if not d.jobs.is_dir():
        return []
    flagged: list[str] = []
    for path in sorted(p for p in d.jobs.iterdir() if p.is_dir()):
        st = d._daemon_state(path.name)
        done = st.get("finished_at")
        if not isinstance(done, (int, float)) or float(done) < since:
            continue
        ending = f"{st.get('state')}@{done}"
        if st.get("spend_checked") == ending:
            continue
        before = dict(st.get("proof_alerts") or {})
        if _check(d, path.name, ending):
            d._write_state(path.name, spend_checked=ending)
            if d._daemon_state(path.name).get("proof_alerts") != before:
                flagged.append(path.name)
    return flagged


__all__ = [
    "ALERT_CODE",
    "LEDGER_NAME",
    "SWEEP_SECONDS",
    "WATERMARK",
    "ledgers",
    "priced_cents",
    "recorded_cents",
    "sweep",
    "watermark",
]
