"""Record a document SMD drafted and filed by hand as the drafting job's delivery.

    python -m jobs.drafting.deliver_by_smd <job_id> <record.json> [--quiet] --dry-run
    python -m jobs.drafting.deliver_by_smd <job_id> <record.json> [--quiet] --confirm <digest>

Root only, on the seat, with the jobs venv's python. It talks to the broker and
reads the job dirs exactly as the runner daemon does, from the same environment
names (`ENV` below, each taken from its home: `daemon`, `seat_config`,
`drafting_lane`); any of those the shell does not set is read from the running
daemon's own environment, by name only (`hand_delivery.daemon_env`), so a bare
root shell needs nothing exported.

WHY THIS EXISTS (2026-11-19). Twice in one evening an attorney asked for a
change to a document SMD had drafted and filed by hand for a request the
Operator could not finish. The seat's own job for that request had been stopped
by SMD, so the job store held no DELIVERED draft of that class on the matter.
The broker's revise routing (`drafting_verbs._route`) reads exactly that, so the
change request queued a fresh full draft, and the reply told the attorney how
the earlier draft was made. After this tool the job store says what happened:
the draft was delivered, as these files, and the next request in that thread
revises them in place.

What it writes, in the drafting lane's own order (`drafting_lane._report`:
the proof before the record), so the row is never delivered without its proof:

  1. one `DELIVERY_PROVEN` row with each file's id, size and sha256, in the same
     order as the files, which is what `DraftingLedger.proven_files` pairs by;
  2. the ledger row moves `failed` -> `running` -> `delivered`, with the folder
     and the files (name, size, role);
  3. the job's daemon state goes terminal (`delivered`), with the lane's own
     DELIVER wake pending (the daemon dispatches it and arms the reply watch),
     or with `--quiet` no wake, when SMD already answered the attorney.

A run cut short is finished by running it again: a `running` row, or a
`delivered` row carrying exactly this record's folder and files while the
daemon state still says the job ended, is taken as this tool's own write. The
proof row is written again then (the ledger reads the last one).

It verifies nothing against Smokeball: the filing step already read each file
back by id, and the record carries what that read-back said.

What it refuses (nothing is written): a HELD job (withdraw it first with
`jobs.stop`: a held row may move only to failed, and a failed row that SMD did
not write would show the firm's portal "Could not finish drafting a document"
at the moment SMD records the delivery); a job still running or queued; a job
already delivered, with a wake still to go, or with a delivered reply already
claimed; another live job for the same request email and class (the ledger's
unique index would refuse the move part way); a job dir with no envelope; a
record missing its folder or any file's id, name, size or sha256. The live run
must repeat the dry run's digest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .. import budget, daemon as daemon_mod, drafting_lane, hand_delivery, run_record, seat_config, spend
from ..broker_client import BrokerError
from ..hand_delivery import Refused
from ..proof import watch

REPLY_KIND = "drafting_job"
#: The ledger state a hand delivery is recorded from: a job that failed, or one
#: SMD stopped (`jobs.stop` records it `failed` with a `stopped_by_smd:` reason).
RECORDABLE_FROM = frozenset({"failed"})
#: Daemon states of a job that has ended (the stop's own is `stopped_by_smd`).
ENDED_DAEMON = frozenset({"failed", "stopped_by_smd"})
#: The roles the lane files under (`drafting/finish.py`); the revise path keys on them.
ROLES = ("draft", "attorney_notes")
QUIET = "SMD answered the requester by hand; no DELIVER wake"
#: What the tool needs from the daemon's environment (`daemon.build`, `drafting_lane.build_lane`).
#: The rate card is one of them: without it a job's spend is priced from a path the seat does not
#: have, and the record falls back to the row's own cents (seen on the firm seat, 2026-11-20).
ENV = (
    daemon_mod.SOCKET_ENV,
    seat_config.SLUG_VAR,
    daemon_mod.RUN_DIR_ENV,
    drafting_lane.QUEUE_ENV,
    budget.PRICING_ENV,
)


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


def _twin(d: Any, row: dict[str, Any]) -> str | None:
    """Another live job (not failed) for this job's request email and class:
    the ledger's unique index (request_ref, document_class) would refuse the
    failed -> running move after the proof had landed."""
    client = hand_delivery.client(d)
    for other in client._request({"action": "drafting_job_status"}).get("jobs") or []:
        oid = str(other.get("id") or "")
        if oid == row.get("id") or other.get("state") == "failed":
            continue
        if other.get("document_class") != row.get("document_class"):
            continue
        full = client._request({"action": "drafting_job_status", "job_id": oid}).get("job") or {}
        if full.get("request_ref") and full.get("request_ref") == row.get("request_ref"):
            return oid
    return None


def _stored_files(row: dict[str, Any]) -> list[Any] | None:
    try:
        return (json.loads(row.get("delivery_json") or "{}") or {}).get("files")
    except ValueError:
        return None


def plan(d: Any, job_id: str, rec: dict[str, Any], *, quiet: bool) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    facts = parse_record(rec)
    files = [{"name": f["name"], "size": f["bytes"], "role": f["role"]} for f in facts["files"]]
    first = d.broker.status(job_id)
    if first is not None and first.get("state") == "held":
        raise Refused(
            f"job {job_id} is held. Withdraw it first (operator/bin/lane-proof.sh <seat> withdraw drafting "
            f"{job_id} --by <you> --why '<reason>'), then record the delivery; nothing was written"
        )

    def ours(row: dict[str, Any]) -> bool:
        return row.get("folder_id") == facts["folder_id"] and _stored_files(row) == files

    row, _st, _env = hand_delivery.check_job(
        d,
        job_id,
        noun="drafting",
        recordable=RECORDABLE_FROM,
        resumable_daemon=ENDED_DAEMON,
        refused_why="only a failed or withdrawn job is recorded as delivered by SMD",
        requester_keys=("requester", "message_ref"),
        reply_kind=REPLY_KIND,
        finishable=ours,
    )
    twin = _twin(d, row)
    if twin is not None:
        raise Refused(
            f"job {twin} is live for the same request email and document class; recording {job_id} "
            "would collide with it. Nothing was written"
        )
    cents = spend.recorded_cents(d.job_dir(job_id), d.LANE, int(row.get("cents") or 0))
    records: list[tuple[str, dict[str, Any]]] = []
    if row.get("state") == "failed":
        records.append(("running", {}))
    if row.get("state") != "delivered":
        delivered = {
            "cents": cents,
            "folder_id": facts["folder_id"],
            "delivery": {"files": files, "cents": cents},
            **run_record.fields_for(d, "hand_delivery", by="smd", with_release=True),
        }
        records.append(("delivered", delivered))
    task = d._compose_wake(job_id, "delivered", {"cents": cents, "folder_id": facts["folder_id"], "files": files})
    wake = {"pending": False, "attempts": 0, "task": task, "quiet": QUIET} if quiet else None
    return {
        "job_id": job_id,
        "from_state": row.get("state"),
        "proof_first": True,
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

#: A served set SMD owed on a delivered response job (``served_deliver.py``).
from .served_deliver import apply_set, apply_told, plan_set, plan_told  # noqa: E402


def _run_set(d: Any, a: Any) -> int:
    """``--set``: the dry run and the confirmed live run, as for a whole job;
    with ``--told``, the attorney was told and the set is marked so."""
    if a.told:
        return hand_delivery.run_cli(
            d,
            lambda lane: plan_told(lane, a.job_id, a.set_label),
            dry_run=a.dry_run,
            confirm=a.confirm,
            use_digest=True,
            partial=(BrokerError,),
            done=f"marked {a.set_label} told on {a.job_id}",
            applier=apply_told,
        )
    return hand_delivery.run_cli(
        d,
        lambda lane: plan_set(lane, a.job_id, hand_delivery.read_record(a.record), a.set_label, quiet=a.quiet),
        dry_run=a.dry_run,
        confirm=a.confirm,
        use_digest=True,
        partial=(BrokerError,),
        done=f"recorded {a.set_label} on {a.job_id}; "
        + (
            "no reply (--quiet)"
            if a.quiet
            else "now send the reply line above to the attorney as an SMD note, then run this again with --told"
        ),
        applier=apply_set,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.drafting.deliver_by_smd", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("record", nargs="?", help="JSON: folder_id, files [{file_id, name, bytes, sha256, role}]")
    ap.add_argument("--quiet", action="store_true", help="SMD already answered the attorney: queue no DELIVER wake")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written and its digest; write nothing")
    ap.add_argument("--confirm", default=None, help="the digest the dry run printed")
    ap.add_argument("--set", dest="set_label", default=None, help="a served set SMD owed on a delivered response job")
    ap.add_argument("--told", action="store_true", help="with --set: the attorney was told the set is filed")
    a = ap.parse_args(argv)
    if not a.record and not (a.set_label and a.told):
        ap.error("the record is required (only --set ... --told takes none)")
    for k, v in hand_delivery.daemon_env([n for n in ENV if not os.environ.get(n)]).items():
        os.environ[k] = v
    d = drafting_lane.build_lane(daemon_mod.build())
    if a.set_label:
        return _run_set(d, a)
    after = "no wake (--quiet)" if a.quiet else "the daemon dispatches its DELIVER wake on its next tick"
    return hand_delivery.run_cli(
        d,
        lambda lane: plan(lane, a.job_id, hand_delivery.read_record(a.record), quiet=a.quiet),
        dry_run=a.dry_run,
        confirm=a.confirm,
        use_digest=True,
        partial=(BrokerError,),
        done=f"recorded {a.job_id} delivered; {after}",
    )


__all__ = ["ENV", "REPLY_KIND", "Refused", "apply", "main", "parse_record", "plan"]


if __name__ == "__main__":
    sys.exit(main())
