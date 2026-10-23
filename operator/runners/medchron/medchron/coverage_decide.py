"""Record SMD's account of a file the coverage gate held as unexplained, and resume.

The coverage gate holds when a unit file is neither cited nor explained
(``stages/coverage.py``). Most explanations are written by the run itself:
the firm's exclusion classes, the billing set, and the composer's own
"nothing extractable: <reason>" line for each file it read. When the composer
read a file and wrote no line for it, nothing in the run can supply the
reason, and before 2026-10-22 nothing outside it could either.

WHY (2026-10-22). The Curtis chronology (matter 200530) held at the gate on
one emailed image; its seven siblings of the same name pattern were each
accounted for by the composer as an email signature or logo image, and this
one had no line at all. A person reads the file in the ops workspace, decides,
and SMD records the decision here, with who decided and on what evidence.

This is SMD's act, as root on the seat, with the medchron venv's python::

    python -m medchron.coverage_decide <job_id> --explain <id> --reason <text> \\
        --by <who decided> --source <where it was read> [--dry-run]

The reason is written to ``coverage_decisions.json`` in the matter directory
and read by the gate beside the composer's dispositions, printed in full like
every other reason. The gate re-runs on the resume (it held, so it is not
done); nothing paid re-runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .resume import MARKER_PREFIX

FILE = "coverage_decisions.json"


class Refused(RuntimeError):
    """The decision cannot be recorded; the message says why."""


def reasons(slug_dir: Path, unit: str) -> dict[str, str]:
    """``{file_key: reason}`` SMD recorded for this unit."""
    p = slug_dir / FILE
    if not p.is_file():
        return {}
    body = json.loads(p.read_text(encoding="utf-8"))
    return {
        str(r["name"]): "SMD: " + str(r["reason"])
        for r in body.get("decisions") or []
        if r.get("unit") == unit and r.get("name") and r.get("reason")
    }


def _record(slug_dir: Path, row: dict[str, Any]) -> None:
    p = slug_dir / FILE
    body = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"decisions": []}
    body["decisions"] = [
        r for r in body.get("decisions") or [] if not (r.get("unit") == row["unit"] and r.get("id") == row["id"])
    ] + [row]
    p.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")


def plan(d: Any, job_id: str, *, file_id: str, reason: str, by: str, source: str) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    if not reason.strip() or not by.strip() or not source.strip():
        raise Refused("--reason, --by (who decided) and --source (where it was read) are all required")
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no chronology job {job_id}")
    if not str(row.get("reason") or "").startswith("coverage gate"):
        raise Refused(f"job {job_id} is {row.get('state')} ({str(row.get('reason') or '')[:80]}), not a coverage hold")
    if d._daemon_state(job_id).get("state") != "held":
        raise Refused(f"the daemon does not hold job {job_id}; it may be running already. Nothing was written.")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was written")
    from . import job as job_mod
    from .stages.coverage import file_key

    job = job_mod.load(d.job_dir(job_id))
    slug_dir = job.data_root / job.slug
    for u in job.units:
        unexplained = slug_dir / "runs" / u.unit / "coverage_unexplained.json"
        held = set(json.loads(unexplained.read_text(encoding="utf-8"))) if unexplained.is_file() else set()
        units_file = slug_dir / "units" / f"{u.unit}.json"
        rows = json.loads(units_file.read_text(encoding="utf-8")) if units_file.is_file() else []
        for f in rows:
            if str(f.get("id")) == file_id and file_key(f) in held:
                return {
                    "job_id": job_id,
                    "slug_dir": str(slug_dir),
                    "decision": {
                        "unit": u.unit,
                        "id": file_id,
                        "name": file_key(f),
                        "reason": reason.strip(),
                        "by": by.strip(),
                        "source": source.strip(),
                    },
                    "marker": {
                        "job_id": job_id,
                        "reason": f"coverage decision recorded by SMD: {file_id} ({by.strip()}, {source.strip()})"[
                            :500
                        ],
                        "redo": [],
                    },
                }
    raise Refused(f"{file_id} is not a file the coverage gate held as unexplained on job {job_id}")


def apply(d: Any, p: dict[str, Any]) -> None:
    slug_dir = Path(p["slug_dir"])
    _record(slug_dir, {**p["decision"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    d._chown_child(slug_dir / FILE)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(
        json.dumps(p["marker"], sort_keys=True), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.coverage_decide", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--explain", required=True, metavar="ID", help="the held file's id")
    ap.add_argument("--reason", required=True, help="why it is not cited, as the gate will print it")
    ap.add_argument("--by", required=True, help="who decided (an email address)")
    ap.add_argument("--source", required=True, help="where the file was read (a session, a vfy_ id)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    a = ap.parse_args(argv)
    from . import daemon as daemon_mod

    d = daemon_mod.build()
    try:
        p = plan(d, a.job_id, file_id=a.explain, reason=a.reason, by=a.by, source=a.source)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"dry_run": a.dry_run, "decision": p["decision"]}, indent=1))
    if a.dry_run:
        return 0
    apply(d, p)
    print(f"recorded the decision on {a.job_id}; the daemon re-queues it on its next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
