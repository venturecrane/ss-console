"""The ``medchron_*`` broker verbs (routine 11, ss#2614).

Registered on the verb table in ``verbs.py``, which declares each verb's
peer classes (the same rows as the list below) and checks them before
handing the request here; ``_gate`` below re-checks with this module's own
message, as defence in depth. ``verbs.py`` asserts at import that its medchron
rows and this module's ``VERBS`` tuple agree, so the two cannot drift.

Peer gating, per verb:

    medchron_job_submit    gateway PID (an agent tool call) OR uid 0 (root on
                           the box: the rehearsal, the Captain-side skill)
    medchron_job_status    gateway PID, agent uid, or uid 0
    medchron_allowance     gateway PID, agent uid, or uid 0
    medchron_job_list      agent uid (the runtime-read gate process) or uid 0
    medchron_job_record    uid 0 only (the runner daemon)
    medchron_backfill_covered
                           uid 0 only, and with NO agent tool at all (ss#2834).
                           It writes what a delivered chronology covered, and an
                           update SKIPS whatever that record names -- so a path
                           the agent could reach is a path a client conversation
                           could use to make a chronology omit medical records.
    medchron_job_resume    uid 0 only, and with NO agent tool (ss#2903). Asks the
                           runner daemon to re-run a FAILED job from the stages
                           its state file has not finished, instead of paying for
                           a fresh one. Requires a reason and the stages the fix
                           touched; only a person knows a defect is fixed.

Every writing verb pins the audit type its transition maps to (``AUDIT_TYPE``),
so none can forge another row. Audit rows carry counts, digests and ids —
never the envelope, never a name.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .broker_context import BrokerContext
from .cycle_window import AnchorInvalid
from .medchron_ledger import (
    ALLOWANCE_KEY,
    AUDIT_TYPE,
    STATES,
    EnvelopeError,
    MedchronLedger,
    admins_from_customer_yaml,
    allowance_from_customer_yaml,
    cycle_from_customer_yaml,
    validate_envelope,
)

#: Addresses inside a free-text ``requested_by`` ("Christa Barrera
#: <christa@example.com>", "christa@example.com", "Christa, Firm LLP"). The
#: field is prose composed by the agent from the asking message, so the address
#: is extracted rather than assumed to be the whole value.
_EMAIL_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")

#: A backfilled row's `created_at`. Its own regex rather than the ledger's
#: private one, so a change to envelope date validation cannot quietly loosen
#: what a history write accepts.
_BACKFILL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VERBS = (
    "medchron_job_submit",
    "medchron_job_status",
    "medchron_allowance",
    "medchron_job_list",
    "medchron_job_record",
    "medchron_backfill_covered",
    "medchron_job_resume",
)

#: The coverage arrays, replaced by counts on the LIST paths. A matter can carry
#: several hundred document ids, and `project()` returns them three times over
#: (the raw `covered_json` plus both parsed keys), so twenty rows of them is
#: hundreds of kilobytes landing in a client-facing turn on a 1 vCPU / 1GB seat.
#: The arrays are what an update needs, and an update asks by matter or by job.
_COVERAGE_KEYS = ("covered_json", "covered_document_ids", "uncovered_document_ids")


def _counted(row: dict[str, Any]) -> dict[str, Any]:
    """One projected row with the id arrays swapped for their sizes.

    `None` stays `None` rather than becoming `0`: "nobody wrote down what was
    covered" and "nothing was covered" lead an update to opposite actions, and
    that distinction is the reason `project()` returns None in the first place.
    """
    out = {k: v for k, v in row.items() if k not in _COVERAGE_KEYS}
    for key, count_key in (
        ("covered_document_ids", "covered_count"),
        ("uncovered_document_ids", "uncovered_count"),
    ):
        ids = row.get(key)
        out[count_key] = None if ids is None else len(ids)
    return out


def medchron_dispatch(
    verbs: MedchronVerbs | None, action: str, request: dict[str, Any], peer_pid: int, peer_uid: int | None
) -> dict[str, Any]:
    """The server's one-line door. A broker built without the verbs (tests via
    ``__new__``, or an image whose entrypoint predates the queue) refuses."""
    if verbs is None:
        raise ValueError("medchron verbs not configured on this broker")
    return verbs.handle(action, request, peer_pid, peer_uid)


class MedchronVerbs:
    def __init__(
        self,
        ledger: MedchronLedger | None,
        *,
        customer_yaml: str,
        customer_slug: str,
        audit_append: Any,
        gateway_pid: int,
        resolve_agent_uid: Any,
    ) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self.customer_slug = customer_slug
        self._audit_append = audit_append
        self.gateway_pid = gateway_pid
        self._resolve_agent_uid = resolve_agent_uid

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> MedchronVerbs:
        """From a live Broker. The ledger is enabled only when the audit ledger
        is (a job that cannot be recorded must not be queued) and the entrypoint
        exported the queue dir; otherwise the verbs answer fail-closed."""
        ledger = None
        audit_writer = broker.ledger
        if audit_db_path and queue_dir and audit_writer is not None:
            ledger = MedchronLedger(audit_db_path, queue_dir)

        def audit_append(row: dict[str, Any]) -> Any:
            # Unreachable without a ledger: the job ledger above exists only
            # when the audit ledger does, and every verb refuses first when
            # the job ledger is absent. Refuse rather than AttributeError.
            if audit_writer is None:
                raise ValueError("medchron verbs have no audit ledger on this broker")
            return audit_writer.append(row)

        return cls(
            ledger,
            customer_yaml=str(broker.customer_path),
            customer_slug=broker.customer_slug,
            audit_append=audit_append,
            gateway_pid=broker.gateway_pid,
            resolve_agent_uid=broker._resolve_agent_uid,
        )

    @property
    def _db(self) -> MedchronLedger:
        """The ledger, narrowed.

        ``handle`` refuses before any of these methods run when the ledger is
        absent (a job that cannot be recorded must not be queued), but that
        guard does not narrow ``self.ledger`` for a type checker across a method
        call, so every use read as an attribute on ``None``. Raising here keeps
        the fail-closed behaviour identical if one is ever reached another way.
        """
        if self.ledger is None:
            raise ValueError("medchron ledger not configured on this broker")
        return self.ledger

    # -- gates ---------------------------------------------------------------
    def _is_agent(self, peer_uid: int | None) -> bool:
        agent_uid = self._resolve_agent_uid()
        return agent_uid is not None and peer_uid == agent_uid

    def _gate(self, action: str, peer_pid: int, peer_uid: int | None) -> None:
        is_root = peer_uid == 0
        is_gateway = peer_pid == self.gateway_pid
        ok = {
            "medchron_job_submit": is_gateway or is_root,
            "medchron_job_status": is_gateway or is_root or self._is_agent(peer_uid),
            "medchron_allowance": is_gateway or is_root or self._is_agent(peer_uid),
            "medchron_job_list": is_root or self._is_agent(peer_uid),
            "medchron_job_record": is_root,
            # ROOT only, and deliberately with no agent tool. This verb writes
            # what a delivered chronology covered, and an update SKIPS whatever
            # it says was covered -- so a path the agent could reach is a path a
            # client conversation could use to make a chronology omit medical
            # records. The runner's own reporting verb is root-only for the
            # adjacent reason.
            "medchron_backfill_covered": is_root,
            # ROOT only, and deliberately with no agent tool (ss#2903). A resume
            # spends money and writes to the firm's matter, and the judgment it
            # rests on -- "the defect is fixed, the completed stages are still
            # good" -- is one only a person can make. An Operator that could
            # resume its own failures is the loop the daemon already learned
            # about on 2026-08-31, when a cap-refused hold re-ran every tick.
            "medchron_job_resume": is_root,
        }.get(action, False)
        if not ok:
            raise PermissionError(f"{action} is not permitted for this caller")

    def _audit(self, action_type: str, metadata: dict[str, Any], matter_ref: str | None) -> None:
        self._audit_append(
            {
                "action_type": action_type,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": "medical-chronology-maintainer",
                "matter_ref": matter_ref,
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )

    # -- dispatch ------------------------------------------------------------
    def handle(self, action: str, request: dict[str, Any], peer_pid: int, peer_uid: int | None) -> dict[str, Any]:
        self._gate(action, peer_pid, peer_uid)
        if self.ledger is None:
            raise ValueError("medchron ledger not configured on this broker")
        if action == "medchron_allowance":
            exclude = str(request.get("exclude_job_id") or "") or None
            try:
                anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
            except AnchorInvalid as exc:
                # Authored-but-unreadable is refused, never quietly demoted to
                # the calendar month: a firm metered on a window it did not
                # author is the harm, and the key it must fix is named.
                return {"ok": True, "authored": False, "invalid": True, "reason": str(exc)}
            return {
                "ok": True,
                **self._db.allowance(
                    allowance_from_customer_yaml(self.customer_yaml),
                    exclude_job_id=exclude,
                    anchor_day=anchor,
                    effective_from=effective_from,
                ),
            }
        if action == "medchron_job_status":
            job_id = str(request.get("job_id") or "")
            matter_id = str(request.get("matter_id") or "")
            if job_id and matter_id:
                # Refused rather than resolved by precedence: a caller that sent
                # both does not know which answer it is getting, and silently
                # picking one would hand an update a delta measured against the
                # wrong thing.
                raise ValueError("medchron_job_status takes job_id or matter_id, not both")
            if job_id:
                row = self._db.read(job_id)
                return {"ok": True, "job": self._db.project(row) if row else None}
            if matter_id:
                return {"ok": True, "matter": self._db.delivered_coverage(matter_id)}
            return {"ok": True, "jobs": [_counted(self._db.project(r)) for r in self._db.list_recent(20)]}
        if action == "medchron_job_list":
            return {"ok": True, "jobs": [_counted(self._db.project(r)) for r in self._db.list_recent(200)]}
        if action == "medchron_job_submit":
            return self._submit(request)
        if action == "medchron_job_record":
            return self._record(request)
        if action == "medchron_backfill_covered":
            return self._backfill(request)
        if action == "medchron_job_resume":
            return self._resume(request)
        raise ValueError(f"unsupported medchron action: {action}")

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        """Ask the runner daemon to re-run a FAILED job from where it stopped.

        This writes a MARKER into the queue dir and nothing else. It cannot do
        the re-queue itself: `jobs/` is root:medchron 0710 and this process is
        workspace-broker (uid 10001, groups workspace-connectors and
        audit-readers), so it can neither read a job's envelope nor tell a wiped
        job dir from a present one. The daemon is root, owns `jobs/`, and is the
        only writer of daemon state -- the same division the sticky-stop release
        already uses. `.resume-` leads with a dot so the daemon's queue scan,
        which skips dotfiles, can never mistake one for an envelope.

        The ledger row is NOT moved here. The daemon records `running` when it
        actually starts, so a marker that never gets resolved (the job dir was
        wiped) leaves the row saying exactly what is true: still failed.
        """
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        redo = request.get("redo")
        if not job_id:
            raise ValueError("medchron_job_resume requires job_id")
        # ADR 0088: a park without a reason is the same stranding under a
        # friendlier name. A resume that does not say why is the same again.
        if not reason:
            raise ValueError(
                "medchron_job_resume requires a reason: what was fixed, and why the done stages still hold"
            )
        if not isinstance(redo, list) or any(not isinstance(s, str) or not s.strip() for s in redo):
            raise ValueError("medchron_job_resume requires redo: a list of stage names, [] when the fix reopens none")
        row = self._db.read(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "failed":
            raise ValueError(f"job {job_id} is {row['state']}, not failed; only a failed job resumes")
        # The refusal a person cannot reasonably work out alone: a LATER job for
        # the same work may already have delivered it. `active_duplicate` does
        # not cover this -- it exempts terminal rows and is only called on
        # submit -- and resuming the stale one re-uploads onto a matter that
        # already has the package.
        twin = self._db.work_twin(job_id)
        if twin is not None:
            raise ValueError(
                f"job {twin['id']} carries the same work and is {twin['state']}; "
                f"resuming {job_id} would deliver it twice"
            )
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        marker = self._db.queue_dir / f".resume-{job_id}.json"
        payload = {"job_id": job_id, "reason": reason[:500], "redo": [s.strip() for s in redo]}
        marker.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        self._audit(
            "MEDCHRON_JOB_RUNNING",
            {"job_id": job_id, "resume_requested": True, "reason": reason[:500], "redo": len(redo)},
            row["matter_id"],
        )
        return {"ok": True, "job_id": job_id, "queued": True, "redo": [s.strip() for s in redo]}

    def _submit(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            envelope = validate_envelope(request.get("envelope") or {})
        except EnvelopeError as exc:
            return {"ok": True, "accepted": False, "reason": str(exc)}
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return {"ok": True, "accepted": False, "reason": str(exc)}
        state = self._db.allowance(
            allowance_from_customer_yaml(self.customer_yaml),
            anchor_day=anchor,
            effective_from=effective_from,
        )
        if not state["authored"]:
            # The key is named because the seat that hits this is usually one
            # whose customer.yaml still carries only the pre-2026-09-09
            # document key: a firm cannot fix a key it is not told about.
            return {
                "ok": True,
                "accepted": False,
                "reason": f"no page allowance is authored for this seat ({ALLOWANCE_KEY}); nothing can be submitted",
            }
        # Order is deliberate. The two checks above say "this seat cannot do
        # this at all", which is ours or the firm's to fix and is worth naming
        # before anything about the requester. These two say "not you" and "not
        # twice". None of the four spends anything.
        refusal = self._requester_refusal(envelope)
        if refusal is not None:
            return {"ok": True, "accepted": False, "reason": refusal}
        twin = self._db.active_duplicate(envelope)
        if twin is not None:
            twin_id, twin_state = twin
            where = {
                "submitted": f"already queued as job {twin_id} and has not started yet",
                "running": f"already running as job {twin_id}",
                "held": f"already submitted as job {twin_id}, which is HELD and waiting on a decision",
            }.get(twin_state, f"already submitted as job {twin_id} (state {twin_state})")
            return {
                "ok": True,
                "accepted": False,
                "reason": f"this chronology package is {where}; nothing new was queued and the "
                "allowance was not charged twice",
                "job_id": twin_id,
                "job_state": twin_state,
            }
        if state["remaining"] <= 0:
            return {
                "ok": True,
                "accepted": False,
                "reason": f"the page allowance is spent ({state['used']:,} of "
                f"{state['allowance']:,} pages in {state['month']}); the Operator stops here "
                "and surfaces the item",
            }
        job_id = self._db.submit(envelope, remaining=state["remaining"])
        self._audit(
            AUDIT_TYPE["submitted"],
            {
                "job_id": job_id,
                "matter_number": envelope["matter"]["number"],
                "units": len(envelope["units"]),
                "allowance_remaining_pages": state["remaining"],
                "allowance_remaining_documents": state["remaining"],
                "requested_by": envelope.get("requested_by"),
                "request_ref": envelope.get("request_ref"),
            },
            envelope["matter"]["id"],
        )
        return {
            "ok": True,
            "accepted": True,
            "job_id": job_id,
            "state": "submitted",
            "unit": state["unit"],
            "allowance_remaining_pages": state["remaining"],
            # Kept for one release: the overlay's pinned tool relays this
            # key by name (ADR 0087 amendment).
            "allowance_remaining_documents": state["remaining"],
        }

    def _requester_refusal(self, envelope: dict[str, Any]) -> str | None:
        """None when a Named Administrator asked for this; a prose reason otherwise.

        WHY THIS IS HERE AND NOT ONLY IN THE SKILL BODY. The seat's initiation
        authority reaches the agent as a ``pre_llm_call`` prompt injection, not
        as a gate -- the overlay's initiation plugin says so in its own words,
        because there is no runtime skill identity at any tool boundary. For the
        self-test that is proportionate: the worst case is a wasted turn. This
        verb spends the firm's authored page allowance under a signed
        agreement, so the authorization has to hold against a model that did not
        follow the sentence, and that means checking it here.

        This is a check on a field the agent supplies, so it is defence in
        depth, not proof of identity: an agent that writes someone else's
        address still passes. What it buys is that the common failure -- a
        non-administrator's request carried faithfully into ``requested_by`` --
        refuses instead of billing, and the audit row carries the address that
        was claimed.
        """
        claimed = str(envelope.get("requested_by") or "").strip().lower()
        admins = admins_from_customer_yaml(self.customer_yaml)
        if admins is None:
            return (
                "the seat's administrator list (scope.admins) could not be read, so who may "
                "request a chronology package cannot be established; nothing was queued"
            )
        if not admins:
            return (
                "no Named Administrator is authored for this seat (scope.admins is empty), so "
                "no one may request a chronology package yet; nothing was queued"
            )
        found = _EMAIL_RE.findall(claimed)
        if not found:
            return (
                "requested_by must carry the requesting administrator's email address; "
                "a chronology package spends the firm's page allowance and the broker will not "
                "queue one it cannot attribute"
            )
        if not any(addr in admins for addr in found):
            return (
                "a chronology package may only be requested by one of the firm's Named "
                "Administrators, and the requester on this submission is not one of them; "
                "nothing was queued"
            )
        return None

    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = dict(request.get("fields") or {})
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("medchron_job_record requires job_id, a known state, and a fields object")
        wake = fields.pop("wake", None)  # audit-only metadata (a lost deliver wake), never a column
        row = self._db.record(job_id, state, fields)
        meta = {
            "job_id": job_id,
            "state": state,
            "documents": row["documents"],
            "pages": row["pages"],
            "cents": row["cents"],
            "reason": row["reason"],
            "folder_id": row["folder_id"],
        }
        if isinstance(wake, dict):
            meta["wake"] = {k: wake.get(k) for k in ("wake_failed", "outcome")}
        if state == "delivered" and isinstance(fields.get("delivery"), dict):
            d = fields["delivery"]
            meta["files"] = [
                {"name": f.get("name"), "sha256": f.get("sha256"), "bytes": f.get("bytes")}
                for f in (d.get("files") or [])
                if isinstance(f, dict)
            ][:50]
        self._audit(AUDIT_TYPE[state], meta, row["matter_id"])
        return {"ok": True, "job": self._db.project(row)}

    def _backfill(self, request: dict[str, Any]) -> dict[str, Any]:
        """Register what a pre-record chronology covered.

        Every A&P chronology delivered before 2026-09-17 has no coverage record,
        so an update on any of those matters refuses and the work falls back to
        people. The records are recomputable from the delivery's own artifacts at
        no AI cost; this is the door they come in through.

        The audit row is not optional bookkeeping. A write to live client state
        that leaves no governance trail is the 2026-09-01 sticky-stop incident,
        where a production state change was made by raw sqlite and the clear
        surface that would have logged it sat unused. Counts only in the
        metadata, never the ids: the audit log is not a copy of the record.
        """
        matter_id = str(request.get("matter_id") or "")
        matter_number = str(request.get("matter_number") or "")
        delivered_at = str(request.get("delivered_at") or "")
        source = str(request.get("source") or "")
        covered = request.get("covered")
        if not matter_id or not matter_number or not delivered_at or not source:
            raise ValueError("medchron_backfill_covered requires matter_id, matter_number, delivered_at and source")
        if not _BACKFILL_DATE_RE.match(delivered_at[:10]):
            # The real delivery date is load-bearing: it keeps the ledger
            # chronologically honest AND puts the row outside the current billing
            # cycle, which is the second of two independent reasons it cannot
            # move the firm's allowance.
            raise ValueError("delivered_at must start with a YYYY-MM-DD date")
        if not isinstance(covered, dict):
            raise ValueError("medchron_backfill_covered requires a covered object")
        row = self._db.backfill_delivered(
            matter_id=matter_id,
            matter_number=matter_number,
            covered=covered,
            delivered_at=delivered_at,
            source=source,
        )
        projected = self._db.project(row)
        self._audit(
            "MEDCHRON_COVERAGE_BACKFILLED",
            {
                "job_id": row["id"],
                "matter_number": row["matter_number"],
                "delivered_at": delivered_at,
                "source": source,
                "covered": len(projected.get("covered_document_ids") or []),
                "uncovered": len(projected.get("uncovered_document_ids") or []),
            },
            row["matter_id"],
        )
        return {"ok": True, "job": projected}
