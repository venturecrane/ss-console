"""Recompute the coverage record of delivered joint-matter chronologies.

    python -m medchron.covered_backfill [--dry-run]

Root only, on the seat, with the medchron venv's python and the runner daemon's
environment (``SMD_WORKSPACE_BROKER_SOCKET``, ``CUSTOMER_SLUG``,
``SMD_MEDCHRON_RUN_DIR``), like ``medchron.units_backfill``. One shot, and safe
to run again: a record already correct comes back ``unchanged``.

WHY (2026-10-29). A joint matter (two or more clients in one job) recorded
every cited exhibit of both clients as uncovered: ``covered.covered_sets``
judged the WHOLE matter manifest for each unit, so each client's cited files
read "not explained" in the other client's record, and the union kept
"uncovered in any unit". The next update on that matter re-read and re-filed
exhibits the firm already had. The runner now judges each unit on its own
files plus the rows no unit adopted; this recomputes, with that fixed code, the
record of every delivered multi-unit job whose directory is still on the seat
(about 72 hours after delivery) and hands it to the root-only broker verb
``medchron_covered_recompute``, which replaces only ``covered_json`` on the
ledger row.

A job whose directory is gone is NOT recomputed. Neither the ledger nor the
broker holds which unit adopted which file, and that is the one fact the fix
needs, so a record cannot be rebuilt from them. A job whose directory remains
without its matter data counts as ``job_dir_gone``; a job wiped whole is not in
the listing at all, and its ledger record stays as delivered (an update on it
then re-reads files it need not, which costs pages but drops nothing).

It prints counts only (jobs seen, recomputed, skipped by reason), never a name
or a job id: the terminal it prints to is not where client data goes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import config as config_mod, job as job_mod
from .covered import covered_payload

#: Outcomes decided here, before the broker is asked.
LOCAL_SKIPS = ("not_delivered", "unreadable_job", "update_job", "single_unit", "job_dir_gone", "no_record_derived")


def _state(job_dir: Path) -> str:
    try:
        return str(json.loads((job_dir / "daemon.json").read_text(encoding="utf-8")).get("state") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def decide(job_dir: Path, cfg: Any) -> tuple[str | None, dict[str, Any] | None]:
    """``(skip_reason, covered)``: the recomputed record, or why there is none."""
    if _state(job_dir) != "delivered":
        return "not_delivered", None
    try:
        job = job_mod.load(job_dir)
    except Exception:  # noqa: BLE001 - any unreadable job.yaml is one counted skip, never a crash
        return "unreadable_job", None
    if getattr(job, "update", None) is not None:
        # An update's record comes from its own selection (``update/coverage``),
        # which never judged another unit's files.
        return "update_job", None
    units = [u.unit for u in job.units]
    if len(units) < 2:
        return "single_unit", None
    slug_dir = Path(job.data_root) / job.slug
    if not (slug_dir / "raw_manifest.jsonl").is_file():
        return "job_dir_gone", None
    try:
        payload = covered_payload(slug_dir, units, cfg)
    except (OSError, ValueError, KeyError):
        return "no_record_derived", None
    if payload is None:
        return "no_record_derived", None
    return None, payload


def run(jobs_dir: Path, broker: Any, cfg: Any, *, dry_run: bool = False) -> dict[str, Any]:
    """Every job dir under ``jobs_dir`` through the broker verb; the counts."""
    seen = 0
    outcomes: Counter[str] = Counter()
    dirs = sorted(p for p in jobs_dir.iterdir() if p.is_dir()) if jobs_dir.is_dir() else []
    for jd in dirs:
        seen += 1
        skip, payload = decide(jd, cfg)
        if skip is not None:
            outcomes[skip] += 1
            continue
        resp = broker._request(
            {"action": "medchron_covered_recompute", "job_id": jd.name, "covered": payload, "dry_run": dry_run}
        )
        outcomes[str(resp.get("outcome") or "unknown")] += 1
    recomputed = outcomes.pop("recomputed", 0)
    return {
        "dry_run": dry_run,
        "jobs_seen": seen,
        "recomputed": recomputed,
        "skipped": dict(sorted(outcomes.items())),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.covered_backfill", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="decide every job, write nothing")
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("REFUSED: run as root on the seat, in the runner daemon's environment", file=sys.stderr)
        return 2
    from . import daemon as daemon_mod
    from .broker_client import BrokerError

    try:
        d = daemon_mod.build()
        cfg = config_mod.load(d.firm_config)
        counts = run(d.jobs, d.broker, cfg, dry_run=a.dry_run)
    except (BrokerError, OSError, SystemExit, config_mod.ConfigError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
