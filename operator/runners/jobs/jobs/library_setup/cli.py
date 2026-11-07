"""``jobs library-setup <job_dir>``: one "set up our ..." job (``job.py``).

The verdict is a one-element JSON list (the shape ``verdict.read`` reads): the
only line on stdout, and the same text in ``verdict.json``. Progress goes to stderr. Exit 0 for every recorded outcome
(observed, delivered, held): the lane reads the verdict, never the exit code,
except that no verdict at all is a failure.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .. import verdict as verdict_mod

INPUTS_ENV = "JOBS_DRAFTING_INPUTS"


def cmd(args: argparse.Namespace) -> int:
    from . import job as job_mod

    jd = Path(args.job_dir)
    with contextlib.redirect_stdout(sys.stderr):
        out = job_mod.run_dir(jd, inputs_dir=args.inputs or os.environ.get(INPUTS_ENV) or None)
    text = json.dumps([out], sort_keys=True)
    verdict_mod.write(jd, text)
    print(text)
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("library-setup", help="find the firm's own files to set up one kind from; reads only")
    p.add_argument("job_dir")
    p.add_argument("--inputs", default=None, help="the drafting firm-inputs dir (default: JOBS_DRAFTING_INPUTS)")
    p.set_defaults(fn=cmd)


__all__ = ["add_parser", "cmd"]
