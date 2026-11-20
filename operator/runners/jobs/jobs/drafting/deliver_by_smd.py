"""Record a document SMD drafted and filed by hand as the drafting job's delivery.

    python -m jobs.drafting.deliver_by_smd <job_id> <record.json> [--quiet] --dry-run
    python -m jobs.drafting.deliver_by_smd <job_id> <record.json> [--quiet] --confirm <digest>

Root only, on the seat, with the jobs venv's python: the same uid and
environment as the runner daemon (`SMD_WORKSPACE_BROKER_SOCKET`,
`CUSTOMER_SLUG`, `SMD_JOBS_RUN_DIR`, `SMD_DRAFTING_QUEUE_DIR`).

WHY THIS EXISTS (2026-11-19). Twice in one evening an attorney asked for a
change to a document SMD had drafted and filed by hand for a request the
Operator could not finish. The seat's own job for that request had ended held,
or had been stopped by SMD, so the job store held no DELIVERED draft of that
class on the matter. The broker's revise routing (`drafting_verbs._route`) reads
exactly that, so the change request queued a fresh full draft, and the reply
told the attorney how the earlier draft was made. After this tool the job store
says what happened: the draft was delivered, as these files, and the next
request in that thread revises them in place.

What it writes, exactly what the lane's own delivery writes (`drafting_lane`
`_report` and `proof/watch.py`):

  1. the ledger row moves to `delivered` with the folder and the files (name,
     size, role): from `failed` (a failed job, or one SMD stopped) through
     `running`; from `held` through `failed` and `running`, because the
     drafting ledger lets a held row move only to failed;
  2. one `DELIVERY_PROVEN` row with each file's id, size and sha256, in the same
     order, which is what `DraftingLedger.proven_files` pairs them by;
  3. the job's daemon state goes terminal (`delivered`), with the lane's own
     DELIVER wake pending (the daemon dispatches it and arms the reply watch),
     or with `--quiet` no wake, when SMD already answered the attorney.

It verifies nothing against Smokeball: the filing step already read each file
back by id, and the record carries what that read-back said.

What it refuses (nothing is written): a job still running or queued, a job
already delivered or with a wake still to go or a delivered reply already
claimed, a job whose job dir no longer keeps its envelope, and a record missing
its folder or any file's id, name, size or sha256. The live run must repeat the
dry run's digest.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import hand_delivery, run_record, spend
from ..hand_delivery import Refused
from ..proof import watch

REPLY_KIND = "drafting_job"
#: Ledger states a hand delivery is recorded from. A job SMD stopped
#: (`jobs.stop`) is `failed` in the ledger with a `stopped_by_smd:` reason.
RECORDABLE_FROM = frozenset({"failed", "held"})
#: Daemon states of a job that has ended (the stop's own is `stopped_by_smd`).
ENDED_DAEMON = frozenset({"failed", "held", "stopped_by_smd"})
#: The roles the lane files under (`drafting/finish.py`); the revise path keys on them.
ROLES = ("draft", "attorney_notes")
QUIET = "SMD answered the requester by hand; no DELIVER wake"


def parse_record(rec: Any) -> dict[str, Any]:
    """The folder and the filed files, each complete."""
    if not isinstance(rec, dict):
        raise Refused("the record must be a JSON object")
    folder_id = str(rec.get("folder_id") or "").strip()
    if not folder_id:
        raise Refused("record.folder_id is required: the folder the documents were filed in")
    files = rec.get("files")
    if not isinstance(files, list) or not files:
        raise Refused("record.files must list every filed document as read back from the matter")
    out: list[dict[str, Any]] = []
    for f in files:
        clean = hand_delivery.checked_file(f, need_sha=True, need_name=True)
        role = str(f.get("role") or "draft")
        if role not in ROLES:
            raise Refused(f"record.files role must be one of {list(ROLES)}")
        out.append({**clean, "role": role})
    if not any(f["role"] == "draft" for f in out):
        raise Refused("record.files must name at least one filed draft (role draft)")
    if len({f["file_id"] for f in out}) != len(out):
        raise Refused("record.files names one file id twice")
    return {"folder_id": folder_id, "files": out}


def plan(d: Any, job_id: str, rec: dict[str, Any], *, quiet: bool) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    facts = parse_record(rec)
    row, _st, _env = hand_delivery.check_job(
        d,
        job_id,
        noun="drafting",
        recordable=RECORDABLE_FROM,
        resumable_daemon=ENDED_DAEMON,
        refused_why="only a held, failed or stopped job is recorded as delivered by SMD",
        requester_keys=("requester", "message_ref"),
        reply_kind=REPLY_KIND,
    )
    cents = spend.recorded_cents(d.job_dir(job_id), d.LANE, int(row.get("cents") or 0))
    files = [{"name": f["name"], "size": f["bytes"], "role": f["role"]} for f in facts["files"]]
    delivered = {
        "cents": cents,
        "folder_id": facts["folder_id"],
        "delivery": {"files": files, "cents": cents},
        **run_record.fields_for(d, "hand_delivery", by="smd", with_release=True),
    }
    records: list[tuple[str, dict[str, Any]]] = []
    if row.get("state") == "held":
        # The drafting ledger moves a held row only to failed; running clears this reason.
        records.append(("failed", {"reason": "delivered_by_smd: SMD filed the document by hand"}))
    records += [("running", {}), ("delivered", delivered)]
    task = d._compose_wake(job_id, "delivered", {"cents": cents, "folder_id": facts["folder_id"], "files": files})
    wake = {"pending": False, "attempts": 0, "task": task, "quiet": QUIET} if quiet else None
    return {
        "job_id": job_id,
        "from_state": row.get("state"),
        "records": records,
        "proof": hand_delivery.proof_request(
            d, job_id, facts["files"], review="released", attempt=watch._attempt(d, job_id)
        ),
        "state": {
            "state": "delivered",
            "delivered_by": "smd",
            "reason": None,
            "wake": wake or {"pending": True, "attempts": 0, "task": task},
        },
    }


apply = hand_delivery.apply


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.drafting.deliver_by_smd", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("record", help="JSON: folder_id, files [{file_id, name, bytes, sha256, role}]")
    ap.add_argument("--quiet", action="store_true", help="SMD already answered the attorney: queue no DELIVER wake")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written and its digest; write nothing")
    ap.add_argument("--confirm", default=None, help="the digest the dry run printed")
    a = ap.parse_args(argv)
    from .. import daemon as daemon_mod, drafting_lane

    d = drafting_lane.build_lane(daemon_mod.build())
    after = "no wake (--quiet)" if a.quiet else "the daemon dispatches its DELIVER wake on its next tick"
    return hand_delivery.run_cli(
        d,
        lambda lane: plan(lane, a.job_id, hand_delivery.read_record(a.record), quiet=a.quiet),
        dry_run=a.dry_run,
        confirm=a.confirm,
        use_digest=True,
        done=f"recorded {a.job_id} delivered; {after}",
    )


__all__ = ["REPLY_KIND", "Refused", "apply", "main", "parse_record", "plan"]


if __name__ == "__main__":
    sys.exit(main())
