"""The scheduled half of ``casefile_job_submit``: one fire of the Case File
Manager's weekday cron, a job per authored caseload (the calendar, the token
gate and the page-once rule are ``casefile_schedule``'s; this module applies
them to the verb). Held apart from ``casefile_verbs`` for its size ceiling;
``CasefileVerbs`` mixes it in.

Peer: the cron's pre_run (agent uid, never the gateway) or root. From the
agent uid it also requires the cron row to be LIVE, so an execute_code turn
cannot start a schedule the firm never switched on.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from . import casefile_schedule as schedule, delivery_proof
from .broker_context import BrokerContext
from .casefile_ledger import AUDIT_TYPE, EnvelopeError, SubmitRefused, normalize_scope, scope_key, validate_envelope
from .litigation_intake import scheduled_recipients

FIRM_CONFIG_ENV = "SMD_CASEFILE_FIRM_CONFIG"
DEFAULT_FIRM_CONFIG = "/var/lib/smd-config/casefile-broker-firm.yaml"
_ENVELOPE_KEYS = {"trigger", "smokeball_token_age_seconds"}


class Refused(ValueError):
    """A submit check that failed; the message is the sentence to relay."""


def refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason, **extra}


def firm_config(path: str | None = None) -> dict[str, Any] | None:
    """The broker's copy of ``casefile-firm.yaml``, or None when absent or unreadable."""
    try:
        doc = yaml.safe_load(Path(path or os.environ.get(FIRM_CONFIG_ENV) or DEFAULT_FIRM_CONFIG).read_text("utf-8"))
    except Exception:  # noqa: BLE001 - absent config enables nothing
        return None
    return doc if isinstance(doc, dict) else None


class ScheduledSubmit:
    """Mixed into ``CasefileVerbs``, which provides ``_db``, ``_now``,
    ``broker``, ``customer_yaml``, ``_gates``, ``_filing``, ``_budget`` and
    ``_audit``."""

    _db: Any
    broker: BrokerContext | None
    customer_yaml: str
    _now: Any
    _gates: Any
    _filing: Any
    _budget: Any
    _audit: Any

    def _page(self, code: str, key: str, detail: str) -> bool:
        """One shortfall page per (code, key): SMD hears once, never daily."""
        if self.broker is None or not schedule.claim_page(self._db._db_path, code, key):
            return False
        ident = re.sub(r"[^0-9A-Za-z_-]", "-", f"scheduled-{code}-{key}")[:64]
        return bool(
            delivery_proof.append_alert(self.broker, lane="casefile", job_id=ident, code="stage_refused",
                                        detail=f"casefile_schedule_{code}: {detail}")
        )  # fmt: skip

    def token_gate(self, age: Any, settings: dict[str, Any]) -> str | None:
        """The refusal sentence when the Smokeball connection has under
        TOKEN_FLOOR_DAYS left (or its age is unknown), after paging SMD once
        for the episode; None when it passes, which ends the episode."""
        refused = schedule.token_refusal(age, schedule.lifetime_days(settings), self._now())
        if refused is None:
            schedule.clear_pages(self._db._db_path, "token")
            return None
        key, sentence = refused
        self._page("token", key, sentence)
        return sentence

    def _scheduled(self, raw: dict[str, Any], *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        """One fire of the weekday cron (``casefile_schedule``'s docstring)."""
        if from_gateway:
            raise PermissionError("a scheduled case file run is submitted by its cron's pre_run or root, never a turn")
        if set(raw) != _ENVELOPE_KEYS:
            return refusal(f"a scheduled envelope carries exactly {sorted(_ENVELOPE_KEYS)}", status="refused")
        try:
            settings, admins = self._gates(peer_uid, from_gateway, "scheduled")
            recipients = scheduled_recipients(settings)
            if not recipients or recipients[0] not in admins:
                raise Refused(
                    "a scheduled case file run goes to the first authored scheduled_recipients entry, which must "
                    "be one of the firm's Named Administrators; nothing was queued"
                )
            names = schedule.caseloads(settings)
            if not names:
                raise Refused("no caseload is authored for the Case File Manager's schedule; nothing was queued")
            cal = schedule.Calendar.from_firm(firm_config())
        except (Refused, schedule.ScheduleError) as exc:
            return refusal(str(exc), status="refused")
        local = cal.local(self._now())
        day, mode = local.date(), cal.mode_for(local.date())
        base = {"date": day.isoformat(), "mode": mode}
        if mode is None:
            return refusal(f"{day.isoformat()} is not a business day for the firm; nothing was queued",
                           status="not_business_day", **base)  # fmt: skip
        stop = self.token_gate(raw["smokeball_token_age_seconds"], settings)
        if stop is not None:
            return refusal(stop, status="token_gate", **base)
        try:
            filing = self._filing(settings)
            spend = self._budget()
        except Refused as exc:
            paged = self._page("refused", day.isoformat(), str(exc)[:200])
            return refusal(str(exc), status="refused", paged=paged, **base)
        past_cutoff = local.hour >= cal.cutoff_hour
        results = [self._one_caseload(name, day, mode, recipients[0], filing, past_cutoff, spend) for name in names]
        return {"ok": True, "accepted": any(r["status"] == "queued" for r in results), **base, "results": results}

    def _one_caseload(
        self, name: str, day: Any, mode: str, requester: str, filing: tuple[str, str, str],
        past_cutoff: bool, spend: tuple[str, int],
    ) -> dict[str, Any]:  # fmt: skip
        """One caseload's job for the day: queued, already queued today,
        deferred behind a job still running, or (past the cutoff) missed."""
        ref, key = schedule.scheduled_ref(day, name), schedule.caseload_key(name)
        conn = self._db._connect()
        try:
            today = schedule.job_for_ref(conn, ref)
        finally:
            conn.close()
        if today is not None:
            return {"caseload": key, "status": "already_queued_today", "job_id": today["id"]}
        scope = {"assisting": {"names": [name]}}
        twin = self._db.unfinished_for_scope(scope_key(normalize_scope(scope)))
        if past_cutoff:
            why = "deferred behind a job still running" if twin else "no job started before the cutoff"
            paged = self._page("missed", f"{day.isoformat()}:{key}", f"{day.isoformat()} caseload {key}: {why}")
            return {"caseload": key, "status": "missed", "paged": paged, **({"job_id": twin} if twin else {})}
        if twin:
            return {"caseload": key, "status": "deferred", "job_id": twin}
        library, file_to, folder = filing
        env = {
            "trigger": "scheduled",
            "mode": mode,
            "requester": requester,
            "message_ref": ref,
            "request_text": f"{schedule.SCHEDULED_TEXT} ({mode})",
            "scope": scope,
            "file_to_matter_id": file_to,
            "file_to_matter_number": library,
            "folder_name": folder,
        }
        try:
            job_id = self._db.submit(validate_envelope(env))
        except SubmitRefused as exc:
            # Lost a race with a twin fire or a request for the same caseload.
            return {"caseload": key, "status": "deferred", **({"job_id": exc.job_id} if exc.job_id else {})}
        except EnvelopeError as exc:
            return {"caseload": key, "status": "refused", "reason": str(exc)}
        self._audit(
            AUDIT_TYPE["queued"],
            {"job_id": job_id, "mode": mode, "trigger": "scheduled", "scope": "assisting", "caseload": key,
             "requested_by": requester, "request_ref": ref, "month": spend[0], "month_cents_used": spend[1]},
        )  # fmt: skip
        return {"caseload": key, "status": "queued", "job_id": job_id, "mode": mode}


__all__ = ["Refused", "ScheduledSubmit", "firm_config", "refusal"]
