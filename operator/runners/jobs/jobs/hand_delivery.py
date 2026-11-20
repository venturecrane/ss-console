"""The lane-neutral half of recording a delivery SMD made by hand.

Each lane's ``deliver_by_smd`` (``medchron/deliver_by_smd.py``,
``drafting/deliver_by_smd.py``) builds its own delivered record, the fields its
own ``_report`` would have handed the broker. Everything else is the same for
every lane and lives here:

* the job checks: the ledger row exists and ended in a state the lane names,
  nothing for it is queued, its job dir keeps the envelope that names the
  requester and the request email, no wake is still to go, no delivered wake
  was already written, and no delivered reply was already claimed;
* a file read back from the matter, checked: its id and size (and, where the
  lane's revise path pairs files by hash, its sha256);
* the ``DELIVERY_PROVEN`` row (``delivery_proof_record``);
* the write, in the daemon's own order: the ledger records, the proof row, then
  the job's daemon state (terminal, with the lane's own wake or none);
* the dry run and the CLI flow.

Nothing here verifies against the matter itself: the filing step already read
each file back by id, and the person running the tool supplies what it read.

No dollar figure is written anywhere a person reads.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

_SHA256 = re.compile(r"[0-9a-f]{64}")


class Refused(RuntimeError):
    """This delivery cannot be recorded; the message says why."""


def client(d: Any) -> Any:
    """The broker client under a lane's broker (a drafting lane wraps its client)."""
    b = d.broker
    return getattr(b, "client", b)


def checked_file(f: Any, *, need_sha: bool = False, need_name: bool = False) -> dict[str, Any]:
    """One file as read back from the matter: {file_id, name, bytes, sha256}."""
    if not isinstance(f, dict) or not str(f.get("file_id") or "").strip():
        raise Refused("every record.files entry needs the file_id read back from the matter")
    size = f.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise Refused("every record.files entry needs its read-back size in bytes")
    sha = str(f.get("sha256") or "")
    if need_sha and not _SHA256.fullmatch(sha):
        raise Refused("every record.files entry needs its read-back sha256 (64 lowercase hex characters)")
    name = str(f.get("name") or "")
    if need_name and not name.strip():
        raise Refused("every record.files entry needs the file's name as filed")
    return {"file_id": str(f["file_id"]), "name": name, "bytes": size, "sha256": sha}


