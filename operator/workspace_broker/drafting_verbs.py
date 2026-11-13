"""The ``drafting_*`` broker verbs: the request edge of the drafting job (a
mediation brief, propounded discovery, discovery responses, a memo or a
deposition outline a firm administrator asks the Operator to draft).

A parallel copy of ``demand_verbs`` minus the re-run, registered the same way:
``verbs.py`` declares each verb's peer classes and checks them before handing
the request here, and asserts at import that its drafting rows and ``VERBS``
below agree. Like the ledger, it imports nothing from the demand lane: the two
intake helpers both lanes use are this lane's own copies in
``drafting_intake.py``, pinned to demand's behavior by a parity test.

Peer gating, per verb (identical to demand):

    drafting_job_submit    gateway PID (the agent's tool call; the overlay sets
                           requested_by / request_ref from the turn's verified
                           inbound, never from the model) OR uid 0
    drafting_job_status    gateway PID, agent uid, or uid 0. Root gets the full
                           row; everyone else the projection.
    drafting_allowance     gateway PID, agent uid, or uid 0.
    drafting_job_record    uid 0 only (the runner daemon)
    drafting_job_resume    uid 0 only, with NO agent tool.
    drafting_job_add_to_request  gateway PID (the words are read from the seat's
                           request index for the turn's verified email, never
                           from the model) OR uid 0; see drafting_additions.py.

THE SUBMIT CHECKS, in order, none of which spends anything:
1. the envelope is exactly ``drafting_ledger.validate_envelope``'s shape;
2. the skill ``document-drafter`` is LISTED and ENABLED on this seat;
3. a per-cycle allowance is authored (``drafting_allowance_per_cycle``);
4. the requester is a Named Administrator (``scope.admins``);
5. the document class is in the skill's ``enabled_classes`` (a class not
   listed is refused with a sentence the skill relays to the requester;
   checked after the requester, so a non-admin learns nothing of it);
6. the filing target is the matter itself or the authored rehearsal matter;
7. REVISE OR DRAFT (``_route``, 2026-11-04): a request email in the thread of
   a draft of its class DELIVERED on the matter revises that draft in place
   (the queue file carries ``revise``: the filed files by id, from the
   delivery proof); a thread the mailbox cannot read refuses, never a guess;
8. the cycle's allowance has a draft left (a revision counts no draft);
9. no unfinished draft of the same class on the matter (re-checked in the
   ledger's write transaction, with the request-email duplicate).

Audit rows carry ids, counts, classes and states, never the request text.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import delivery_proof, drafting_additions, library_setup_verbs
from .audit_ledger import _iso_utc
from .broker_context import BrokerContext
from .cycle_window import AnchorInvalid
from .drafting_intake import operator_library_number, resolve_request_graph_id
from .drafting_ledger import (
    ALLOWANCE_KEY,
    AUDIT_TYPE,
    CLASS_LABEL,
    CLASSES_KEY,
    DOCUMENT_CLASSES,
    REPORT_LISTS,
    SKILL_NAME,
    STATES,
    DraftingLedger,
    EnvelopeError,
    SubmitRefused,
    incoming_delivery,
    validate_envelope,
)
from .medchron_ledger import admins_from_customer_yaml, cycle_from_customer_yaml
from . import seat_config

#: The audit types, spelled as literals here so a reader (and the producer
#: manifest's token check) finds them in this file; pinned to the ledger's.
AUDIT_TYPES = {
    "submitted": "DRAFTING_JOB_SUBMITTED",
    "running": "DRAFTING_JOB_RUNNING",
    "held": "DRAFTING_JOB_HELD",
    "delivered": "DRAFTING_JOB_DELIVERED",
    "failed": "DRAFTING_JOB_FAILED",
}
#: A person asked the runner to re-run a failed job. NOT a transition.
RESUME_AUDIT_TYPE = "DRAFTING_JOB_RESUME_REQUESTED"
if AUDIT_TYPES != AUDIT_TYPE:
    raise RuntimeError("drafting_verbs.AUDIT_TYPES and drafting_ledger.AUDIT_TYPE disagree")

#: The skill setting naming the library matter's id (the same key and value
#: the demand lane authors), the one matter a rehearsal may file on.
REHEARSAL_MATTER_KEY = "rehearsal_matter_id"

_PACIFIC = ZoneInfo("America/Los_Angeles")

VERBS = (
    "drafting_job_submit",
    "drafting_job_status",
    "drafting_allowance",
    "drafting_job_record",
    "drafting_job_resume",
    "drafting_job_add_to_request",
)


def drafting_skill_settings(path: str | Path) -> dict[str, Any] | None:
    """The drafting skill's authored settings, or None unless the seat LISTS
    the skill and does not disable it. Omission is refusal (ADR 0035)."""
    try:
        doc = seat_config.load(Path(path), encoding="utf-8", on_missing="raise") or {}
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return None
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if not isinstance(skill, dict) or skill.get("name") != SKILL_NAME:
                continue
            if skill.get("enabled") is False:
                return None
            settings = skill.get("settings")
            return settings if isinstance(settings, dict) else {}
    return None


def allowance_of(settings: dict[str, Any] | None) -> int | None:
    value = (settings or {}).get(ALLOWANCE_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def enabled_classes_of(settings: dict[str, Any] | None) -> frozenset[str]:
    """The classes the seat switched on. Authored as a comma-separated string
    (skill settings are scalars, ADR 0075); a YAML list is read the same way.
    Unauthored is none; an unknown name switches nothing on."""
    value = (settings or {}).get(CLASSES_KEY)
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return frozenset()
    return frozenset(c.strip() for c in value if isinstance(c, str) and c.strip() in DOCUMENT_CLASSES)


class ThreadUnknown(RuntimeError):
    """The mailbox could not say which conversation a request email is in."""


def graph_thread_of(msgraph: Any) -> Callable[[str], str] | None:
    """The conversation id of an email by its internetMessageId, read from the
    seat's own mailbox (``msgraph_lookup.find_received``), or None on a seat
    with no Graph mailbox (no thread to compare by). Raises ``ThreadUnknown``
    when it could not look: "could not tell" must never read as "not a reply"."""
    if msgraph is None:
        return None

    def thread_of(internet_message_id: str) -> str:
        from . import msgraph_lookup

        try:
            found = msgraph_lookup.find_received(msgraph, internet_message_id)
        except Exception as exc:  # noqa: BLE001 - any failure to look is "unknown", never "none"
            raise ThreadUnknown(str(exc)[:200]) from None
        return str((found or {}).get("conversation_id") or "")

    return thread_of


class DraftingVerbs:
    def __init__(
        self,
        ledger: DraftingLedger | None,
        *,
        customer_yaml: str,
        audit_append: Any,
        thread_of: Callable[[str], str] | None = None,
    ) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self._audit_append = audit_append
        #: Revise-mode routing (2026-11-04): an email's conversation id.
        self.thread_of = thread_of
        #: How long ``drafting_job_add_to_request`` waits for the runner's daemon.
        self.add_wait_seconds = drafting_additions.WAIT_SECONDS

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> DraftingVerbs:
        """Enabled only when the audit ledger is and the entrypoint exported
        the drafting queue dir (SMD_DRAFTING_QUEUE_DIR)."""
        writer = broker.ledger
        ledger = (
            DraftingLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None
        )

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("drafting verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(
            ledger,
            customer_yaml=str(broker.customer_path),
            audit_append=audit_append,
            thread_of=graph_thread_of(getattr(broker, "msgraph", None)),
        )

    @property
    def _db(self) -> DraftingLedger:
        if self.ledger is None:
            raise ValueError("drafting ledger not configured on this broker")
        return self.ledger

    def _audit(self, action_type: str, metadata: dict[str, Any], matter_ref: str | None) -> None:
        self._audit_append(
            {
                "action_type": action_type,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "matter_ref": matter_ref,
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )

    # -- dispatch ------------------------------------------------------------
    def handle(self, action: str, request: dict[str, Any], peer_uid: int | None) -> dict[str, Any]:
        if self.ledger is None:
            raise ValueError("drafting ledger not configured on this broker")
        if action == "drafting_allowance":
            return self._allowance(request)
        if action == "drafting_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "drafting_job_submit":
            return self._submit(request, peer_uid)
        if action == "drafting_job_record":
            return self._record(request)
        if action == "drafting_job_resume":
            return self._resume(request)
        if action == "drafting_job_add_to_request":
            return drafting_additions.add_to_request(
                self._db, self._audit, request, wait=self.add_wait_seconds, now=_iso_utc
            )
        raise ValueError(f"unsupported drafting action: {action}")

    # -- allowance ------------------------------------------------------------
    def _allowance(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return {"ok": True, "authored": False, "invalid": True, "reason": str(exc)}
        exclude = str(request.get("exclude_job_id") or "")
        settings = drafting_skill_settings(self.customer_yaml)
        state = self._db.allowance(
            allowance_of(settings), anchor_day=anchor, effective_from=effective_from, exclude=exclude
        )
        month, cents = self._cents_this_month(exclude)
        return {
            "ok": True,
            **state,
            "enabled_classes": sorted(enabled_classes_of(settings)),
            "cents_used": cents,
            "cents_month": month,
        }

    def _cents_this_month(self, exclude: str, now: datetime | None = None) -> tuple[str, int]:
        """("YYYY-MM", cents) written in the current Pacific calendar month,
        without ``exclude`` (the runner's monthly spend budget's window)."""
        local = (now or datetime.now(timezone.utc)).astimezone(_PACIFIC)
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        nxt = (start + timedelta(days=32)).replace(day=1)

        def utc(dt: datetime) -> str:
            wall = datetime(dt.year, dt.month, 1, tzinfo=_PACIFIC)
            return wall.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        return start.strftime("%Y-%m"), self._db.spend_between(utc(start), utc(nxt), exclude)

    # -- status ---------------------------------------------------------------
    def _status(self, request: dict[str, Any], *, full: bool) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        if job_id:
            row = self._db.read(job_id)
            if row is None:
                return {"ok": True, "job": None}
            return {"ok": True, "job": row if full else DraftingLedger.project(row)}
        return {"ok": True, "jobs": [DraftingLedger.project(r) for r in self._db.recent(20)]}

    # -- submit ---------------------------------------------------------------
    def _filing_target_ok(self, envelope: dict[str, Any], settings: dict[str, Any]) -> bool:
        """The job files on the matter it reads, or on the authored rehearsal
        matter (its id in the skill's settings AND the firm's library number)."""
        file_to = envelope["file_to"]
        if file_to is None or file_to["id"] == envelope["matter"]["id"]:
            return True
        library = operator_library_number(self.customer_yaml)
        library_id = str(settings.get(REHEARSAL_MATTER_KEY) or "").strip().lower()
        return bool(
            library and library_id and file_to["number"] == library and file_to["id"].strip().lower() == library_id
        )

    def _route(self, envelope: dict[str, Any], revise_of: str, peer_uid: int | None) -> dict[str, Any] | str | None:
        """REVISE OR DRAFT, decided here from the job store, never by the model.

        A request is a revision when a brief of its class was DELIVERED on its
        matter and the request email is in that delivery's email thread (the
        same Graph conversation as the email that asked for the brief). Root
        may name the delivered job (``revise_of``: the ops workspace's
        rehearsal). Returns the runner's ``revise`` block, None for a fresh
        draft, or a refusal sentence. A thread that could not be read refuses:
        queuing a fresh draft on a guess is the $56 re-read of 2026-11-04."""
        delivered = self._db.delivered_on_matter(envelope["matter"]["id"], envelope["document_class"])
        if revise_of:
            if peer_uid != 0:
                return "revise_of is accepted from root only; nothing was queued"
            prior = next((r for r in delivered if r["id"] == revise_of), None)
            if prior is None:
                return f"job {revise_of} is not a delivered draft of this class on this matter; nothing was queued"
        else:
            if not delivered or self.thread_of is None:
                return None
            try:
                here = self.thread_of(envelope["request_ref"])
                prior = next((r for r in delivered if here and self.thread_of(r["request_ref"]) == here), None)
            except ThreadUnknown:
                return (
                    "this email could not be matched to the email thread of the draft already filed on the matter "
                    "(the mailbox did not answer), so it is not known whether it asks to revise that draft; "
                    "nothing was queued"
                )
            if prior is None:
                return None
        files = self._db.proven_files(prior["id"])
        if not files or not prior.get("folder_id"):
            return (
                f"the draft filed as job {prior['id']} cannot be identified on the matter by file id, so it cannot be "
                "revised in place; nothing was queued"
            )
        return {"prior_job_id": prior["id"], "folder_id": prior["folder_id"], "files": files}

    def _submit(self, request: dict[str, Any], peer_uid: int | None = None) -> dict[str, Any]:
        def refused(reason: str, **extra: Any) -> dict[str, Any]:
            return {"ok": True, "accepted": False, "reason": reason, **extra}

        raw = request.get("envelope") or {}
        try:
            envelope = validate_envelope(raw)
        except EnvelopeError as exc:
            return refused(str(exc))
        revise_of = str(raw.get("revise_of") or "") if isinstance(raw, dict) else ""
        settings = drafting_skill_settings(self.customer_yaml)
        if settings is None:
            return refused(
                "the drafting lane is not enabled on this seat (document-drafter is not listed); nothing was queued"
            )
        klass = envelope["document_class"]
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return refused(str(exc))
        state = self._db.allowance(allowance_of(settings), anchor_day=anchor, effective_from=effective_from)
        if not state["authored"]:
            return refused(f"no drafting allowance is authored for this seat ({ALLOWANCE_KEY}); nothing was queued")
        admins = admins_from_customer_yaml(self.customer_yaml)
        if not admins:
            return refused(
                "the seat's administrator list (scope.admins) is empty or could not be read, so who may "
                "request a draft cannot be established; nothing was queued"
            )
        if envelope["requested_by"] not in admins:
            return refused(
                "a draft may only be requested by one of the firm's Named Administrators, and the "
                "requester on this submission is not one of them; nothing was queued"
            )
        # After the requester check, so someone who may not ask never learns
        # which classes the firm has switched on.
        if klass not in enabled_classes_of(settings):
            label = CLASS_LABEL[klass]
            return refused(f"{label[0].upper()}{label[1:]} isn't switched on for your firm; nothing was queued")
        if not self._filing_target_ok(envelope, settings):
            return refused(
                "a draft is filed on the matter it reads, or on the firm's own authored "
                "library matter for a rehearsal; that filing target is neither, so nothing was queued"
            )
        revise = self._route(envelope, revise_of, peer_uid)
        if isinstance(revise, str):
            return refused(revise)
        if revise is not None:
            envelope = drafting_additions.with_carried_words(envelope, self._db.read(revise["prior_job_id"]))
        # A revision is the delivered draft finished as asked: no draft is
        # counted, so a spent allowance never blocks it (its spend is capped).
        if revise is None and state["remaining"] <= 0:
            return refused(
                f"this cycle's drafting allowance is spent ({state['used']} of {state['allowance']} in "
                f"{state['cycle']}); nothing was queued"
            )
        try:
            job_id = self._db.submit(
                envelope, allowance=state["allowance"], anchor_day=anchor, effective_from=effective_from, revise=revise
            )
        except SubmitRefused as exc:
            extra = {"job_id": exc.job_id} if exc.job_id else {}
            return refused(f"{exc}; nothing new was queued", **extra, **({"underway": True} if exc.underway else {}))
        return self._accepted(job_id, envelope, state["remaining"], revise)

    def _accepted(
        self, job_id: str, envelope: dict[str, Any], remaining: int, revise: dict[str, Any] | None
    ) -> dict[str, Any]:
        """The submitted row's audit and the answer the agent relays."""
        klass = envelope["document_class"]
        mode = {"mode": "revise" if revise else "draft", **({"revise_of": revise["prior_job_id"]} if revise else {})}
        self._audit(
            AUDIT_TYPES["submitted"],
            {
                "job_id": job_id,
                "matter_number": envelope["matter"]["number"],
                "file_to_other_matter": envelope["file_to"] is not None,
                "document_class": klass,
                "allowance_remaining": remaining,
                "requested_by": envelope["requested_by"],
                "request_ref": envelope["request_ref"],
                **mode,
            },
            envelope["matter"]["id"],
        )
        return {
            "ok": True,
            "accepted": True,
            "job_id": job_id,
            "state": "submitted",
            "document_class": klass,
            "allowance_remaining": remaining,
            **mode,
        }

    # -- the runner's report and a person's resume ---------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = request.get("fields")
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("drafting_job_record requires job_id, a known state, and a fields object")
        row = self._db.record(job_id, state, dict(fields))
        if row.get("prev_state") == state:
            return {"ok": True, "job": DraftingLedger.project(row)}
        meta: dict[str, Any] = {
            "job_id": job_id,
            "state": state,
            "document_class": row["document_class"],
            "cents": row["cents"],
            "reason": row["reason"],
            "folder_id": row["folder_id"],
        }
        # The STORED report after this record (merged in the ledger), counted
        # only when this record carried one.
        stored = json.loads(row["delivery_json"]) if row.get("delivery_json") else None
        delivery = stored if incoming_delivery(dict(fields)) is not None else None
        if isinstance(delivery, dict):
            meta["files"] = [
                {"name": f.get("name"), "size": f.get("size"), "role": f.get("role")}
                for f in (delivery.get("files") or [])
                if isinstance(f, dict)
            ][:20]
            # Counts only: a marker or a caption line can carry a client fact.
            for key in REPORT_LISTS:
                if isinstance(delivery.get(key), list):
                    meta[f"{key}_count"] = len(delivery[key])
        self._audit(AUDIT_TYPES[state], meta, row["matter_id"])
        return {"ok": True, "job": DraftingLedger.project(row)}

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        """Write the resume marker the runner daemon reads; the row moves when
        the daemon actually starts (it records ``running``)."""
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        if not job_id:
            raise ValueError("drafting_job_resume requires job_id")
        if not reason:
            raise ValueError(
                "drafting_job_resume requires a reason: what was fixed, and why the done stages still hold"
            )
        row = self._db.read(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "failed":
            raise ValueError(f"job {job_id} is {row['state']}, not failed; only a failed job resumes")
        twin = self._db.active_on_matter(row["matter_id"], row["document_class"])
        if twin is not None and twin != job_id:
            raise ValueError(f"job {twin} is already underway on this matter; resuming {job_id} would run it twice")
        again = self._db.live_for_ref(row["request_ref"], row["document_class"], job_id)
        if again is not None:
            raise ValueError(
                f"job {again} was submitted since for the same request email; resuming {job_id} would answer it twice"
            )
        held = _release_held(request, row)
        extras = {k: v for k, v in request.items() if k != "release_held"}
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        marker = self._db.queue_dir / f".resume-{job_id}.json"
        marker.write_text(
            json.dumps(
                {"job_id": job_id, "reason": reason[:500], **delivery_proof.resume_extras(extras), **held},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self._audit(RESUME_AUDIT_TYPE, {"job_id": job_id, "reason": reason[:500], **held}, row["matter_id"])
        return {"ok": True, "job_id": job_id, "queued": True}


def _release_held(request: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """``release_held``: SMD read a ledger brief held before it filed
    (``held_for_smd``) and files it as it stands. Root only, like every resume,
    so no email can talk a turn into it; who released it is named."""
    v = request.get("release_held", False)
    if not isinstance(v, bool):
        raise ValueError("release_held must be true or false")
    if not v:
        return {}
    if not str(row.get("reason") or "").startswith("held_for_smd:"):
        raise ValueError(f"job {row.get('id')} is not held for SMD before filing; release_held does not apply")
    by = str(request.get("by") or "").strip()
    if not by:
        raise ValueError("release_held requires by: who read the brief and released it")
    if request.get("release_review") or request.get("redo_review"):
        raise ValueError("release_held is its own decision; do not combine it with a review release or redo")
    return {"release_held": True, "by": by[:120]}


def drafting_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = broker.drafting
    if verbs is None:
        raise ValueError("drafting verbs not configured on this broker")
    if action in ("drafting_job_status", "drafting_job_record") and library_setup_verbs.owns(verbs, request):
        # A "set up our ..." job: the drafting lane's second kind, its own table.
        return library_setup_verbs.handle(verbs, action, request, peer_uid)
    envelope = request.get("envelope")
    if action == "drafting_job_submit" and isinstance(envelope, dict) and "request_graph_id" in envelope:
        # Root only; the drafting lane's copy of the demand lane's resolution.
        resolved = resolve_request_graph_id(broker, envelope, peer_uid)
        if "refused" in resolved:
            return {"ok": True, "accepted": False, "reason": resolved["refused"]}
        request = {**request, "envelope": resolved["envelope"]}
    return verbs.handle(action, request, peer_uid)


__all__ = [
    "VERBS",
    "DraftingVerbs",
    "allowance_of",
    "drafting_dispatch",
    "drafting_skill_settings",
    "enabled_classes_of",
]
