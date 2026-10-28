"""The daemon's proven-delivery duties, kept out of ``daemon.py`` (which sits at
its module size ceiling) and shared by every lane:

* ``settle``: before a lane records its verdict. A dry run never becomes
  ``delivered``; a ``delivered`` without proof becomes ``failed`` (resumable);
  a review hold, a read-back failure, and every hold our side caused
  (``holds.classify``: gates, seed, update, upload, refusal, limit) page SMD;
  a proven delivery writes its
  evidence row (``DELIVERY_PROVEN``: ids, sizes, hashes, the review's status
  and spend).
* ``arm_reply_watch`` / ``check_reply_watches``: after the DELIVER wake. The
  turn the wake starts is what emails the requester, and until now nothing
  checked that it did: a wake answered by a timeout after send was counted as
  sent (``dispatch_wakes``), and a turn that never replied left the job green.
  The watch reads the job's bound replies through the broker until one is
  ``sent`` and was found in Sent Items by the send's own lookup; an ``unknown``
  send, or nothing by the deadline, pages SMD.
* ``wake_lost``: the DELIVER turn could not be started after every attempt.

Every alert goes through the broker's root-only ``delivery_proof_alert``, one
``DELIVERY_PROOF_FAILED`` audit row, which the overlay heartbeat pages on.
Alerts are de-duplicated per (code, attempt), and an open one is re-raised
once a day while the job stays where it stopped: the shortfall window is 24
hours, and a hold must not age out of the pager while the firm still waits.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .. import proof_mode
from . import PROOF_KEY, gate, holds

logger = logging.getLogger("medchron.daemon")

REPLY_DEADLINE_SECONDS = 20 * 60.0
#: After a hold in the wake turn, how long a redraft has before the watch pages.
#: The model's one redraft lands in seconds (A&P 20:29Z 2026-10-20: 8 s).
HELD_GRACE_SECONDS = 3 * 60.0
REALERT_SECONDS = 23 * 3600.0
#: The bound-reply kind each lane's jobs answer under (reply_binding.KINDS).
REPLY_KIND = {
    "medchron": "medchron_job",
    "demand": "demand_job",
    "drafting": "drafting_job",
    "litigation": "litigation_job",
    "negotiation": "negotiation_job",
    "casefile": "casefile_job",
    # An SMD note (medchron/note_by_smd.py), watched by the note lane.
    "note": "note_job",
}
#: A reason that begins with one of these is already a proof failure the
#: runner named; the daemon pages it under the same code.
_REASON_CODES = ("review_held", "readback_mismatch", "readback_unproven")


def in_proof_run(d: Any) -> bool:
    """A lane proof run's twin (``proof_run.twin``): its child runs with the
    proof flag and its ``dry_run`` is the expected ending, judged by
    ``proof_run.judge`` and reported to SMD there. Nothing here may page or
    record for it: its broker proxies reads to the real one, so an alert from a
    twin would land in the seat's real audit ledger as a false page."""
    env = getattr(d, "child_env", None) or {}
    return str(env.get(proof_mode.PROOF_ENV, "")).strip() == "1"


def _client(d: Any) -> Any:
    b = d.broker
    return getattr(b, "client", b)


def _attempt(d: Any, job_id: str) -> int:
    try:
        return max(1, int(d._daemon_state(job_id).get("attempts") or 1))
    except (TypeError, ValueError):
        return 1


def alert(d: Any, job_id: str, code: str, detail: str = "") -> bool:
    """One proof alert, de-duplicated per (code, attempt) within a day.
    Returns whether a row was written. Never raises: an alert that cannot be
    written is logged at ERROR and retried on the next occasion."""
    from ..broker_client import BrokerError

    if code not in gate.CODES:
        raise ValueError(f"unknown proof code {code!r}")
    if in_proof_run(d):
        return False
    attempt = _attempt(d, job_id)
    st = d._daemon_state(job_id)
    sent = dict(st.get("proof_alerts") or {})
    key = f"{code}:{attempt}"
    now = d.clock()
    if key in sent and now - float(sent[key]) < REALERT_SECONDS:
        return False
    try:
        _client(d)._request(
            {
                "action": "delivery_proof_alert",
                "lane": d.LANE,
                "job_id": job_id,
                "attempt": attempt,
                "code": code,
                "detail": str(detail or "")[:300],
            }
        )
    except (BrokerError, OSError, AttributeError, TypeError, ValueError) as exc:
        logger.error("%s job %s: could not raise the %s proof alert: %s", d.LANE, job_id, code, exc)
        return False
    sent[key] = now
    if d.job_dir(job_id).is_dir():
        d._write_state(job_id, proof_alerts=sent)
    logger.warning("%s job %s: proof alert %s raised (%s)", d.LANE, job_id, code, str(detail or "")[:200])
    return True