def check_job(
    d: Any,
    job_id: str,
    *,
    noun: str,
    recordable: Iterable[str],
    resumable_daemon: Iterable[str],
    refused_why: str,
    requester_keys: tuple[str, str],
    reply_kind: str,
    finishable: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(ledger row, daemon state, envelope) of a job this tool may record. Raises Refused.

    ``recordable``: the ledger states a hand delivery is recorded from.
    ``resumable_daemon``: the daemon states that make a ``running`` ledger row
    this tool's own half-finished write rather than a job the daemon runs.
    ``requester_keys``: the envelope's (requester, request email) fields.
    ``finishable``: a lane may pass a test of a ``delivered`` row; a row that
    passes it, while the daemon state still says the job ended, is this tool's
    own write to finish (the daemon state or the wake did not land)."""
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no {noun} job {job_id}")
    st = d._daemon_state(job_id)
    # `running` is accepted only as this tool's own half-finished write (the
    # ledger moved, the delivered record did not land): the daemon's state for
    # the job still says it ended. A job the daemon is running says otherwise.
    ended = st.get("state") in set(resumable_daemon)
    resumable = ended and (
        row.get("state") == "running"
        or (row.get("state") == "delivered" and finishable is not None and finishable(row))
    )
    if row.get("state") not in set(recordable) and not resumable:
        raise Refused(f"job {job_id} is {row.get('state')}; {refused_why}")
    if (d.queue / f"{job_id}.json").exists():
        raise Refused(f"job {job_id} is still queued; the daemon would run it. Nothing was written.")
    jd = d.job_dir(job_id)
    try:
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"job {job_id} has no readable envelope in its job dir ({exc}); nothing was written") from exc
    who, ref = requester_keys
    if not env.get(who) or not env.get(ref):
        raise Refused(f"job {job_id}'s envelope names no requester or request email; there is no thread to answer")
    wake = st.get("wake") or {}
    # A CONSUMED wake for a failed job is replaced: DELIVER sends nothing on
    # `failed`. A consumed hold wake had its one reply under its own key. A
    # wake still pending is the daemon's to send and is never overwritten,
    # whatever its outcome; a delivered state or a consumed delivered wake
    # means the delivery was already announced.
    if st.get("state") == "delivered" or wake.get("pending") or "Outcome: delivered." in str(wake.get("task") or ""):
        raise Refused(f"job {job_id} is already delivered or has a wake still to go; one delivery wake per job")
    replies = client(d)._request({"action": "delivery_reply_status", "kind": reply_kind, "job_id": job_id})
    if any(str(r.get("key") or "").endswith(":delivered") for r in replies.get("replies") or []):
        raise Refused(f"job {job_id} already has its delivered reply; a second would be a second send")
    return row, st, env


def proof_request(d: Any, job_id: str, files: list[dict[str, Any]], *, review: str, attempt: int) -> dict[str, Any]:
    """The ``DELIVERY_PROVEN`` row: ids, sizes and hashes only, never a name."""
    return {
        "action": "delivery_proof_record",
        "lane": d.LANE,
        "job_id": job_id,
        "attempt": attempt,
        "review": review,
        "review_cents": 0,
        "review_model": "",
        "files": [{"file_id": f["file_id"], "size": f["bytes"], "sha256": f["sha256"]} for f in files],
        "rows": 0,
    }


def apply(d: Any, p: dict[str, Any]) -> None:
    """The plan's writes, in the lane's own order: the chronology's daemon
    records the ledger and then the proof; the drafting lane writes the proof
    first (``proof_first``), so its row is never delivered without one. The
    terminal daemon state is always last."""
    job_id = p["job_id"]
    if p.get("proof_first"):
        client(d)._request(p["proof"])
    for state, fields in p["records"]:
        d.broker.record(job_id, state, fields)
    if not p.get("proof_first"):
        client(d)._request(p["proof"])
    d._write_state(job_id, finished_at=d.clock(), **p["state"])


def digest(p: dict[str, Any]) -> str:
    """A short digest of a plan: what the dry run showed is what the live run writes."""
    blob = json.dumps(p, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def daemon_env(names: Iterable[str], *, proc: Path = Path("/proc")) -> dict[str, str]:
    """The named variables from the running jobs daemon's environment, and no
    others (the daemon's environment holds secrets; only these names are read).
    {} when no daemon runs or its environment cannot be read. A root shell on
    the seat (``fly ssh console``) does not carry the entrypoint's exports, so
    a tool run there takes the daemon's own values instead of a guessed path."""
    wanted = set(names)
    try:
        pids = [p for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return {}
    for pid in sorted(pids, key=lambda p: int(p.name)):
        try:
            argv = (pid / "cmdline").read_bytes().split(b"\0")
            if b"jobs.daemon" not in argv or b"-m" not in argv:
                continue
            raw = (pid / "environ").read_bytes()
        except OSError:
            continue
        out: dict[str, str] = {}
        for item in raw.split(b"\0"):
            key, sep, value = item.decode("utf-8", "replace").partition("=")
            if sep and key in wanted:
                out[key] = value
        return out
    return {}


def read_record(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_cli(
    d: Any,
    make_plan: Callable[[Any], dict[str, Any]],
    *,
    dry_run: bool,
    done: str,
    confirm: str | None = None,
    use_digest: bool = False,
    partial: tuple[type[BaseException], ...] = (),
) -> int:
    """Plan on the built lane ``d``, print, and (not a dry run) apply. Exit 0
    done or dry run, 2 refused (nothing written). With ``use_digest`` a live
    run must repeat the dry run's digest. An error of a ``partial`` type during
    the writes exits 3: some may have landed, and a fresh run finishes them."""
    try:
        if d is None:
            raise Refused("this seat has no such lane running; nothing was written")
        p = make_plan(d)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    out: dict[str, Any] = {"dry_run": dry_run, **p}
    if use_digest:
        out["digest"] = digest(p)
    print(json.dumps(out, indent=1, default=str))
    if dry_run:
        if use_digest:
            print(f"Nothing was written. To record it, rerun without --dry-run and with --confirm {out['digest']}.")
        return 0
    if use_digest and confirm != out["digest"]:
        print(
            "REFUSED: a live run needs --confirm <digest> from a fresh dry run of the same record; nothing was written",
            file=sys.stderr,
        )
        return 2
    try:
        apply(d, p)
    except partial as exc:
        print(
            f"FAILED PARTWAY: {exc}. Some writes may have landed; dry-run again and confirm to finish.", file=sys.stderr
        )
        return 3
    print(done)
    return 0


__all__ = ["Refused", "apply", "check_job", "checked_file", "client", "digest", "proof_request", "run_cli"]
