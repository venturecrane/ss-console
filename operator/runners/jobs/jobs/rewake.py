"""`jobs rewake <job_id> --by WHO --why WHY [--lane LANE] [--again]`: start
one finished job's DELIVER turn again, so the email it owed goes out.

The case it exists for (A&P 201167, 2026-10-20): a negotiation alert's turn
ended with its reply held by the outgoing-email check, nothing was sent, and
the reply watch paged twenty minutes later. Setting the stored wake back to
pending did nothing: ``dispatch_wakes`` posted the same ``handoff_id``
(``<lane>-<job>``) and the gateway answered 202 and dropped it as a duplicate
delivery. The re-send that worked was a hand POST under a new id.

This makes that the supported path. It re-arms the stored, code-authored task
under a re-send number, and the daemon posts it as ``<lane>-<job>-r<n>``
(``<lane>-<job>-w<seq>-r<n>`` for a job's later wake; see ``handoff_id``) on
its next tick, then watches for the reply exactly as it did the first time
(``proof/watch.py``): a re-send that also goes unsent pages again.

It refuses where a re-send would be wrong: no stored task, a wake still
pending, a reply watch still open, or a reply already proven sent (that one
needs ``--again``, because it would email the requester a second time).

Run as root on the seat: the job dirs are root-owned. The file is rewritten
in place, so its owner and mode are kept.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import seat_layout
from .lane_proof_cli import DEFAULT_RUN_DIR, JOBS

#: Proof alerts a re-send must be able to raise again within the day.
_RESET_ALERTS = ("reply_unproven:", "wake_lost:")


def handoff_id(lane: str, job_id: str, wake: dict[str, Any]) -> str:
    """The id the gateway admits this wake under.

    The gateway's durable admission keys on this id: the same id with the
    same text is a duplicate (dropped), the same id with DIFFERENT text is an
    ``admission_conflict`` (503, on every retry). So every distinct wake a job
    writes needs its own id, and every re-post of one wake the same id.

    ``seq`` numbers the job's wakes (``stamp``): a resumed job's next attempt,
    or SMD's delivered record over a failed run, writes a new wake with new
    text, and reusing the first wake's id is what lost a drafting reply on
    2026-11-03 (five 503s, then ``wake_lost``, until a hand ``rewake``).
    ``resend`` numbers a ``rewake`` of the SAME wake, whose text is unchanged.
    A job's first wake keeps the bare ``<lane>-<job>`` id it always had."""
    seq = int(wake.get("seq") or 1)
    n = int(wake.get("resend") or 0)
    return f"{lane}-{job_id}" + (f"-w{seq}" if seq > 1 else "") + (f"-r{n}" if n > 0 else "")


def stamp(st: dict[str, Any], wake: dict[str, Any]) -> bool:
    """Number a wake the first time it is posted or re-armed; True when it was
    unnumbered (the caller persists ``wake`` and ``wake_seq``).

    The running count lives in the job's ``wake_seq``, outside the wake,
    because a resume clears the wake itself. A job whose earlier wakes predate
    the count (no ``wake_seq``) is seeded from what it has done: each resume,
    and SMD's delivered record over a job that ran, followed a wake already
    posted under the bare id. Over-counting only skips a number; under-counting
    is the conflict."""
    if wake.get("seq"):
        return False
    prev = st.get("wake_seq")
    if prev is None:
        prev = int(st.get("resumes") or 0) + (1 if st.get("delivered_by") == "smd" and st.get("attempts") else 0)
    wake["seq"] = int(prev) + 1
    return True


def post_id(d: Any, job_id: str, wake: dict[str, Any]) -> str:
    """The id ``Daemon._dispatch_wakes`` posts ``wake`` under. A newly numbered
    wake is written to the job's state BEFORE the post, so a crash after the
    gateway admitted it re-posts under the same id and is deduped."""
    if stamp(d._daemon_state(job_id), wake):
        d._write_state(job_id, wake=wake, wake_seq=wake["seq"])
    return handoff_id(d.LANE, job_id, wake)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: The broker's socket when ``SMD_WORKSPACE_BROKER_SOCKET`` is unset (the
#: same default ``operator/bin/medchron-backfill-covered.py`` uses).
DEFAULT_SOCKET = seat_layout.BROKER_SOCKET

#: What ``thread(kind, job_id)`` answers: the broker's ``delivery_reply_status``
#: ``thread`` field, ``{"checked": bool, "answered": sentence}``.
ThreadCheck = Callable[[str, str], dict[str, Any]]