def _record_proven(d: Any, job_id: str, outcomes: list[Any]) -> None:
    from ..broker_client import BrokerError

    if in_proof_run(d):
        return

    files: list[dict[str, Any]] = []
    rows = 0
    review = "passed"
    model = ""
    for o in outcomes:
        proof = o.get(PROOF_KEY) if isinstance(o, dict) else None
        if not isinstance(proof, dict):
            continue
        files += [f for f in proof.get("files") or [] if isinstance(f, dict)]
        rows += int(proof.get("rows") or 0)
        if proof.get("review") == "released":
            review = "released"
        elif proof.get("review") == "code_only" and review == "passed":
            review = "code_only"
        model = model or str(proof.get("review_model") or "")
    try:
        _client(d)._request(
            {
                "action": "delivery_proof_record",
                "lane": d.LANE,
                "job_id": job_id,
                "attempt": _attempt(d, job_id),
                "review": review,
                "review_cents": gate.review_cents(outcomes),
                "review_model": model,
                "files": files,
                "rows": rows,
            }
        )
    except (BrokerError, OSError, AttributeError, TypeError, ValueError) as exc:
        logger.error("%s job %s: could not record its proof: %s", d.LANE, job_id, exc)


def _carried_alerts(d: Any, job_id: str, outcomes: list[Any]) -> None:
    """A delivered outcome may still carry codes its lane raised on the way
    (``proof.alerts``: e.g. SMD released a review hold, so the job filed with
    findings an attorney must read). Each known code pages once; an unknown
    one is logged, never raised, because a lane's typo must not lose the
    delivery it decorates."""
    raised: set[str] = set()
    for o in outcomes:
        proof = o.get(PROOF_KEY) if isinstance(o, dict) else None
        codes = proof.get("alerts") if isinstance(proof, dict) else None
        unit = str(o.get("unit") or o.get("kind") or "job") if isinstance(o, dict) else "job"
        for code in codes if isinstance(codes, list) else []:
            if code not in gate.CODES:
                logger.error("%s job %s: %s carries unknown proof alert %r", d.LANE, job_id, unit, code)
            elif code not in raised:
                raised.add(code)
                alert(d, job_id, code, f"{unit}: delivered, and its lane raised {code}")


