"""`medchron casefile <job_dir>`: one case file job (a ``full`` read or an
``apply`` of approved lines). The verdict is ONE JSON object: the only line on
stdout, and the same text in ``verdict.json``. Everything else the run (or a
library it imports) writes to stdout goes to stderr. Exit 0 delivered, 1 held,
2 failed; a Machine stop exits ``stop_request.STOP_EXIT`` for the next boot.

``--rehearse REPORT_JSON`` runs the $0 rehearsal (``rehearse.py``) instead.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from .. import stop_request, verdict as verdict_mod


def _mode(job_dir: Path) -> str:
    try:
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "full"
    return str(data.get("mode") or "full") if isinstance(data, dict) else "full"


def _build(args: argparse.Namespace) -> Any:
    jd = Path(args.job_dir)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731 - one-line sink, the litigation CLI's shape
    if args.rehearse:
        from .rehearse import RehearsalRun

        return RehearsalRun(
            jd,
            report_path=Path(args.rehearse),
            inputs_dir=args.inputs,
            pricing=args.pricing,
            state_dir=args.state_dir,
            log=log,
        )
    if _mode(jd) == "apply":
        from .apply import ApplyRun

        return ApplyRun(jd, state_dir=args.state_dir, log=log)
    from .run import CasefileRun

    return CasefileRun(jd, inputs_dir=args.inputs, pricing=args.pricing, state_dir=args.state_dir, log=log)


def cmd(args: argparse.Namespace) -> int:
    from . import firm as firm_mod, job as job_mod
    from .outcome import Verdict

    stop_request.arm(args.job_dir)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            try:
                r = _build(args)
            except job_mod.CasefileJobError as exc:
                v = Verdict("failed", stage="envelope", reason=f"envelope_invalid: {exc}")
            except firm_mod.CasefileConfigError as exc:
                v = Verdict("failed", stage="config", reason=f"config_missing: {str(exc)[:300]}")
            except Exception as exc:  # noqa: BLE001 - a verdict, never a trace on stdout
                v = Verdict("failed", stage="setup", reason=f"unexpected: {type(exc).__name__}: {str(exc)[:300]}")
            else:
                if hasattr(r, "reopen"):
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
    cf = sub.add_parser("casefile", help="run one case file job: a caseload's File Status and ranked list, or an apply")
    cf.add_argument("job_dir")
    cf.add_argument("--inputs", default=None, help="the case file inputs dir (default: MEDCHRON_CASEFILE_INPUTS_DIR)")
    cf.add_argument("--pricing", default=None)
    cf.add_argument("--redo", default="", help="comma-separated stages to reopen (a resume request)")
    cf.add_argument("--state-dir", default=None, help="the lane's persistent state dir (default: on the volume)")
    cf.add_argument(
        "--rehearse", default=None, metavar="REPORT_JSON", help="$0: measure the packs, read nothing, file nothing"
    )
    cf.set_defaults(fn=cmd)


__all__ = ["add_parser", "cmd"]
