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


#: A real ending: recorded even when the Machine's stop lands as it is
#: written, never run again (``resume.cut_short_by_stop``, ``finished``).
FINISHED_OUTCOMES = frozenset({"delivered", "dry_run", "held", "refused"})


def one(job_dir: Path, out: str, verdicts: tuple[str, ...]) -> dict[str, Any] | None:
    """A runner that writes ONE verdict object (case file, negotiation,
    litigation, records): the file first, then stdout's last line; the first
    that parses as an object whose ``verdict`` is one of ``verdicts``."""
    texts = []
    try:
        texts.append((Path(job_dir) / NAME).read_text(encoding="utf-8"))
    except OSError:
        pass
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    if lines:
        texts.append(lines[-1])
    for t in texts:
        try:
            v = json.loads(t)
        except ValueError:
            continue
        if isinstance(v, dict) and v.get("verdict") in verdicts:
            return v
    return None


def every(job_dir: Path) -> list[Any]:
    """Every verdict a run wrote, in either shape a lane's runner emits.

    WHY (2026-10-13, the A&P litigation proof). The chronology, demand and
    drafting runners write a JSON LIST to ``verdict.json``; the litigation and
    negotiation runners write ONE JSON OBJECT (``__main__._cmd_litigate`` and
    ``_cmd_negotiate``, read back by ``one``). ``read`` keeps only a list, so a
    litigation proof run that wrote ``{"verdict": "dry_run", ...}`` read back as
    ``[]`` and its proof was recorded ``failed``, "the runner wrote no dry_run
    verdict", stages [] and 0 cents. A single object is one verdict, judged
    exactly as a one-item list (``proof_run.judge``)."""
    listed = read(job_dir, "")
    if listed:
        return listed
    try:
        parsed = json.loads((Path(job_dir) / NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [parsed] if isinstance(parsed, dict) else []


def finished(v: Any) -> bool:
    """For a runner that writes ONE verdict object (``one``): True when it is a
    real ending that must be recorded even mid-stop, never re-run.
    ``resume.cut_short_by_stop`` reads list-shaped outcomes and cannot see this
    shape, so these lanes ask here first. A ``failed`` that carries a paged
    reason (``review_held:``, ``readback_mismatch:``, ``update_held:``) is the
    work's own ending and pages SMD; any other ``failed`` written as the stop
    landed is the stop."""
    from .medchron.unit_proof import PAGED_PREFIXES

    if not isinstance(v, dict):
        return False
    if v.get("verdict") in FINISHED_OUTCOMES:
        return True
    return v.get("verdict") == "failed" and str(v.get("reason") or "").startswith(PAGED_PREFIXES)


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


#: What the runner's own exit code says it decided (``__main__._exit_code``).
_DECIDED = {0: "delivered", 1: "failed", 2: "crashed outside any stage", 3: "held", 4: "refused"}


def lost(code: int) -> str:
    """The reason recorded when an attempt returns no verdict. A negative code
    is a signal, and requeue.KILLED retries it. A positive code is a runner that
    finished and decided; only its report was lost, to a defect (09-16: exit 4,
    a refusal whose stdout a library banner spoiled before verdict.json
    existed). None of those is retried: a retry would re-run a refusal or a
    failure the runner already decided, so the reason names what it decided."""
    head = f"the runner exited {code} without a verdict"
    if code < 0:
        return head
    what = _DECIDED.get(code, "ended with an unknown code")
    return f"{head}: it {what}, and its verdict was lost (a defect: verdict.json unwritten, stdout not JSON)"