def settle(d: Any, job_id: str, outcomes: list[Any], state: str, fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The state a lane may record, given its runner's outcomes. Mutates and
    returns ``fields`` (the reason). A proof run's twin passes through as it
    stands: its broker records nothing and its verdict is judged elsewhere."""
    if in_proof_run(d):
        return state, fields
    if any(isinstance(o, dict) and str(o.get("outcome") or o.get("verdict")) == "dry_run" for o in outcomes):
        # A dry run files nothing, so it can never be a delivery; on a lane whose
        # ledger has no resumable hold it is a (resumable) failure instead.
        state = "held" if d.LANE == "medchron" else "failed"
        fields["reason"] = "dry_run_reported: a dry run reached the daemon as a verdict; nothing was filed"
        alert(d, job_id, "dry_run_reported", "a dry run reported as a job verdict")
        for k in ("folder_id", "delivery", "covered", "notices", "files"):
            fields.pop(k, None)
        return state, fields
    if state == "delivered":
        problem = gate.check(outcomes)
        if problem is not None:
            code, detail = problem
            fields["reason"] = f"{code}: {detail}"[:500]
            for k in ("folder_id", "delivery", "covered", "notices", "files"):
                fields.pop(k, None)
            alert(d, job_id, code, detail)
            return "failed", fields
        _record_proven(d, job_id, outcomes)
        _carried_alerts(d, job_id, outcomes)
        return state, fields
    reason = str(fields.get("reason") or "")
    for code in _REASON_CODES:
        if reason.startswith(code) or reason.startswith(f"refused: {code}"):
            alert(d, job_id, code, reason)
            return state, fields
    if state == "held":
        # Every hold pages SMD here, with the technical reason, under the
        # hold's own code (proof/holds.py). On the chronology lane this page is
        # the only notice anyone gets: the firm hears nothing until delivery.
        # A page-allowance hold carries the figures SMD decides it on and
        # the two commands that decide it (``allowance.py``).
        from .. import allowance

        code = holds.classify(d.LANE, reason)[1]
        if code is not None:
            alert(d, job_id, code, allowance.on_hold(d, job_id, outcomes, reason) or reason)
    return state, fields


def _iso(epoch: float) -> str:
    """UTC ISO-8601 with a Z, the audit log's own ``ts`` shape."""
    return datetime.fromtimestamp(max(0.0, epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _requester(d: Any, job_id: str) -> str:
    try:
        env = json.loads((d.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(env.get("requested_by") or env.get("requester") or "")


def wake_owed(d: Any, state: str, reason: Any) -> bool:
    """Whether a job that ended in ``state`` gets a DELIVER wake.

    The chronology lane (the Captain, 2026-10-24): the firm hears from the
    Operator about a chronology ONLY when it is delivered. A held chronology
    gets no wake, so no turn exists that could word a hold to the firm; SMD is
    paged at ``settle``. The one held chronology that still wakes is
    ``rerouted_to_update``, for WORK: the turn resubmits the refused RUN as the
    UPDATE and sends nothing (the broker refuses a held chronology its reply),
    and ``check_reroutes`` pages when no UPDATE was queued. Every other lane,
    and every other ending, is unchanged."""
    if d.LANE != "medchron" or state != "held":
        return True
    return holds.classify("medchron", reason)[0] == holds.REROUTED_TO_UPDATE


def arm_reply_watch(d: Any, job_id: str) -> None:
    """After a DELIVER wake is consumed: watch for the requester's reply when
    one is owed (the job ended delivered or held, and it has a requester; a
    negotiation notice is always owed its one email). A held chronology is owed
    no reply (``wake_owed``): its only watch is the reroute's."""
    st = d._daemon_state(job_id)
    if st.get("state") not in ("delivered", "held"):
        return
    _arm_reroute(d, job_id, st)
    if d.LANE == "medchron" and st.get("state") == "held":
        return
    if not (st.get("notice") or _requester(d, job_id)):
        return  # a rehearsal submission: nobody is owed a reply
    d._write_state(
        job_id,
        reply_watch={
            "kind": REPLY_KIND[d.LANE],
            "due": d.clock() + REPLY_DEADLINE_SECONDS,
            "attempt": _attempt(d, job_id),
            "state": st.get("state"),
        },
    )


def _watched(d: Any) -> list[str]:
    if not d.jobs.is_dir():
        return []
    out = []
    for p in sorted(d.jobs.iterdir()):
        w = d._daemon_state(p.name).get("reply_watch")
        if isinstance(w, dict) and not w.get("done"):
            out.append(p.name)
    return out


def _rows_for(job_id: str, w: dict[str, Any], resp: dict[str, Any]) -> list[dict[str, Any]]:
    """The bound-reply rows that answer this watch. The broker names the key the
    job's reply is owed under (``expected_key``: its own ledger's attempt and
    state, the key ``reply_binding`` claims), and only that row counts. The
    daemon's ``attempts`` is NOT that number: it counts every start, a restart
    after a deploy included, and the chronology ledger has no attempt at all
    (always 1), so a watch keyed on it never matched a resumed job's reply and
    paged on a reply that went (2026-10-13). The daemon's own key is the
    fallback for a broker that predates ``expected_key``."""
    replies = [r for r in resp.get("replies") or [] if isinstance(r, dict)]
    expected = str(resp.get("expected_key") or "")
    if not expected:
        expected = f"{w.get('kind')}:{job_id}:{w.get('attempt') or 1}:{w.get('state')}"
    return [r for r in replies if str(r.get("key") or "") == expected]


def _arm_reroute(d: Any, job_id: str, st: dict[str, Any]) -> None:
    """A RUN the runner refused on a delivered matter (``rerouted_to_update``)
    pages nothing at settle: the DELIVER turn resubmits it as the UPDATE and
    sends the firm nothing. When that resubmission is refused nobody else will
    ever hear of it, so SMD MUST be paged (2026-10-13). This watch proves the
    UPDATE was queued, and pages when it was not."""
    if d.LANE != "medchron" or st.get("state") != "held":
        return
    if holds.classify("medchron", st.get("reason"))[0] != holds.REROUTED_TO_UPDATE:
        return
    try:
        env = json.loads((d.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        env = {}
    finished = float(st.get("finished_at") or d.clock())
    d._write_state(
        job_id,
        reroute_watch={
            "matter_number": str((env.get("matter") or {}).get("number") or ""),
            "since": _iso(finished - 60.0),
            "due": d.clock() + REPLY_DEADLINE_SECONDS,
        },
    )


def check_reroutes(d: Any) -> None:
    """Each tick: close a reroute watch once an UPDATE for the same matter is
    queued; page ``stage_refused`` when none is by the deadline."""
    from ..broker_client import BrokerError

    if not d.jobs.is_dir():
        return
    for p in sorted(d.jobs.iterdir()):
        st = d._daemon_state(p.name)
        w = st.get("reroute_watch")
        if not isinstance(w, dict) or w.get("done"):
            continue
        try:
            resp = _client(d)._request({"action": "medchron_job_status"})
        except (BrokerError, OSError, AttributeError, TypeError, ValueError) as exc:
            logger.warning("medchron job %s: job list unreadable (%s); reroute watch stays open", p.name, exc)
            continue
        queued = any(
            isinstance(j, dict)
            and str(j.get("id") or "") != p.name
            and str(j.get("matter_number") or "") == w.get("matter_number")
            and str(j.get("created_at") or "") >= str(w.get("since") or "")
            for j in resp.get("jobs") or []
        )
        if queued:
            w.update(done=True, outcome="update_queued")
        elif d.clock() >= float(w.get("due") or 0):
            alert(
                d,
                p.name,
                "stage_refused",
                "the runner refused a RUN on a delivered matter and no UPDATE was queued after the DELIVER "
                "wake; the firm has been told nothing",
            )
            w.update(done=True, outcome="paged")
        else:
            continue
        d._write_state(p.name, reroute_watch=w)


def check_reply_watches(d: Any) -> None:
    """Each tick: settle every open reply watch from the job's bound replies."""
    from ..broker_client import BrokerError

    for job_id in _watched(d):
        st = d._daemon_state(job_id)
        w = dict(st["reply_watch"])
        try:
            resp = _client(d)._request(
                {
                    "action": "delivery_reply_status",
                    "kind": w["kind"],
                    "job_id": job_id,
                    # An AgentMail seat has no bound replies; the broker counts its own
                    # dispatched sends to this requester since the watch was armed.
                    "requester": _requester(d, job_id),
                    "since": _iso(float(w.get("due") or 0) - REPLY_DEADLINE_SECONDS - 60.0),
                }
            )
        except (BrokerError, OSError, AttributeError, TypeError, ValueError) as exc:
            logger.warning("%s job %s: reply status unreadable (%s); watch stays open", d.LANE, job_id, exc)
            continue
        rows = _rows_for(job_id, w, resp)
        if not rows and resp.get("channel") == "agentmail" and int(resp.get("sends_to_requester") or 0) > 0:
            # AgentMail's strongest evidence: the broker's own dispatched send to
            # the requester (there is no Sent Items to read back). A Graph seat
            # never takes this path and stays on the stricter located proof.
            w.update(done=True, proven_at=d.clock(), outcome="dispatched")
            d._write_state(job_id, reply_watch=w)
            continue
        if any(r.get("outcome") == "sent" and r.get("located") is not False for r in rows):
            w.update(done=True, proven_at=d.clock())
            d._write_state(job_id, reply_watch=w)
            continue
        if any(r.get("outcome") == "unknown" for r in rows):
            alert(d, job_id, "reply_unproven", "the requester's reply failed in transport; it may or may not have gone")
            w.update(done=True, outcome="unknown")
            d._write_state(job_id, reply_watch=w)
            continue
        if any(r.get("outcome") == "sent" and r.get("located") is False for r in rows):
            # The broker already paged (reply_not_in_sent_items); close the watch.
            w.update(done=True, outcome="unlocated")
            d._write_state(job_id, reply_watch=w)
            continue
        armed = float(w.get("due") or 0) - REPLY_DEADLINE_SECONDS
        if int(resp.get("holds") or 0) > 0 and d.clock() >= armed + HELD_GRACE_SECONDS:
            # The send check held the turn's reply and nothing followed within
            # the grace (a redraft goes in seconds): page now, not at the
            # deadline (A&P 201167, 2026-10-20). `medchron rewake` re-sends it.
            alert(d, job_id, "reply_unproven", "the reply was held by the outgoing-email check and none followed")
            w.update(done=True, outcome="held")
            d._write_state(job_id, reply_watch=w)
            continue
        if d.clock() >= float(w.get("due") or 0):
            alert(
                d,
                job_id,
                "reply_unproven",
                f"no reply to the requester within {int(REPLY_DEADLINE_SECONDS // 60)} minutes of the DELIVER wake",
            )
            w.update(done=True, outcome="none_by_deadline")
            d._write_state(job_id, reply_watch=w)


def wake_lost(d: Any, job_id: str, outcome: str) -> None:
    alert(d, job_id, "wake_lost", f"the DELIVER wake failed every attempt ({outcome}); nobody was told")


__all__ = [
    "REALERT_SECONDS",
    "REPLY_DEADLINE_SECONDS",
    "REPLY_KIND",
    "alert",
    "arm_reply_watch",
    "check_reply_watches",
    "check_reroutes",
    "in_proof_run",
    "settle",
    "wake_lost",
    "wake_owed",
]
