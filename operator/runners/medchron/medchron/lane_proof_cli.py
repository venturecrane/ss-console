"""`medchron lane-proof ...`: SMD's hands on the lane-proof gate, run as root
on the seat (``operator/bin/lane-proof.sh`` wraps it from a laptop).

* ``status``     the proven set, the jobs waiting on a proof, recent proof runs.
* ``dry-run ID`` ask the job's lane for a proof run of it: a held job, or a
                 FINISHED one whose job dir still keeps its envelope (prove a
                 lane on a real past request before the firm next asks);
                 ``--wait`` polls for its result and prints it.
* ``accept ID --evidence vfy_... --by NAME``
                 record the lane proven, from that job's latest PASSED proof
                 run, naming the verify id of SMD reading its artifacts. The
                 daemon releases every job waiting on the lane on its next tick.
* ``revoke KEY --why ... --by NAME``
                 un-prove a lane (new jobs of that kind wait again).

There is deliberately no command that proves a lane without a passed proof
run: the authored ``lane-proofs.yaml`` (reviewed in git) is the only other way
in, and that is for lanes whose live record already proves them.

Every output line is plain text for a person reading a terminal. File names
and matter text stay on the seat; only lane keys, job ids, counts and paths
are printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import lane_proof, proof_run

DEFAULT_RUN_DIR = "/run/smd-medchron"
#: Each daemon lane's queue under the run dir (the entrypoints' exports).
QUEUES = {
    "medchron": "queue",
    "demand": "demand-queue",
    "drafting": "drafting-queue",
    "litigation": "litigation-queue",
    "negotiation": "negotiation-queue",
}


#: Each daemon lane's job dirs (a finished job's envelope, kept 72 h).
JOBS = {
    "medchron": "jobs",
    "demand": "demand-jobs",
    "drafting": "drafting-jobs",
    "litigation": "litigation-jobs",
    "negotiation": "negotiation-jobs",
}


def _lane_of(run_dir: Path, job_id: str, *, finished: bool = False) -> str | None:
    """The lane holding the job: queued (waiting), or with ``finished`` a job
    dir that still keeps its envelope (a proactive proof of a real request)."""
    for lane, q in QUEUES.items():
        if (run_dir / q / f"{job_id}.json").is_file():
            return lane
    if finished:
        for lane, j in JOBS.items():
            if (run_dir / j / job_id / "envelope.json").is_file():
                return lane
    return None


def _status(run_dir: Path) -> int:
    proofs = lane_proof.load(run_dir)
    print("Proven on this seat:")
    for key in sorted(proofs.proven):
        e = proofs.proven[key]
        flag = " (REVOKED in the authored file)" if key in proofs.revoked else ""
        print(f"  {key}: {e.get('source')} {e.get('at') or e.get('proven_at') or ''} {e.get('evidence') or ''}{flag}")
    if not proofs.proven:
        print("  (none)")
    for p in proofs.problems:
        print(f"  PROBLEM: {p}")
    print("Waiting on a proof:")
    held = lane_proof.holds(run_dir)
    for h in held:
        queued = _lane_of(run_dir, str(h.get("job_id"))) is not None
        since = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(float(h.get("held_at") or 0)))
        print(f"  {h.get('job_id')} {h.get('key')} since {since}{'' if queued else ' (no longer queued)'}")
    if not held:
        print("  (none)")
    print("Proof runs on the volume:")
    for r in proof_run.results(run_dir)[-10:]:
        print(f"  {r.get('finished_at')} {r.get('job_id')} {r.get('key')}: {r.get('status')} ({r.get('why') or ''})")
    return 0


def _dry_run(run_dir: Path, job_id: str, wait: float) -> int:
    lane = _lane_of(run_dir, job_id, finished=True)
    if lane is None:
        print(f"no envelope for {job_id}: neither waiting in a queue nor kept in a job dir (wiped at 72 h)")
        return 2
    marker = proof_run.request(run_dir / QUEUES[lane], job_id)
    print(f"proof run requested: {lane} job {job_id} ({marker.name}); the lane runs it when it is idle")
    deadline = time.time() + wait
    while wait and time.time() < deadline:
        if not marker.exists():
            mine = [r for r in proof_run.results(run_dir) if r.get("job_id") == job_id]
            if mine:
                print(json.dumps(mine[-1], indent=1))
                return 0 if mine[-1].get("status") == "passed" else 1
        time.sleep(15)
    return 0


def _accept(run_dir: Path, job_id: str, evidence: str, by: str) -> int:
    if not lane_proof.VERIFY_RE.match(evidence):
        print("--evidence must be the crane_verify id (vfy_...) of reading the proof run's artifacts")
        return 2
    mine = [r for r in proof_run.results(run_dir) if r.get("job_id") == job_id]
    if not mine:
        print(f"no proof run of {job_id} on the volume; run `lane-proof dry-run {job_id}` first")
        return 2
    last = mine[-1]
    if last.get("status") != "passed":
        print(f"the latest proof run of {job_id} did not pass ({last.get('status')}: {last.get('why')}); not accepted")
        return 1
    row = lane_proof.append_event(
        run_dir,
        {
            "event": "proven",
            "key": last["key"],
            "job_id": job_id,
            "proof": last.get("path"),
            "evidence": evidence,
            "by": by,
            "cents": last.get("cents"),
        },
    )
    print(f"{row['key']} PROVEN at {row['at']}; every job waiting on it is released on the lane's next tick")
    return 0


def _revoke(run_dir: Path, key: str, why: str, by: str) -> int:
    if not lane_proof.KEY_RE.match(key) or not why.strip():
        print("revoke needs a lane key and a reason")
        return 2
    row = lane_proof.append_event(run_dir, {"event": "revoked", "key": key, "why": why.strip()[:300], "by": by})
    print(f"{key} revoked at {row['at']}; new jobs of that kind wait for a proof again")
    return 0


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if args.action != "status" and not args.target:
        print(f"{args.action} needs a job id or a lane key")
        return 2
    if args.action == "status":
        return _status(run_dir)
    if args.action == "dry-run":
        return _dry_run(run_dir, args.target, args.wait)
    if not args.by.strip():
        print("--by is required: who is recording this")
        return 2
    if args.action == "accept":
        return _accept(run_dir, args.target, args.evidence, args.by.strip())
    return _revoke(run_dir, args.target, args.why, args.by.strip())


def add(sub: Any) -> None:
    lp = sub.add_parser("lane-proof", help="the lane-proof gate: status, dry-run a held job, accept, revoke")
    lp.add_argument("action", choices=["status", "dry-run", "accept", "revoke"])
    lp.add_argument("target", nargs="?", default="", help="a job id (dry-run, accept) or a lane key (revoke)")
    lp.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    lp.add_argument("--wait", type=float, default=0.0, help="dry-run: seconds to wait for the result")
    lp.add_argument("--evidence", default="", help="accept: the crane_verify id of reading the artifacts")
    lp.add_argument("--why", default="", help="revoke: the reason")
    lp.add_argument("--by", default="", help="accept/revoke: who")
    lp.set_defaults(fn=run)


if __name__ == "__main__":  # pragma: no cover
    p = argparse.ArgumentParser(prog="medchron")
    add(p.add_subparsers(dest="cmd", required=True))
    a = p.parse_args()
    sys.exit(a.fn(a))
