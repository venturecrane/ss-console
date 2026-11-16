"""The mailbox keeper's model calls, broker-side: one per thread, fenced.

WHY IN THE BROKER. A cron pre_run never receives the seat's model key: Hermes'
``build_subprocess_env`` scrubs provider keys from a script's environment
(probed on the seat 2026-11-16, vfy_01M7JJEB32QDGMMM22J9PCDC35). The broker
already reads the mail, so the reading happens here, with the seat's own key
materialized at boot into ``$SMD_MODEL_CREDENTIAL_PATH`` (``model.json``, 0600,
the broker's; ``operator/templates/entrypoint-mailbox-keeper.sh``), only on a
seat that authors the keeper's ``classify`` setting. The broker launches
through ``env -i`` and holds no key otherwise.

THE CALL is ``model_call.call`` (the client the sealed web reader uses): no
tools, thinking off, the authored ``classify_model``, one thread per call.

THE FENCES, all in code:
* per run: ``cost_fence_cents`` of the seat's ``mailbox-keeper`` settings,
  summed in FRACTIONAL cents (a 1.4-cent call counts 1.4, never 2). Before each
  call the run asks whether the largest call so far would still fit; when it
  would not, classifying stops and every remaining thread is "could not read".
* per seat-local day: the keeper's own spend ledger (``spend.json``) against
  the seat's ``safety.sticky_stop.cost_cap_daily_cents``. A dry run is charged
  here too, so a rehearsal loop cannot spend past the seat's day.
* the seat's cost breaker (``shared/cost_breaker.py``, ADR 0062) is asked by
  the pre_run before the broker call and charged after with the cents this run
  reports (the breaker's store sits on the hermes-only part of the volume,
  which the broker uid cannot reach). A pre_run that cannot ask it sends
  ``model: refused`` and nothing is read.
* three model errors in a row, or ``DEADLINE_SECONDS`` overall, stop the run.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import mailbox_keeper as mk
from . import mailbox_keeper_classify as mkc
from . import model_call

CREDENTIAL_ENV = "SMD_MODEL_CREDENTIAL_PATH"
CALL_SECONDS = 60.0
DEADLINE_SECONDS = 1_200.0
MAX_ERRORS_IN_A_ROW = 3


@dataclass
class Reading:
    outcomes: list[mkc.Outcome] = field(default_factory=list)
    cents: float = 0.0
    calls: int = 0
    #: "" when every thread was read; else the word that stopped or refused it.
    stopped: str = ""


def credential_path() -> Path | None:
    """The materialized key file, or None when the broker was launched without
    one (a seat that does not author ``classify``): nothing is read then."""
    found = os.environ.get(CREDENTIAL_ENV) or ""
    return Path(found) if found else None


def _unread(c: mkc.Candidate, reason: str) -> mkc.Outcome:
    return mkc.Outcome(thread_id=c.thread_id, message_id=c.message_id, kind="unread", reason=reason)


def _one(c: mkc.Candidate, settings: mk.Settings, key: str, rates: dict[str, Any]) -> tuple[mkc.Outcome, float]:
    """One call. Raises ModelCallError when no answer came back."""
    request = mkc.build_request(c, settings.classify_model)
    response = model_call.call(request, key=key, deadline=time.monotonic() + CALL_SECONDS)
    cents = model_call.usage_cents(response, rates)
    if response.get("stop_reason") == "refusal":
        return _unread(c, "refused"), cents
    doc = model_call.parse_json_object(model_call.response_text(response))
    return mkc.outcome(c, mkc.validate(doc) if doc is not None else "schema"), cents


def read_all(
    candidates: list[mkc.Candidate],
    settings: mk.Settings,
    *,
    day_room_cents: float,
    clock: Callable[[], float] = time.monotonic,
) -> Reading:
    """Read every candidate inside the fences (module docstring)."""
    out = Reading()
    key, rates = "", {}
    try:
        path = credential_path()
        if path is None:
            raise model_call.ModelCallError("no model credential path")
        key, table = model_call.load_credential(path)
        found = table.get(settings.classify_model)
        if isinstance(found, dict):
            rates = found
        else:
            out.stopped = "model_unpriced"
    except model_call.ModelCallError:
        out.stopped = "no_credential"
    if out.stopped:
        out.outcomes = [_unread(c, out.stopped) for c in candidates]
        return out
    fence = min(settings.cost_fence_cents, day_room_cents)
    started, largest, errors = clock(), 0.0, 0
    for c in candidates:
        if not out.stopped:
            if fence <= 0 or out.cents + largest > fence:
                out.stopped = "fence"
            elif clock() - started > DEADLINE_SECONDS:
                out.stopped = "deadline"
            elif errors >= MAX_ERRORS_IN_A_ROW:
                out.stopped = "model_errors"
        if out.stopped:
            out.outcomes.append(_unread(c, out.stopped))
            continue
        try:
            result, cents = _one(c, settings, key, rates)
        except model_call.ModelCallError:
            errors += 1
            out.outcomes.append(_unread(c, "model_error"))
            continue
        errors = 0
        out.calls += 1
        out.cents += cents
        largest = max(largest, cents)
        out.outcomes.append(result)
    if not out.stopped and errors and (errors >= MAX_ERRORS_IN_A_ROW or out.calls == 0):
        out.stopped = "model_errors"  # every call failed, or the run ended on a run of failures
    return out


__all__ = ["CREDENTIAL_ENV", "DEADLINE_SECONDS", "Reading", "credential_path", "read_all"]
