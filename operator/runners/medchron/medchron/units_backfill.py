"""Keep the units of chronology jobs submitted before the broker kept them.

    python -m medchron.units_backfill [--dry-run]

Root only, on the seat, with the medchron venv's python and the runner daemon's
environment (``SMD_WORKSPACE_BROKER_SOCKET``, ``CUSTOMER_SLUG``,
``SMD_MEDCHRON_RUN_DIR``), like ``medchron.allowance``. One shot, and safe to
run again: a job already kept is skipped.

WHY (2026-10-29). Since that day the broker keeps each submitted job's units
(``workspace_broker/medchron_identity.py``), so an UPDATE on a matter whose
chronology was delivered reuses the date of birth the delivered job ran on
instead of asking the firm to confirm it again. A job submitted before that has
no kept units, and its update would ask. The units are still in the job's own
envelope (``<run dir>/jobs/<job_id>/envelope.json``) until the job dir is wiped,
about 72 hours after delivery, and the broker cannot read that directory. This
reads each one present and hands its units to the root-only broker verb
``medchron_units_backfill``, which keeps them under the same rules as submit and
refuses a job it never queued, a job already kept, units with no date of birth,
and units naming clients the job's ledger row does not.

It prints counts only (jobs seen, recorded, skipped by reason), never a name, a
date or a job id: the terminal it prints to is not where client data goes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

#: Outcomes decided here, before the broker is asked.
LOCAL_SKIPS = ("no_envelope", "unreadable_envelope")


def _units(job_dir: Path) -> tuple[str | None, Any]:
    """``(skip_reason, units)``: the envelope's units, or why there are none."""
    path = job_dir / "envelope.json"
    if not path.is_file():
        return "no_envelope", None
    try:
        env = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable_envelope", None
    if not isinstance(env, dict):
        return "unreadable_envelope", None
    return None, env.get("units")


def run(jobs_dir: Path, broker: Any, *, dry_run: bool = False) -> dict[str, Any]:
    """Every job dir under ``jobs_dir`` through the broker verb; the counts."""
    seen = 0
    outcomes: Counter[str] = Counter()
    dirs = sorted(p for p in jobs_dir.iterdir() if p.is_dir()) if jobs_dir.is_dir() else []
    for jd in dirs:
        seen += 1
        skip, units = _units(jd)
        if skip is not None:
            outcomes[skip] += 1
            continue
        resp = broker._request(
            {"action": "medchron_units_backfill", "job_id": jd.name, "units": units, "dry_run": dry_run}
        )
        outcomes[str(resp.get("outcome") or "unknown")] += 1
    recorded = outcomes.pop("recorded", 0)
    return {"dry_run": dry_run, "jobs_seen": seen, "recorded": recorded, "skipped": dict(sorted(outcomes.items()))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.units_backfill", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="decide every job, keep nothing")
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("REFUSED: run as root on the seat, in the runner daemon's environment", file=sys.stderr)
        return 2
    from . import daemon as daemon_mod
    from .broker_client import BrokerError

    try:
        d = daemon_mod.build()
        counts = run(d.jobs, d.broker, dry_run=a.dry_run)
    except (BrokerError, OSError, SystemExit) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
