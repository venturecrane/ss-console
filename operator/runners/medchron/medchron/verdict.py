"""The run's verdict as a FILE the runner writes, not a line on its stdout.

WHY (ss#2906). `_report` parsed the child's stdout with `json.loads` and
treated any failure to parse as no verdict at all -- `failed`, with the reason
"the runner exited N without a verdict". On this seat's ledger that is the
largest single recorded failure class: 4 of the 15 real pipeline attempts
across three matters ended that way (`medchron_jobs`, read 2026-09-24), and
`failed` is terminal, so each one needed a human before the work could move.

Stdout cannot carry a verdict safely, because stdout is not the runner's alone.
Anything the process or a library it imports writes lands in the same stream --
`import fitz` prints a banner, and that alone once turned a handled refusal into
a terminal `failed` (the trap is recorded in the runner's own history). The CLI
already routes its progress to stderr for exactly this reason
(`__main__.py:17-20`); that fixed the lines the driver emits and left every
line it does not control.

A file has no such sharing. The runner writes the same JSON it prints, the
daemon prefers the file, and stdout stays the fallback so a runner that predates
this module still reports normally through an updated daemon.

WHY IT IS CLEARED BEFORE THE CHILD STARTS. A stale verdict is worse than no
verdict: since ss#2903 a failed job is resumed IN PLACE, in the same job dir, so
a second attempt that dies without writing would otherwise read the FIRST
attempt's file and report whatever that said -- `delivered`, possibly, for a run
that shipped nothing. `clear()` runs immediately before the launch, so a file
present afterwards can only have been written by the attempt that just ran.

The write is rename-based. A verdict half-written when the process is killed
must not parse as a shorter, different verdict; a partial temp file is never
renamed, so the daemon sees no file and falls back to stdout, which is the
honest answer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

NAME = "verdict.json"


def clear(job_dir: Path) -> None:
    """Remove any verdict left by an earlier attempt at this job."""
    (Path(job_dir) / NAME).unlink(missing_ok=True)


def write(job_dir: Path, payload: str) -> None:
    """Write this run's verdict JSON, atomically. Never raises: the verdict on
    stdout is still going out, and a runner that cannot write its own job dir
    must not turn a finished run into a crash."""
    path = Path(job_dir) / NAME
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def read(job_dir: Path, out: str) -> list[Any]:
    """The attempt's outcomes: the file if it holds a non-empty list, else
    whatever stdout carried, else `[]` -- which is what `_report` turns into
    "exited without a verdict".

    A file holding `[]` is not preferred over stdout. An empty list is what the
    driver returns when it produced no outcome at all, so it carries no more
    information than an unparsable stream and must not mask a usable one.
    """
    for source in (_from_file(Path(job_dir) / NAME), _from_text(out)):
        if source:
            return source
    return []


def _from_file(path: Path) -> list[Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _from_text(out: str) -> list[Any]:
    try:
        parsed = json.loads(out) if out.strip() else []
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []
