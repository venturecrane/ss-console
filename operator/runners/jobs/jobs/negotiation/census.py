"""`python -m jobs.negotiation.census <workdir> --slug <seat> --design <guid>`: which
open matters have more than one plaintiff, at $0, read-only.

An offer whose document does not say which plaintiff it is for is entered only on
a file with one plaintiff (``rows._tab_for``), so this count is how many of the
firm's files that rule can touch, and it is readable before anything changes. No
model call, no document read, no write: the matter list and each matter's tabs
through the dry run's read-only layout. ``workdir`` is a throwaway directory, as
``jobs negotiate-dry-run`` takes.

The report names matter numbers and counts, no person.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from .. import arrivals
from .dryrun import NegotiationDryRun, write_job


JOB_ID = "01CENSVS000000000000000000"


def census(run: NegotiationDryRun) -> dict[str, Any]:
    out: dict[str, Any] = {"matters": 0, "one_plaintiff": 0, "no_tab": 0, "unreadable": 0, "several": []}
    for m in arrivals.open_matters(run.seat, run.job.matter_statuses):
        out["matters"] += 1
        try:
            tabs = run._tabs(m["id"])
            plaintiffs = run._plaintiffs(m["id"], tabs)
        except Exception as exc:  # noqa: BLE001 - a census names a matter it could not read, never stops on it
            run.log(f"census: a matter's tabs could not be read ({type(exc).__name__})")
            out["unreadable"] += 1
            continue
        if not tabs:
            out["no_tab"] += 1
        if len(plaintiffs) > 1:
            out["several"].append({"number": m["number"], "plaintiffs": len(plaintiffs), "tabs": len(tabs)})
        elif tabs:
            out["one_plaintiff"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.negotiation.census")
    ap.add_argument("workdir", help="a THROWAWAY dir: job.json lands here")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--design", default="", help="the firm's Negotiation Details design guid")
    ap.add_argument("--statuses", default="Open")
    ap.add_argument("--report", default=None, help="where to write the report (default: stdout only)")
    ap.add_argument("--pricing", default=None)
    args = ap.parse_args(argv)
    wd = Path(args.workdir)
    with contextlib.redirect_stdout(sys.stderr):
        write_job(
            wd,
            slug=args.slug,
            seed_saved_before="",
            design=args.design,
            firm_words=[],
            statuses=[x.strip() for x in args.statuses.split(",") if x.strip()],
            cap_usd=1.0,  # nothing here calls a model; the job's schema requires a cap
            job_id=JOB_ID,
        )
        run = NegotiationDryRun(wd, pricing=args.pricing, log=lambda m: print(m, file=sys.stderr))
        report = census(run)
    body = json.dumps(report)
    if args.report:
        Path(args.report).write_text(body, encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["census", "main"]
