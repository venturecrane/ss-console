"""The ``litigation_*`` broker verbs: the request edge of the litigation status
job (one fresh status workbook of the firm's open litigation matters, filed to
the firm's own library matter), registered the way the drafting lane's are:
``verbs.py`` declares each verb's peer classes and checks them first, and
asserts at import that its litigation rows and ``VERBS`` below agree.

Peer gating, per verb:

    litigation_job_submit   two forms, told apart by the envelope's trigger:
                            ``request``   gateway PID (the agent's tool call;
                                          the overlay sets requester,
                                          message_ref and request_text from the
                                          turn's verified inbound) OR uid 0;
                            ``scheduled`` the weekday cron's pre_run (agent
                                          uid, NOT the gateway) OR uid 0. The
                                          envelope names no requester: the
                                          broker fills it from the authored
                                          ``scheduled_recipients``. From the
                                          agent uid it also requires the cron
                                          row to be LIVE (uncommented), so an
                                          execute_code turn cannot start a
                                          scheduled run the firm never enabled.
    litigation_job_status   gateway PID, agent uid, or uid 0. Root gets the
                            full row, and with no job id the Pacific month's
                            spend (``month_cents``), which the runner's lane
                            meters against the firm's budget, and the billing
                            cycle's status-list allowance (``list_allowance``),
                            which the lane checks before a list starts.
    litigation_job_record   uid 0 only (the runner daemon)
    litigation_job_resume   uid 0 only, with NO agent tool.
    litigation_job_close    uid 0 only, with NO agent tool: SMD ends a HELD
                            job it finished by hand (``held -> failed``,
                            once), so nothing on the seat answers it again.
                            Writes no queue file, no resume marker and no
                            wake; ``failed`` is the ending the reply binding
                            refuses. ``dry_run`` checks and writes nothing.

THE SUBMIT CHECKS, in order, none of which spends anything:
1. the envelope is one of the two input shapes;
2. the skill ``litigation-status`` is LISTED and ENABLED, and its
   ``initiation`` permits the trigger;
3. (scheduled, agent uid) the cron row for the skill is live;
4. the requester is a Named Administrator (``scope.admins``); for a scheduled
   run, the first authored ``scheduled_recipients`` entry must be one;
5. the filing target is authored: ``file_to_matter_id`` and ``folder_name``
   in the skill's settings and the firm's library matter number;
6. the firm's litigation config is staged and its monthly budget authored and
   not yet spent this Pacific month;
7. any attorneys named resolve, one each, against the firm's authored roster,
   and any paralegals named against its ``paralegals`` list. Both named: their
   staff ids, and a matter must match each. One side only, or "my cases"
   (``mine``): a people scope, every matter those people (the requester found
   by email in Smokeball's staff list) hold any staff role on, resolved by the
   runner (2026-10-24);
   (A staff reassignment, told apart by the email's own words in
   ``litigation_reassign``, skips 7: its people are read from those words,
   never the turn's scope, and resolved by the runner against the practice
   system's staff list. It needs the skill setting ``staff_reassignment:
   enabled`` and a shape the code can read, or nothing is queued.)
8. no unfinished job for the same scope, and this email (or this Pacific
   date's scheduled run) has not already queued one (re-checked in the
   ledger's write transaction). A FAILED requested run does not count: it
   delivered nothing and told nobody, so its email may ask again
   (2026-10-13); a failed scheduled run still holds its date.

THE CYCLE'S LIST ALLOWANCE (2026-10-24) is NOT a submit check. Like the
chronology's page allowance it is read when the job starts, by the lane
(``list_allowance`` on the root status view): a status list that would be past
the billing cycle's included lists is HELD there (``list_allowance:``, class
``smd_finishing``, SMD paged ``limit_held`` to quote it), never run and never
charged. After the firm accepts the quote, root resubmits the request with
``over_allowance_ref`` (the evidence id), which rides the row and lets that one
job run; only root may set it.

Audit rows carry ids, counts and states, never the request text and never a
matter fact.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import delivery_proof, hold_class, litigation_allowance
from .broker_context import BrokerContext
from .litigation_intake import (
    DEADLINES_FOLDER_KEY,
    FILE_TO_KEY,
    FOLDER_KEY,
    attorney_roster,
    cron_row_live,
    firm_config,
    initiation_allows,
    monthly_budget_cents,
    operator_library_number,
    paralegal_roster,
    requested_report,
    wants_discovery12,
    DISCOVERY12_KEY,
    DISCOVERY12_ON,
    named_attorneys,
    named_paralegals,
    scheduled_recipients,
    settings_of,
    skill_entry,
)
from .litigation_ledger import (
    AUDIT_TYPE,
    COUNT_FIELDS,
    MAX_REQUEST_TEXT,
    SKILL_NAME,
    STATES,
    CLOSE_REF,
    CLOSED_REASON,
    EnvelopeError,
    LitigationLedger,
    SubmitRefused,
    scope_key,
    validate_envelope,
)
from .litigation_reassign import REASSIGN_KEY, REASSIGN_ON, ReassignUnreadable, is_reassignment
from .litigation_reassign import parse as parse_reassignment
from .litigation_report import writes as report_writes
from .medchron_ledger import admins_from_customer_yaml

#: The audit types, spelled as literals here so a reader (and the producer
#: manifest's token check) finds them in this file; pinned to the ledger's.
AUDIT_TYPES = {
    "queued": "LITIGATION_JOB_SUBMITTED",
    "running": "LITIGATION_JOB_RUNNING",
    "held": "LITIGATION_JOB_HELD",
    "delivered": "LITIGATION_JOB_DELIVERED",
    "failed": "LITIGATION_JOB_FAILED",
}
#: A person asked the runner to re-run a failed job. NOT a transition.
RESUME_AUDIT_TYPE = "LITIGATION_JOB_RESUME_REQUESTED"
if AUDIT_TYPES != AUDIT_TYPE:
    raise RuntimeError("litigation_verbs.AUDIT_TYPES and litigation_ledger.AUDIT_TYPE disagree")

_PACIFIC = ZoneInfo("America/Los_Angeles")
_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
#: What a scheduled run's request_text says (it has no email).
SCHEDULED_TEXT = "scheduled weekday litigation status run"


class _Refused(ValueError):
    """A submit check that failed; the message is the sentence to relay."""


def _refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason, **extra}


VERBS = (
    "litigation_job_submit",
    "litigation_job_status",
    "litigation_job_record",
    "litigation_job_resume",
    "litigation_job_close",
)

_REQUEST_KEYS = {"trigger", "requester", "message_ref", "request_text", "scope"}
#: Scope keys only root may set (resolved ids, and the people scope, whose
#: requester is the verified sender the broker fills); a turn names people.
_STAFF_ID_KEYS = ("attorney_staff_ids", "paralegal_staff_ids", "people")
#: A turn's named scope: each key -> (roster reader, resolver, envelope key).
_NAMED_SCOPE = {
    "attorneys": (attorney_roster, named_attorneys, "attorney_staff_ids"),
    "paralegals": (paralegal_roster, named_paralegals, "paralegal_staff_ids"),
}


def pacific_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(_PACIFIC).date()


def month_window(now: datetime | None = None) -> tuple[str, str, str]:
    """("YYYY-MM", start, end) of the current Pacific calendar month, as UTC
    ISO strings in the ledger's format."""
    local = (now or datetime.now(timezone.utc)).astimezone(_PACIFIC)
    start = datetime(local.year, local.month, 1, tzinfo=_PACIFIC)
    nxt = (start + timedelta(days=32)).replace(day=1)

    def utc(dt: datetime) -> str:
        return (
            datetime(dt.year, dt.month, 1, tzinfo=_PACIFIC).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )

    return start.strftime("%Y-%m"), utc(start), utc(nxt)


