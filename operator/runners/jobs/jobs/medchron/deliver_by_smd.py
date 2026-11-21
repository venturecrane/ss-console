"""Record a chronology SMD filed by hand as the job's delivery, and wake DELIVER.

    python -m jobs.medchron.deliver_by_smd <job_id> <record.json> [--update] [--dry-run]

Root only, on the seat, with the jobs venv's python: the same uid and
environment as the runner daemon (`SMD_WORKSPACE_BROKER_SOCKET`,
`CUSTOMER_SLUG`, `SMD_JOBS_RUN_DIR`).

WHY THIS EXISTS (2026-10-14). Two chronology updates on one seat were
built on SMD's side and filed into the matter by hand, while the seat's own
jobs for those requests had been stopped and recorded `failed`. The requester
is still owed the notice a seat delivery gives: from the Operator, in her own
request thread, copying the firm people on that request. Only a job's
completion wake may bind that reply (`reply_binding.py`, the overlay's
`turn_sources`), and only the runner daemon composes and dispatches one. The
ops-card answer is the wrong channel: a new thread, no CC, and SMD's template
voice. So this does exactly what the daemon's `_report` does after a delivered
verdict, with the delivery's facts supplied by the person who filed it:

  1. the ledger row moves `failed`/`held` -> `running` -> `delivered`, carrying
     the counts, the folder, the files and the covered set (the set the NEXT
     update reads its delta from, so it is required);
  2. one `DELIVERY_PROVEN` row records the files as read back from the matter;
  3. the job's daemon state goes terminal (`delivered`) with a pending wake
     composed by the daemon's own `_compose_wake`. The daemon's own loop then
     dispatches it and arms the reply watch, which pages SMD if the reply never
     reaches Sent Items.

What it refuses, each because the alternative is a second send or a paid run:
  * a row already `delivered` (one delivery per job; a re-run of this is a no-op
    refusal, never a second wake);
  * a job dir that already carries a wake (a runner wake was sent or is
    pending), or no job dir / envelope at all (the requester and request email
    live there and nowhere else on the seat);
  * a queue file for the job (the daemon would claim and RUN it);
  * any bound reply already claimed for the job;
  * a record without a folder, files, or a covered set.

The state written is terminal, so the daemon never runs the job: `_in_progress`
skips terminal states and `claim_next` reads only the queue.

`--update` adds the wake's update line (the request was an update INTO the
delivered chronology), which the daemon writes itself only when the envelope
carries an `update` block; these jobs were submitted before that block existed.
`--dry-run` prints the ledger records, the proof row, the state entry and the
wake, and writes nothing.

No dollar figure is written anywhere a person reads: `cents` is the ledger's
cost column (the console row), never text.

The lane-neutral half (the job checks, the proof row, the write order, the CLI
flow) is ``jobs/hand_delivery.py``, shared with the drafting lane's
``jobs/drafting/deliver_by_smd.py``; this file keeps the chronology's record.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import hand_delivery, run_record
from ..hand_delivery import Refused

#: The lane's own wording (medchron/lane.py `_compose_wake`); the DELIVER skill reads
#: this line as "the reply says the chronology was updated".
UPDATE_LINE = "Request: update into the delivered chronology."
REPLY_KIND = "medchron_job"
RECORDABLE_FROM = frozenset({"failed", "held"})


def _int(rec: dict[str, Any], key: str) -> int:
    v = rec.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise Refused(f"record.{key} must be a non-negative integer")
    return v


def parse_record(rec: Any) -> dict[str, Any]:
    """The delivery's facts, checked: what `_report` would have been handed."""
    if not isinstance(rec, dict):
        raise Refused("the record must be a JSON object")
    folder_id = str(rec.get("folder_id") or "").strip()
    if not folder_id:
        raise Refused("record.folder_id is required: the delivered folder")
    files = rec.get("files")
    if not isinstance(files, list) or not files:
        raise Refused("record.files must list the delivered files as read back from the matter")
    clean = [{**hand_delivery.checked_file(f), "confirmed": True} for f in files]
    covered = rec.get("covered")
    if not isinstance(covered, dict) or not isinstance(covered.get("covered"), list):
        raise Refused("record.covered is required ({covered, uncovered, pulled}): the next update reads it")
    # THIS delivery's sets only, never the matter's union: the ledger unions
    # every delivered row on the matter at read time (medchron_coverage
    # .union_coverage), and `uncovered` wins that union, so an earlier
    # delivery's documents listed here as uncovered would be read again.
    delivery: dict[str, Any] = {"files": clean}
    if isinstance(rec.get("replaced"), dict):
        delivery["replaced"] = dict(rec["replaced"])
    exhibits = rec.get("exhibits")
    if exhibits is not None:
        if not isinstance(exhibits, list) or not all(isinstance(x, str) and x.strip() for x in exhibits):
            raise Refused("record.exhibits must list the exhibit file ids filed beside the chronology")
        delivery["exhibits"] = [x.strip() for x in exhibits]
    return {
        "documents": _int(rec, "documents"),
        "pages": _int(rec, "pages"),
        "cents": _int(rec, "cents"),
        "folder_id": folder_id,
        "covered": covered,
        "delivery": delivery,
    }


def plan(d: Any, job_id: str, rec: dict[str, Any], *, update: bool) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    fields = parse_record(rec)
    row, _st, _env = hand_delivery.check_job(
        d,
        job_id,
        noun="chronology",
        recordable=RECORDABLE_FROM,
        resumable_daemon=RECORDABLE_FROM,
        refused_why="only a failed or held job is recorded as delivered by SMD",
        requester_keys=("requested_by", "request_ref"),
        reply_kind=REPLY_KIND,
    )
    task = d._compose_wake(job_id, "delivered", fields)
    if update and UPDATE_LINE not in task:
        lines = task.split("\n")
        # After the counts, where the daemon puts it for an update envelope.
        lines.insert(3, UPDATE_LINE)
        task = "\n".join(lines)
    return {
        "job_id": job_id,
        "from_state": row.get("state"),
        # The delivered record says SMD delivered it (run_record.py): a run
        # SMD finished by hand is never counted as one that needed nobody.
        "records": [
            ("running", {}),
            ("delivered", {**fields, "run": run_record.block("hand_delivery", by="smd", with_release=True)}),
        ],
        "proof": hand_delivery.proof_request(d, job_id, fields["delivery"]["files"], review="passed", attempt=1),
        "state": {
            "state": "delivered",
            "delivered_by": "smd",
            "reason": None,
            "wake": {"pending": True, "attempts": 0, "task": task},
        },
    }


apply = hand_delivery.apply


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.medchron.deliver_by_smd", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("record", help="JSON: documents, pages, cents, folder_id, files, covered[, replaced, exhibits]")
    ap.add_argument("--update", action="store_true", help="the delivery is an update into the delivered chronology")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    a = ap.parse_args(argv)
    from . import lane as lane_mod

    d = lane_mod.build()
    return hand_delivery.run_cli(
        d,
        lambda lane: plan(lane, a.job_id, hand_delivery.read_record(a.record), update=a.update),
        dry_run=a.dry_run,
        done=f"recorded {a.job_id} delivered; the daemon dispatches its DELIVER wake on its next tick",
    )


__all__ = ["REPLY_KIND", "UPDATE_LINE", "Refused", "apply", "main", "parse_record", "plan"]


if __name__ == "__main__":
    sys.exit(main())
