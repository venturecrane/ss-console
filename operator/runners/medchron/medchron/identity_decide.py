"""Record the firm's answer to an identity hold, and resume the job.

A chronology holds when a folded email attachment names someone other than the
client (``gates/foreign_identity``), and the requester is asked whose it is.
Until 2026-10-22 the answer had nowhere to go: the gate read only a release
file nobody wrote, ``medchron_job_resume`` refused every hold but the audit
gate's, and "leave it out" did not exist. An attorney answered a hold on a matter
with "Just leave it out of the chronology", the Operator could only tell him it
had no way to act, and he forwarded the exchange to SMD asking for help.

This is SMD's act, as root on the seat, with the medchron venv's python::

    python -m medchron.identity_decide <job_id> --withhold <id> [<id> ...] \\
        --by <who decided> --source <where they said it> [--dry-run]
    python -m medchron.identity_decide <job_id> --release <id> [...] --by ... --source ...

``--withhold`` (the firm said to leave it out): the document joins no unit, is
read by no stage, never holds the run again, and is disclosed as "left out at
the firm's direction"; the coverage record counts it accounted for, so an update
never asks again. ``--release`` (a person read it and it is the client's): it is
folded in as any other record. Ids are the hold's own (``msgatt-<sha12>`` or
the sha12, or a pulled document's id) and must be on the job; who decided and
where they said it are recorded beside the decision.

The resume goes through the daemon's own marker (``resume.take_requests``), the
same path ``medchron_job_resume`` writes, so the ledger row moves to ``running``
only when the daemon actually starts the job. A withhold re-runs ``build_units``
(free) so the document leaves the units already built; everything paid for
(the pull, vision, billing) is kept.

There is no agent tool for this, deliberately: no email can talk a seat past a
hold (docs/handbook/incident-response.md, 2026-10-13). The Operator tells the
requester SMD is finishing it, and SMD records the answer here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .gates import foreign_identity as FI
from .resume import MARKER_PREFIX


class Refused(RuntimeError):
    """This decision cannot be recorded; the message says why."""


def _identity_hold(row: dict[str, Any]) -> bool:
    """Held on another person's record, by the classifier the firm's wording
    and SMD's page already come from (``proof/holds.py``), never a second
    reading of the reason that could drift from it."""
    from .proof.holds import ANOTHER_PERSON, classify

    return row.get("state") == "held" and classify("medchron", row.get("reason"))[0] == ANOTHER_PERSON


def _known_ids(slug_dir: Any) -> set[str]:
    """Every document id the job pulled, folded attachments included."""
    out: set[str] = set()
    p = slug_dir / "raw_manifest.jsonl"
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rid = str((json.loads(line) or {}).get("id") or "")
        except ValueError:
            continue
        if rid:
            out.add(rid)
    return out


def plan(d: Any, job_id: str, *, action: str, ids: list[str], by: str, source: str) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    if action not in ("release", "withhold"):
        raise Refused("name one decision: --withhold or --release")
    ids = [i.strip() for i in ids if i and i.strip()]
    if not ids:
        raise Refused("name the held documents the decision is about")
    if not by.strip() or not source.strip():
        raise Refused("--by (who decided) and --source (where they said it) are both required")
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no chronology job {job_id}")
    if not _identity_hold(row):
        raise Refused(f"job {job_id} is {row.get('state')} ({str(row.get('reason') or '')[:80]}), not an identity hold")
    if d._daemon_state(job_id).get("state") != "held":
        raise Refused(f"the daemon does not hold job {job_id}; it may be running already. Nothing was written.")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was written")
    from . import job as job_mod

    job = job_mod.load(d.job_dir(job_id))
    slug_dir = job.data_root / job.slug
    known = _known_ids(slug_dir)
    unknown = [i for i in ids if not any(FI.matches(k, {i}) for k in known)]
    if unknown:
        raise Refused(f"not documents on job {job_id}: {unknown}; use the ids the hold named")
    return {
        "job_id": job_id,
        "slug_dir": str(slug_dir),
        "decision": {"action": action, "ids": ids, "by": by.strip(), "source": source.strip()},
        "marker": {
            "job_id": job_id,
            "reason": f"identity decision recorded by SMD: {action} {', '.join(ids)} ({by.strip()}, {source.strip()})"[
                :500
            ],
            # A withheld document is already in the units build_units wrote;
            # that stage is free and re-runs so it leaves them. A release
            # changes nothing upstream of the identity stage, which never
            # finished and re-runs on its own.
            "redo": ["build_units"] if action == "withhold" else [],
        },
    }


def apply(d: Any, p: dict[str, Any]) -> None:
    from pathlib import Path

    slug_dir = Path(p["slug_dir"])
    dec = p["decision"]
    FI.record_decision(
        slug_dir,
        action=dec["action"],
        ids=dec["ids"],
        by=dec["by"],
        source=dec["source"],
        at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    d._chown_child(slug_dir / FI.RELEASE_FILE)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(
        json.dumps(p["marker"], sort_keys=True), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.identity_decide", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--withhold", nargs="+", metavar="ID", help="the firm said to leave these out")
    which.add_argument("--release", nargs="+", metavar="ID", help="a person read these and they are the client's")
    ap.add_argument("--by", required=True, help="who decided (an email address)")
    ap.add_argument("--source", required=True, help="where they said it (a message id, a vfy_ id)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    a = ap.parse_args(argv)
    from . import daemon as daemon_mod

    d = daemon_mod.build()
    action, ids = ("withhold", a.withhold) if a.withhold else ("release", a.release)
    try:
        p = plan(d, a.job_id, action=action, ids=ids, by=a.by, source=a.source)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"dry_run": a.dry_run, **p}, indent=1, default=str))
    if a.dry_run:
        return 0
    apply(d, p)
    print(f"recorded the {action} on {a.job_id}; the daemon re-queues it on its next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
