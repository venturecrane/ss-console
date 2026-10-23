"""Retire an audit control a person found GENUINE, and resume the job.

Every eighth claim is also checked against page 1 of another institution's
exhibit, and that control must come back unsupported; one SUPPORTED control
makes the whole audit INVALID (``audit/run.py``), because a verifier that
says yes to a mismatched page makes every verdict worthless.

A control is only a control when its page cannot carry the claim. WHY
(2026-10-22): the Curtis chronology (matter 200530) checked 612 controls and
two came back SUPPORTED. Both were run against Exhibit 1 page 1, which is the
diagnosis index of a records production: a code list covering the patient's
whole history, so it really does state every diagnosis-level claim. A person
read both in the ops workspace and found them genuine; the verifier was right
and the control was badly chosen. Nothing could record that.

This is SMD's act, as root on the seat, with the medchron venv's python::

    python -m medchron.audit_decide <job_id> --retire KEY [KEY ...] --reason <text> \\
        --by <who decided> --source <where it was read> [--dry-run]

Only a SUPPORTED control row on a job held or failed at the audit for that
reason can be retired. The decision is written beside the audit results and
both the round's report and the final verdict gate leave those rows out, with
the count logged. Every audit row is kept, so the resume re-bills nothing it
already checked.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .resume import MARKER_PREFIX


class Refused(RuntimeError):
    """The decision cannot be recorded; the message says why."""


def _supported_controls(results: Path) -> set[str]:
    out: set[str] = set()
    if not results.is_file():
        return out
    for line in results.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if str(r.get("kind", "")).startswith("control") and r.get("verdict") == "SUPPORTED":
            out.add(str(r.get("key")))
    return out


def plan(d: Any, job_id: str, *, keys: list[str], reason: str, by: str, source: str) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    if not keys or not reason.strip() or not by.strip() or not source.strip():
        raise Refused("--retire, --reason, --by (who decided) and --source (where it was read) are all required")
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no chronology job {job_id}")
    if not str(row.get("reason") or "").startswith("audit INVALID"):
        raise Refused(f"job {job_id} is {row.get('state')} ({str(row.get('reason') or '')[:80]}), not an invalid audit")
    if d._daemon_state(job_id).get("state") not in ("held", "failed"):
        raise Refused(f"the daemon is not holding job {job_id}; it may be running already. Nothing was written.")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was written")
    from . import job as job_mod
    from .audit.run import AuditPaths

    job = job_mod.load(d.job_dir(job_id))
    slug_dir = job.data_root / job.slug
    for u in job.units:
        paths = AuditPaths(slug_dir, u.unit)
        supported = _supported_controls(paths.results)
        unknown = [k for k in keys if k not in supported]
        if not unknown:
            return {
                "job_id": job_id,
                "file": str(paths.retired),
                "rows": [
                    {"key": k, "reason": reason.strip(), "by": by.strip(), "source": source.strip()} for k in keys
                ],
                "marker": {
                    "job_id": job_id,
                    "reason": f"audit controls retired by SMD: {', '.join(keys)} ({by.strip()}, {source.strip()})"[
                        :500
                    ],
                    "redo": [],
                },
            }
    raise Refused(f"not SUPPORTED control rows on job {job_id}: {keys}")


def apply(d: Any, p: dict[str, Any]) -> None:
    f = Path(p["file"])
    body = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"retired": []}
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    new = {r["key"] for r in p["rows"]}
    body["retired"] = [r for r in body.get("retired") or [] if r.get("key") not in new] + [
        {**r, "at": at} for r in p["rows"]
    ]
    f.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
    d._chown_child(f)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(
        json.dumps(p["marker"], sort_keys=True), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.audit_decide", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--retire", nargs="+", required=True, metavar="KEY", help="SUPPORTED control row keys")
    ap.add_argument("--reason", required=True, help="why the control page carries the claim")
    ap.add_argument("--by", required=True, help="who decided (an email address)")
    ap.add_argument("--source", required=True, help="where the rows were read (a vfy_ id)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    a = ap.parse_args(argv)
    from . import daemon as daemon_mod

    d = daemon_mod.build()
    try:
        p = plan(d, a.job_id, keys=a.retire, reason=a.reason, by=a.by, source=a.source)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"dry_run": a.dry_run, "retire": [r["key"] for r in p["rows"]]}, indent=1))
    if a.dry_run:
        return 0
    apply(d, p)
    print(f"retired {len(p['rows'])} control(s) on {a.job_id}; the daemon re-queues it on its next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