class LitigationVerbs:
    def __init__(
        self,
        ledger: LitigationLedger | None,
        *,
        customer_yaml: str,
        audit_append: Any,
        firm_config_path: str | None = None,
        now: Any = None,
    ) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self.firm_config_path = firm_config_path
        self._audit_append = audit_append
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> LitigationVerbs:
        """Enabled only when the audit ledger is and the entrypoint exported
        the litigation queue dir (SMD_LITIGATION_QUEUE_DIR)."""
        writer = broker.ledger
        ledger = (
            LitigationLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None
        )

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("litigation verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(ledger, customer_yaml=str(broker.customer_path), audit_append=audit_append)

    @property
    def _db(self) -> LitigationLedger:
        if self.ledger is None:
            raise ValueError("litigation ledger not configured on this broker")
        return self.ledger

    def _audit(self, action_type: str, metadata: dict[str, Any]) -> None:
        self._audit_append(
            {
                "action_type": action_type,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "matter_ref": None,
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )

    # -- dispatch ------------------------------------------------------------
    def handle(
        self, action: str, request: dict[str, Any], peer_uid: int | None, *, from_gateway: bool = False
    ) -> dict[str, Any]:
        if self.ledger is None:
            raise ValueError("litigation ledger not configured on this broker")
        if action == "litigation_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "litigation_job_submit":
            return self._submit(request, peer_uid=peer_uid, from_gateway=from_gateway)
        if action == "litigation_job_record":
            return self._record(request)
        if action == "litigation_job_resume":
            return self._resume(request)
        if action == "litigation_job_close":
            return self._close(request)
        raise ValueError(f"unsupported litigation action: {action}")

    # -- status ---------------------------------------------------------------
    def month_cents(self, exclude: str = "") -> tuple[str, int]:
        month, start, end = month_window(self._now())
        return month, self._db.spend_between(start, end, exclude)

    def _status(self, request: dict[str, Any], *, full: bool) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        if job_id:
            row = self._db.read(job_id)
            if row is None:
                return {"ok": True, "job": None}
            job = {**LitigationLedger.project(row), **({"row": row} if full else {})}
            if job.get("state") == "held":  # the firm-safe token; the reason stays SMD's (2026-10-13)
                job["hold_class"] = hold_class.classify("litigation", job.get("reason"))[0]
            return {"ok": True, "job": job}
        out: dict[str, Any] = {"ok": True, "jobs": [LitigationLedger.project(r) for r in self._db.recent(20)]}
        if full:
            exclude = str(request.get("exclude_job_id") or "")
            month, cents = self.month_cents(exclude)
            budget = monthly_budget_cents(firm_config(self.firm_config_path))
            out.update({"month": month, "month_cents": cents, "monthly_budget_cents": budget})
            out["list_allowance"] = self.list_allowance(exclude)
        return out

    def list_allowance(self, exclude: str = "") -> dict[str, Any]:
        """The billing cycle's status-list allowance the lane checks before a
        list starts (``litigation_allowance``)."""
        return litigation_allowance.list_allowance(self.customer_yaml, self._db, self._now(), exclude)

    # -- submit ---------------------------------------------------------------
    def _input(self, raw: Any, *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        """The caller's envelope, checked for shape and peer. Returns the
        trigger and the fields the caller may set."""
        if not isinstance(raw, dict):
            raise EnvelopeError("litigation_job_submit requires an envelope object")
        trigger = raw.get("trigger")
        if trigger == "scheduled":
            if from_gateway:
                raise PermissionError(
                    "a scheduled litigation run is submitted by its cron's pre_run or root, never a turn"
                )
            # 2026-10-29: root may author a scheduled run's request text (SMD
            # sending the firm a list nobody emailed for, e.g. "court columns
            # only"); the cron's pre_run never does, and the text only narrows
            # what is read (the runner's ``court_only``), never who is told.
            if peer_uid == 0 and set(raw) == {"trigger", "request_text"}:
                text = raw["request_text"]
                if not isinstance(text, str) or not text.strip() or len(text) > MAX_REQUEST_TEXT:
                    raise EnvelopeError(f"request_text must be 1 to {MAX_REQUEST_TEXT} characters")
                return {"trigger": "scheduled", "request_text": text}
            if set(raw) != {"trigger"}:
                raise EnvelopeError('a scheduled envelope carries only {"trigger": "scheduled"}')
            return {"trigger": "scheduled"}
        if trigger != "request":
            raise EnvelopeError('trigger must be "request" or "scheduled"')
        if not from_gateway and peer_uid != 0:
            raise PermissionError("a requested litigation run is submitted from an email turn or by root")
        if set(raw) != _REQUEST_KEYS:
            raise EnvelopeError(f"a request envelope carries exactly {sorted(_REQUEST_KEYS)}")
        scope = raw["scope"]
        if isinstance(scope, dict) and set(scope) & set(_STAFF_ID_KEYS) and peer_uid != 0:
            raise PermissionError("staff ids are named by root only; a turn names attorneys or paralegals")
        text = raw["request_text"]
        if isinstance(text, str) and len(text) > MAX_REQUEST_TEXT:
            raise EnvelopeError(f"request_text must be at most {MAX_REQUEST_TEXT} characters")
        return dict(raw)

    def _submit(self, request: dict[str, Any], *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        """The checks in the module docstring's order. Each helper raises
        ``_Refused`` with the sentence the skill relays; none spends anything."""
        over = request.get("over_allowance_ref")
        if over is not None and peer_uid != 0:
            raise PermissionError("over_allowance_ref is set by root only: SMD's record that the list was quoted")
        try:
            given = self._input(request.get("envelope"), peer_uid=peer_uid, from_gateway=from_gateway)
            settings, admins = self._seat_gates(given["trigger"], peer_uid)
            requester, message_ref, request_text, scope_in = self._who(given, settings, admins)
            report, extra = self._kind(given["trigger"], request_text, settings)
            library, file_to, folder = self._filing_target(settings, report)
            firm, cents = self._budget(spends=report in ("status", "discovery12"))
            envelope = {
                "trigger": given["trigger"],
                "requester": requester,
                "message_ref": message_ref,
                "request_text": request_text,
                # A reassignment's people come from the email's words (``extra``),
                # never the turn's scope; one runs at a time on the seat.
                "scope": {"all": True} if report == "reassign" else self._resolve_scope(scope_in, firm, requester),
                "file_to_matter_id": file_to,
                "file_to_matter_number": library,
                "folder_name": folder,
                **extra,
            }
            valid = validate_envelope(envelope)
            job_id = self._db.submit(valid, over_allowance_ref=None if over is None else str(over))
        except EnvelopeError as exc:
            return _refusal(str(exc))
        except _Refused as exc:
            return _refusal(str(exc))
        except SubmitRefused as exc:
            return _refusal(f"{exc}; nothing new was queued", **({"job_id": exc.job_id} if exc.job_id else {}))
        self._audit_submitted(job_id, valid, cents, over_allowance_ref=over)
        return {
            "ok": True,
            "accepted": True,
            "job_id": job_id,
            "state": "queued",
            "trigger": valid["trigger"],
            "report": valid.get("report") or "status",
            **({"workbooks": valid["workbooks"]} if valid.get("workbooks") else {}),
            **({"reassign": valid["reassign"]} if valid.get("reassign") else {}),
        }

    def _seat_gates(self, trigger: str, peer_uid: int | None) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Checks 2 to 4's seat half: the skill listed and enabled for this
        trigger, a scheduled run's cron row live, the admin list readable."""
        entry = skill_entry(self.customer_yaml)
        if entry is None:
            raise _Refused(
                "the litigation status lane is not enabled on this seat (litigation-status is not listed); "
                "nothing was queued"
            )
        if not initiation_allows(entry, trigger):
            word = "on request" if trigger == "request" else "on a schedule"
            raise _Refused(f"the litigation status list is not switched on {word} for this seat; nothing was queued")
        if trigger == "scheduled" and peer_uid != 0 and not cron_row_live(self.customer_yaml):
            raise _Refused("the weekday litigation status run is not enabled on this seat; nothing was queued")
        admins = admins_from_customer_yaml(self.customer_yaml)
        if not admins:
            raise _Refused(
                "the seat's administrator list (scope.admins) is empty or could not be read, so who may "
                "request a status list cannot be established; nothing was queued"
            )
        return settings_of(entry), admins

    def _who(
        self, given: dict[str, Any], settings: dict[str, Any], admins: tuple[str, ...]
    ) -> tuple[str, str, str, Any]:
        """(requester, message_ref, request_text, scope) for either trigger,
        the requester a Named Administrator in both."""
        if given["trigger"] == "scheduled":
            recipients = scheduled_recipients(settings)
            if not recipients or recipients[0] not in admins:
                raise _Refused(
                    "a scheduled run goes to the first authored scheduled_recipients entry, which must be "
                    "one of the firm's Named Administrators; none is, so nothing was queued"
                )
            ref = f"scheduled:{pacific_today(self._now()).isoformat()}"
            return recipients[0], ref, given.get("request_text") or SCHEDULED_TEXT, {"all": True}
        requester = str(given["requester"] or "").strip().lower()
        if requester not in admins:
            raise _Refused(
                "a status list may only be requested by one of the firm's Named Administrators, and the "
                "requester on this submission is not one of them; nothing was queued"
            )
        return requester, given["message_ref"], given["request_text"], given["scope"]

    @staticmethod
    def _kind(trigger: str, text: str, settings: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """(report, the envelope's extra keys) from the request's OWN words, in
        code. A reassignment is checked first: it is the only kind that writes
        to the firm's matters, so it must be switched on for the seat and its
        shape read exactly, or nothing is queued."""
        if trigger != "request":
            return "status", {}
        if is_reassignment(text):
            if str(settings.get(REASSIGN_KEY) or "").strip() != REASSIGN_ON:
                raise _Refused("moving cases between staff is not switched on for this seat; nothing was queued")
            try:
                return "reassign", {"report": "reassign", "reassign": parse_reassignment(text)}
            except ReassignUnreadable as exc:
                raise _Refused(str(exc)) from exc
        if wants_discovery12(text):
            if str(settings.get(DISCOVERY12_KEY) or "").strip() != DISCOVERY12_ON:
                raise _Refused("the discovery breakdown is not switched on for this seat; nothing was queued")
            return "discovery12", {"report": "discovery12"}
        report, books = requested_report(text)
        return report, ({"report": report, "workbooks": list(books)} if report == "deadlines" else {})

    def _filing_target(self, settings: dict[str, Any], report: str = "status") -> tuple[str, str, str]:
        """The library matter and the folder: ``folder_name`` for the status
        list, ``deadlines_folder_name`` for the deadlines report (each
        authored, neither defaulted)."""
        library = operator_library_number(self.customer_yaml)
        file_to = str(settings.get(FILE_TO_KEY) or "").strip()
        folder = str(settings.get(DEADLINES_FOLDER_KEY if report == "deadlines" else FOLDER_KEY) or "").strip()
        if not (library and _UUID.match(file_to) and folder):
            raise _Refused(
                "where the status list is filed is not authored on this seat (the library matter and its "
                "folder); nothing was queued"
            )
        return library, file_to, folder

    def _budget(self, *, spends: bool = True) -> tuple[dict[str, Any] | None, int]:
        """(the firm config, cents spent this Pacific month), or refused. The
        deadlines report spends nothing (no model call), so a spent month
        never refuses it; the authored config is still required (its rosters)."""
        firm = firm_config(self.firm_config_path)
        budget = monthly_budget_cents(firm)
        if budget is None:
            raise _Refused("the firm's litigation status budget is not authored on this seat; nothing was queued")
        month, cents = self.month_cents()
        if spends and cents >= budget:
            raise _Refused(f"this month's litigation status budget is spent ({month}); nothing was queued")
        return firm, cents

    @staticmethod
    def _place(scope_in: dict[str, Any], firm: dict[str, Any] | None) -> dict[str, dict[str, str]]:
        """``{envelope id key: {roster name: staff id}}`` for every name the turn
        passed, each checked against the firm's rosters.

        A name passed on one side that is on NO roster of that side but
        names exactly one person on the other side's roster is that person,
        on that side: the rosters are authored lists of different people, so
        "Irma's files" passed as an attorney is still Irma's files. Unknown on
        both sides, or ambiguous, refuses with the passed side's sentence."""
        found: dict[str, dict[str, str]] = {}
        for key in sorted(k for k in scope_in if k in _NAMED_SCOPE):
            names = scope_in[key]
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise _Refused(f"scope.{key} must be a list of names; nothing was queued")
            other = next(k for k in _NAMED_SCOPE if k != key)
            for name in names:
                side = key
                hits, why = _NAMED_SCOPE[key][1]([name], _NAMED_SCOPE[key][0](firm))
                if why and " is not a" in why:
                    alt, alt_why = _NAMED_SCOPE[other][1]([name], _NAMED_SCOPE[other][0](firm))
                    if not alt_why:
                        side, hits, why = other, alt, None
                if why:
                    raise _Refused(why)
                found.setdefault(_NAMED_SCOPE[side][2], {}).update(dict(hits))
        return found

    @classmethod
    def _resolve_scope(cls, scope_in: Any, firm: dict[str, Any] | None, requester: str = "") -> Any:
        """The turn's named scope, checked against the firm's rosters (a typo
        is refused here, before anything is queued), as the envelope carries it:

        * people on ONE side only, and/or ``mine`` (2026-10-24, the Captain's
          rule: one person's files are every matter that person holds ANY
          staff role on): ``{"people": {"names": [roster names], "requester":
          <the verified sender when mine, else null>}}``. The runner finds every
          Smokeball record of each person and of the requester by email.
        * attorneys AND paralegals named: their staff ids, and a matter must
          match each (``attorney_staff_ids`` + ``paralegal_staff_ids``), as before.

        ``mine`` with ``all``, or with both sides named, is refused. Any other
        scope passes through for the envelope check."""
        if not isinstance(scope_in, dict) or not scope_in or not set(scope_in) <= {*_NAMED_SCOPE, "mine"}:
            if isinstance(scope_in, dict) and "mine" in scope_in:
                raise _Refused("ask for your own cases or for all of the firm's cases, not both; nothing was queued")
            return scope_in
        mine = scope_in.get("mine", False)
        if not isinstance(mine, bool):
            raise _Refused("scope.mine must be true or false; nothing was queued")
        found = cls._place(scope_in, firm)
        if len(found) == 2:
            if mine:
                raise _Refused(
                    "your own cases cannot be combined with both attorneys and paralegals named; name one side "
                    "with your own cases, or both sides without them; nothing was queued"
                )
            return {k: sorted(set(v.values())) for k, v in sorted(found.items())}
        names = sorted({n for side in found.values() for n in side})
        if not names and not mine:
            raise _Refused(f"no {'attorney' if 'attorneys' in scope_in else 'paralegal'} was named; nothing was queued")
        return {"people": {"names": names, "requester": requester if mine else None}}

    def _audit_submitted(
        self, job_id: str, valid: dict[str, Any], cents: int, *, over_allowance_ref: Any = None
    ) -> None:
        scope = valid["scope"]
        people = scope.get("people") or {}
        self._audit(
            AUDIT_TYPES["queued"],
            {
                "job_id": job_id,
                "trigger": valid["trigger"],
                "scope": "all"
                if scope.get("all")
                else "people"
                if people
                else "+".join(
                    n
                    for n, k in (("attorneys", "attorney_staff_ids"), ("paralegals", "paralegal_staff_ids"))
                    if scope.get(k)
                ),
                "report": valid.get("report") or "status",
                **({"reassign": valid["reassign"]} if valid.get("reassign") else {}),
                "attorney_count": len(scope.get("attorney_staff_ids") or []),
                "paralegal_count": len(scope.get("paralegal_staff_ids") or []),
                **({"people_count": len(people["names"]), "mine": bool(people["requester"])} if people else {}),
                "requested_by": valid["requester"],
                "request_ref": valid["message_ref"],
                "month_cents_used": cents,
                **({"over_allowance_ref": str(over_allowance_ref)} if over_allowance_ref is not None else {}),
            },
        )

    # -- the runner's report and a person's resume ---------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = request.get("fields")
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("litigation_job_record requires job_id, a known state, and a fields object")
        row = self._db.record(job_id, state, dict(fields))
        if row.get("prev_state") == state:
            return {"ok": True, "job": LitigationLedger.project(row)}
        projected = LitigationLedger.project(row)
        meta: dict[str, Any] = {
            "job_id": job_id,
            "state": state,
            "trigger": row["trigger"],
            "cents": row["cents"],
            "stage": row["stage"],
            "reason": row["reason"],
            "folder_id": row["folder_id"],
            **{k: row[k] for k in COUNT_FIELDS},
        }
        if projected["file"]:
            meta["file"] = {"name": projected["file"]["name"], "size": projected["file"]["size"]}
        if projected.get("counts"):
            meta["counts"] = projected["counts"]
        if "writes" in fields:  # a reassignment names every write it made to the firm's records, here
            meta["writes"] = report_writes(fields["writes"])
        self._audit(AUDIT_TYPES[state], meta)
        return {"ok": True, "job": projected}

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        """Write the resume marker the runner daemon reads; the row moves when
        the daemon actually starts (it records ``running``)."""
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        if not job_id:
            raise ValueError("litigation_job_resume requires job_id")
        if not reason:
            raise ValueError(
                "litigation_job_resume requires a reason: what was fixed, and why the done stages still hold"
            )
        row = self._db.read(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "failed":
            raise ValueError(f"job {job_id} is {row['state']}, not failed; only a failed job resumes")
        if row.get("close_ref"):
            raise ValueError(f"job {job_id} was closed by SMD ({row['close_ref']}); a closed job never runs again")
        twin = self._db.unfinished_for_scope(row["scope_key"])
        if twin is not None and twin != job_id:
            raise ValueError(f"job {twin} is already underway for this scope; resuming {job_id} would run it twice")
        again = self._db.live_for_ref(row["request_ref"], job_id)
        if again is not None:
            raise ValueError(
                f"job {again} was submitted since for the same request email; resuming {job_id} would answer it twice"
            )
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        marker = self._db.queue_dir / f".resume-{job_id}.json"
        marker.write_text(
            json.dumps(
                {"job_id": job_id, "reason": reason[:500], **delivery_proof.resume_extras(request)}, sort_keys=True
            ),
            encoding="utf-8",
        )
        self._audit(RESUME_AUDIT_TYPE, {"job_id": job_id, "reason": reason[:500]})
        return {"ok": True, "job_id": job_id, "queued": True}

    def _close(self, request: dict[str, Any]) -> dict[str, Any]:
        """SMD's close of a held job it finished by hand: the row moves
        ``held -> failed`` once and one LITIGATION_JOB_FAILED audit row records
        it in ids and tokens only (SMD's sentence stays on the job row's
        ``close_reason``). Nothing else is written: no queue file, no resume
        marker, no wake, so no completion turn exists to answer the requester,
        and the reply binding refuses a failed job if anything asks."""
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        source_ref = str(request.get("source_ref") or "").strip()
        if not job_id:
            raise ValueError("litigation_job_close requires job_id")
        if not reason:
            raise ValueError("litigation_job_close requires a reason: what SMD did, and how the requester was told")
        if not CLOSE_REF.match(source_ref):
            raise ValueError(
                "litigation_job_close requires source_ref: a crane_verify id (vfy_<ULID>) or an audit row id (ULID)"
            )
        row = self._db.read(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "held":
            raise ValueError(f"job {job_id} is {row['state']}; only a held job is closed by SMD")
        meta = {
            "job_id": job_id,
            "state": "failed",
            "prev_state": "held",
            "trigger": row["trigger"],
            "reason": CLOSED_REASON,
            "closed_by": "smd",
            "source_ref": source_ref,
        }
        if request.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "job_id": job_id, "from_state": "held", "audit": meta}
        after = self._db.close(job_id, reason, source_ref)
        self._audit(AUDIT_TYPES["failed"], meta)
        return {"ok": True, "dry_run": False, "job": LitigationLedger.project(after), "audit": meta}


def litigation_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = broker.litigation
    if verbs is None:
        raise ValueError("litigation verbs not configured on this broker")
    return verbs.handle(action, request, peer_uid, from_gateway=pid == broker.gateway_pid)


__all__ = [
    "AUDIT_TYPES",
    "RESUME_AUDIT_TYPE",
    "SCHEDULED_TEXT",
    "VERBS",
    "LitigationVerbs",
    "litigation_dispatch",
    "month_window",
    "pacific_today",
    "scope_key",
]