def thread_check(client: Any) -> ThreadCheck:
    """Ask the broker whether job ``job_id``'s thread already holds a reply sent
    after it finished (2026-11-06): an SMD hand release counts, and a
    completion re-sent then is the requester's second email. A broker that
    could not answer is ``checked: False``, never "nothing in the thread"."""

    def ask(kind: str, ident: str) -> dict[str, Any]:
        from .broker_client import BrokerError

        try:
            resp = client._request({"action": "delivery_reply_status", "kind": kind, "job_id": ident, "thread": True})
        except (BrokerError, OSError, ValueError, TypeError) as exc:
            return {"checked": False, "answered": "", "why": str(exc)[:200]}
        thread = resp.get("thread") if isinstance(resp, dict) else None
        if not isinstance(thread, dict):
            return {"checked": False, "answered": "", "why": "the broker did not report on the thread"}
        return {"checked": bool(thread.get("checked")), "answered": str(thread.get("answered") or "")}

    return ask


def reply_kind(lane: str) -> str:
    """The bound-reply kind a lane's job replies under (``medchron`` is the
    chronology lane's own name for its jobs)."""
    return f"{lane}_job"


def rewake(
    run_dir: Path,
    lane: str,
    job_id: str,
    by: str,
    why: str,
    again: bool,
    clock: Callable[[], float] = time.time,
    thread: ThreadCheck | None = None,
) -> int:
    p = run_dir / JOBS[lane] / job_id / "daemon.json"
    if not p.is_file():
        print(f"no {lane} job {job_id} on this seat (wiped at 72 h, or never here)")
        return 2
    st = json.loads(p.read_text(encoding="utf-8"))
    wake = st.get("wake")
    if not isinstance(wake, dict) or not wake.get("task"):
        print(f"{job_id} has no stored DELIVER task to start again")
        return 2
    if wake.get("pending"):
        print(f"{job_id}'s wake is already pending; the daemon sends it on its next tick")
        return 1
    w = st.get("reply_watch") if isinstance(st.get("reply_watch"), dict) else {}
    if w and not w.get("done"):
        print(f"{job_id}'s reply watch is still open (due {_iso(float(w.get('due') or 0))}); wait for it to settle")
        return 1
    if w.get("proven_at") and not again:
        print(
            f"{job_id}'s reply was proven sent at {_iso(float(w['proven_at']))}; --again sends the requester a second"
        )
        return 1
    if thread is not None:
        # 2026-11-06 (A&P): the completion went out as an SMD hand release,
        # outside the job's claim; a rewake here would have sent it twice.
        seen = thread(reply_kind(lane), job_id)
        if not seen.get("checked"):
            print(
                f"refused: could not ask the broker whether {job_id}'s thread already has a reply "
                f"({seen.get('why') or 'the broker did not say'}); nothing was re-armed or sent"
            )
            return 1
        if seen.get("answered"):
            print(f"refused: {seen['answered']}")
            return 1
    n = int(wake.get("resend") or 0) + 1
    now = clock()
    # An unnumbered (pre-count) wake keeps the number it was posted under, so
    # the re-send differs from it only by -r<n>.
    stamp(st, wake)
    st["wake_seq"] = max(int(st.get("wake_seq") or 0), int(wake["seq"]))
    wake.update(pending=True, attempts=0, resend=n, sent_at=None, outcome=None)
    st["wake"] = wake
    st.pop("reply_watch", None)
    st["proof_alerts"] = {k: v for k, v in (st.get("proof_alerts") or {}).items() if not k.startswith(_RESET_ALERTS)}
    st["rewakes"] = [*(st.get("rewakes") or []), {"at": now, "by": by, "why": why[:300], "resend": n}]
    with p.open("r+", encoding="utf-8") as f:
        f.write(json.dumps(st, indent=1))
        f.truncate()
    print(
        f"{lane} {job_id}: DELIVER turn re-armed as {handoff_id(lane, job_id, wake)}; the daemon starts it on its "
        "next tick and pages if the reply is not seen in Sent Items within 20 minutes"
    )
    return 0


def _run(args: Any) -> int:
    if not args.by.strip() or not args.why.strip():
        print("--by and --why are required: who is re-sending, and why")
        return 2
    import os

    from .broker_client import BrokerClient
    from .daemon import SOCKET_ENV

    # The variable is entrypoint-computed for the broker only, so a root or
    # medchron shell on the seat (where a rewake is run) does not have it: the
    # broker's own default path is the answer then. A socket that cannot be
    # reached is ``thread_check``'s ``checked: False``, which re-arms nothing.
    sock = os.environ.get(SOCKET_ENV, "").strip() or DEFAULT_SOCKET
    return rewake(
        Path(args.run_dir),
        args.lane,
        args.job_id,
        args.by.strip(),
        args.why.strip(),
        args.again,
        thread=thread_check(BrokerClient(sock)),
    )


def add(sub: Any) -> None:
    rw = sub.add_parser("rewake", help="start one finished job's DELIVER turn again (a stopped alert or reply)")
    rw.add_argument("job_id", help="the job id (for a negotiation alert, the notice id)")
    rw.add_argument("--lane", choices=sorted(JOBS), default="negotiation")
    rw.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    rw.add_argument("--by", default="", help="who is re-sending")
    rw.add_argument("--why", default="", help="why the first one did not go")
    rw.add_argument("--again", action="store_true", help="re-send even though a reply was proven sent")
    rw.set_defaults(fn=_run)
