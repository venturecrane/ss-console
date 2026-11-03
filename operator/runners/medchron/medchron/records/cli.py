"""`medchron records <job_dir>`: one matter records job. The verdict is ONE
JSON object: the only line on stdout, and the same text in ``verdict.json``.
Everything else the run (or a library it imports) writes goes to stderr. Exit
0 delivered (or a proof run's dry_run), 1 held, 2 failed; a Machine stop exits
``stop_request.STOP_EXIT`` for the next boot.

`medchron records --rehearse <dir>` runs the capped rehearsal instead
(``rehearse.py``): exit 0 when the reply matches ``expected.json`` and no page
was read, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Any

from .. import stop_request, verdict as verdict_mod


def _log(m: str) -> None:
    print(m, file=sys.stderr)


def cmd(args: argparse.Namespace) -> int:
    if args.rehearse:
        from .rehearse import rehearse

        with contextlib.redirect_stdout(sys.stderr):
            report, code = rehearse(Path(args.rehearse), pricing=args.pricing, cap_usd=args.cap, log=_log)
        print(report)
        return code
    if not args.job_dir:
        print("medchron records: a job dir is required (or --rehearse DIR)", file=sys.stderr)
        return 2
    from . import firm as firm_mod, job as job_mod
    from .outcome import Verdict

    stop_request.arm(args.job_dir)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            try:
                from .run import RecordsRun

                r = RecordsRun(
                    Path(args.job_dir), inputs_dir=args.inputs, pricing=args.pricing, state_dir=args.state_dir, log=_log
                )
            except job_mod.RecordsJobError as exc:
                v = Verdict("failed", stage="envelope", reason=f"envelope_invalid: {exc}")
            except firm_mod.RecordsConfigError as exc:
                v = Verdict("failed", stage="config", reason=f"config_missing: {str(exc)[:300]}")
            except Exception as exc:  # noqa: BLE001 - a verdict, never a trace on stdout
                v = Verdict("failed", stage="setup", reason=f"unexpected: {type(exc).__name__}: {str(exc)[:300]}")
            else:
                if args.measure:
                    report = r.measure()
                    print(report, file=sys.__stdout__)
                    return 0
                r.reopen([x.strip() for x in (args.redo or "").split(",") if x.strip()])
                v = r.run()
    except stop_request.StopRequested as stop:
        print(f"medchron: {stop}; exiting {stop_request.STOP_EXIT} for the next boot to resume", file=sys.stderr)
        return stop_request.STOP_EXIT
    payload = v.to_json()
    verdict_mod.write(Path(args.job_dir), payload)
    print(payload)
    return v.exit_code


def add_parser(sub: Any) -> None:
    rc = sub.add_parser(
        "records", help="run one matter records job: find, cut and describe the records a colleague asked for"
    )
    rc.add_argument("job_dir", nargs="?", default=None)
    rc.add_argument("--inputs", default=None, help="the records inputs dir (default: MEDCHRON_RECORDS_INPUTS_DIR)")
    rc.add_argument("--pricing", default=None)
    rc.add_argument("--redo", default="", help="comma-separated stages to reopen (a resume request)")
    rc.add_argument("--state-dir", default=None, help="the lane's persistent state dir (default: on the volume)")
    rc.add_argument(
        "--rehearse",
        default=None,
        metavar="DIR",
        help="the capped rehearsal on copies of a seat's page indexes: no page is read; the match may be asked",
    )
    rc.add_argument(
        "--measure",
        action="store_true",
        help="$0: print each record's ranked candidate files and what the pre-flight would price; run nothing",
    )
    rc.add_argument("--cap", type=float, default=2.0, help="the rehearsal's dollar cap (default 2.00)")
    rc.set_defaults(fn=cmd)


__all__ = ["add_parser", "cmd"]
