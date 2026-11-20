"""SMD keeps a sentence the update's entry read disputes, and resumes the job.

An update holds when two readers find a line wrong and no correction applied
(``stages/entry_audit_update``). When the readers are RIGHT, SMD corrects or
deletes the line in ``runs/<unit>/final-chronology.md`` and resumes
(``stages/entry_audit_held``). When they are WRONG, this is SMD's one act:
each named line is marked settled by SMD, by the sha256 of its text, in
``runs/<unit>/entry_read/smd-settled.json`` with who, where and when. A line
so marked is never held again and never edited by the reader on any later
read; it is still read, and its finding still recorded.

As root on the seat, with the jobs venv's python::

    python -m jobs.medchron.update_settle <job_id> --list
    python -m jobs.medchron.update_settle <job_id> --line <sha> [<sha> ...] \\
        --by <who decided> --source <the vfy_ id of the pages read> [--dry-run]

``--list`` prints each held line's sha, class and text (on the seat only).
The resume goes through the daemon's own marker, as ``identity_decide``'s.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from ..matter.store import write_atomic
from ..resume import MARKER_PREFIX

SETTLED = "smd-settled.json"
HOLD_PREFIX = "audit coverage:"


class Refused(RuntimeError):
    """This decision cannot be recorded; the message says why."""


def line_sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def path(slug_dir: Path, unit: str) -> Path:
    from ..stages import entry_audit_io as IO

    return slug_dir / "runs" / unit / IO.DIR / SETTLED


def settled(slug_dir: Path, unit: str) -> set[str]:
    """The line shas SMD settled on this unit."""
    p = path(slug_dir, unit)
    try:
        got = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        return set()
    lines = got.get("lines") if isinstance(got, dict) else None
    return set(lines) if isinstance(lines, dict) else set()


def held_lines(slug_dir: Path, unit: str) -> list[dict[str, str]]:
    """[{sha, class, line}] of the unit's held lines (``entry_audit_held``)."""
    from ..stages import entry_audit_held as HELD

    p = slug_dir / "runs" / unit / "entry_read" / HELD.HELD
    try:
        rec = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        return []
    out = []
    for row in rec.get("held") or [] if isinstance(rec, dict) else []:
        if isinstance(row, dict) and isinstance(row.get("line"), str):
            out.append({"sha": line_sha(row["line"]), "class": str(row.get("class") or ""), "line": row["line"]})
    return out


def plan(d: Any, job_id: str, *, shas: list[str], by: str, source: str) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    shas = [s.strip() for s in shas if s and s.strip()]
    if not shas:
        raise Refused("name the held lines to settle (--line <sha>; --list prints them)")
    if not by.strip() or not source.strip():
        raise Refused("--by (who decided) and --source (where it was read) are both required")
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no chronology job {job_id}")
    if row.get("state") != "held" or not str(row.get("reason") or "").startswith(HOLD_PREFIX):
        raise Refused(f"job {job_id} is {row.get('state')} ({str(row.get('reason') or '')[:80]}), not an audit hold")
    if d._daemon_state(job_id).get("state") != "held":
        raise Refused(f"the daemon does not hold job {job_id}; it may be running already. Nothing was written.")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was written")
    from . import job as job_mod

    job = job_mod.load(d.job_dir(job_id))
    slug_dir = job.data_root / job.slug
    by_unit = {u.unit: {r["sha"] for r in held_lines(slug_dir, u.unit)} for u in job.units}
    unknown = [s for s in shas if not any(s in held for held in by_unit.values())]
    if unknown:
        raise Refused(f"not lines job {job_id} holds on: {unknown}; --list prints them")
    units = {u: sorted(set(shas) & held) for u, held in by_unit.items() if set(shas) & held}
    return {
        "job_id": job_id,
        "slug_dir": str(slug_dir),
        "units": units,
        "decision": {"by": by.strip(), "source": source.strip()},
        "marker": {
            "job_id": job_id,
            "intervention": "decide_update",
            "reason": f"held line(s) settled by SMD: {len(shas)} ({by.strip()}, {source.strip()})"[:500],
            "redo": [],
        },
    }


def apply(d: Any, p: dict[str, Any], at: str | None = None) -> None:
    slug_dir = Path(p["slug_dir"])
    when = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for unit, shas in p["units"].items():
        f = path(slug_dir, unit)
        got = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"lines": {}}
        for s in shas:
            got["lines"][s] = {**p["decision"], "at": when}
        f.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(f, json.dumps(got, indent=1))
        d._chown_child(f)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(
        json.dumps(p["marker"], sort_keys=True), encoding="utf-8"
    )


def _list(d: Any, job_id: str) -> int:
    from . import job as job_mod

    job = job_mod.load(d.job_dir(job_id))
    for u in job.units:
        for r in held_lines(job.data_root / job.slug, u.unit):
            print(f"{u.unit}\t{r['sha']}\t{r['class']}\t{r['line']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.medchron.update_settle", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--list", action="store_true", help="print each held line's sha, class and text")
    which.add_argument("--line", nargs="+", metavar="SHA", help="the held lines SMD keeps as written")
    ap.add_argument("--by", default="", help="who decided (an email address)")
    ap.add_argument("--source", default="", help="where the pages were read (a vfy_ id)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    a = ap.parse_args(argv)
    from . import lane as lane_mod

    d = lane_mod.build()
    if a.list:
        return _list(d, a.job_id)
    try:
        p = plan(d, a.job_id, shas=a.line, by=a.by, source=a.source)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"dry_run": a.dry_run, **{k: v for k, v in p.items() if k != "slug_dir"}}, indent=1))
    if a.dry_run:
        return 0
    apply(d, p)
    print(f"settled {sum(len(v) for v in p['units'].values())} line(s) on {a.job_id}; the daemon re-queues it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
