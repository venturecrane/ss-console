#!/usr/bin/env python3
"""mailbox-keeper pre-run: one broker call keeps the managed mailbox, no turn.

Class N (ADR 0050, doctrine Law 18): no turn reads a message or writes a word.
The skill's hourly cron row runs this script, which makes ONE broker call,
``mailbox_keeper_run`` with mode ``scheduled``, and prints
``{"wakeAgent": false, ...}`` as its LAST stdout line, always. The broker files
SMD Ops mail every hour and, on the day's first run at or after the authored
hour, has a model read the remaining threads (only when the seat authors
``classify: true``) and sends the one list to the skill's authored
``digest_recipient`` (``operator/workspace_broker/mailbox_keeper_verbs.py``).

THE METER. The model call runs in the broker (this script never holds the key;
Hermes scrubs provider keys from a script's environment), but the seat's cost
breaker (``shared/cost_breaker.py``, ADR 0062) lives on the hermes-only part of
the volume, so this script asks it BEFORE the call and charges it AFTER with
the cents the broker reports. A breaker that is stopped, or that cannot be
built, sends ``model: refused``: the broker then reads nothing, and the list
falls back to the code rules.

WHAT STDOUT CARRIES. Counts and a status word only: never a sender, a subject
or a thread id. This script never wakes a turn, so nothing it prints reaches a
prompt.

EVERY RUN LEAVES A ROW. The outcome (done, a failed read, a refused send, the
broker unreachable) is written as a SUPPRESSED_WAKE heartbeat row through the
agent-uid verb, counts only, so a scheduled slot with no run is never
traceless and a failed read is visible as ``mailbox_keeper_failed``.

THE ONE BROKER CLIENT. The scheduler stages this file alone, so nothing beside
it is importable, but it runs under the gateway's own interpreter (Hermes'
``cron/scheduler_script.py``: ``[sys.executable, script]`` on POSIX), where the
overlay's ``shared`` package is installed (``operator/templates/Dockerfile``,
the overlay step). So the socket, its framing and its env name come from
``shared.workspace_broker`` rather than a sixteenth copy of the client. If that
import ever fails, the script still prints its verdict, ``client_unavailable``,
and wakes nothing; no heartbeat can be written then, and the work-health
triage reads the missing row.

Exit code is 0 whenever a verdict line was printed.
"""

from __future__ import annotations

import json
import sys

SKILL = "mailbox-keeper"
# A full inbox read is one Gmail call per thread, broker-side, and the day's
# list waits up to 90 s of jitter and up to 20 minutes of model reading
# (mailbox_keeper_model.DEADLINE_SECONDS); Hermes' own script budget is an
# hour, so this bounds the wait, not the work.
_TIMEOUT_SECONDS = 1800.0
_COUNT_KEYS = (
    "pages",
    "threads",
    "settled",
    "trashed",
    "trash_failed",
    "labelled",
    "label_failed",
    "needs_you",
    "waiting",
    "candidates",
    "still_open",
    "decisions",
    "receipts",
    "notices",
    "unread",
    "drafts",
    "filed",
    "file_skipped",
    "file_failed",
    "model_calls",
    "cost_cents",
)
#: The broker's statuses that mean the keeper did its job.
_OK_STATUSES = frozenset({"done", "already_sent_today", "busy"})


def _word(value: object, limit: int) -> str:
    """A content-free label: letters, digits, ``_`` and ``:`` only."""
    text = str(value or "")
    return text[:limit] if text and text.replace("_", "").replace(":", "").isalnum() else ""


def _heartbeat(request, basis: str, extra: dict) -> bool:
    """The run's outcome as one SUPPRESSED_WAKE row, counts only. True on ack."""
    metadata = {"decision_basis": basis, "platform": "cron-pre-run", **extra}
    row = {
        "action_type": "SUPPRESSED_WAKE",
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": SKILL,
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
    try:
        return request({"action": "suppressed_wake_append", "row": row}, timeout=20.0).get("ok") is True
    except Exception:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        return False


def _verdict(answer: dict) -> tuple[str, dict]:
    status = _word(answer.get("status"), 40) or "unknown"
    found = answer.get("counts")
    raw: dict = found if isinstance(found, dict) else {}
    counts = {k: raw[k] for k in _COUNT_KEYS if isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool)}
    extra = {"status": status, "mode": _word(answer.get("mode"), 12), **counts}
    reason = _word(answer.get("reason"), 60)
    if reason:
        extra["reason"] = reason
    classify = _word(answer.get("classify"), 24)
    if classify:
        extra["classify"] = classify
    return ("mailbox_keeper_ran" if status in _OK_STATUSES else "mailbox_keeper_failed"), extra


def _meter():
    """(the seat's cost breaker, "allowed"), or (None, "refused") when it is
    stopped or cannot be built: an unmetered model call is never made."""
    try:
        from shared import seat_config
        from shared.audit_client import audit_client_from_env
        from shared.cost_breaker import build_breaker

        slug = seat_config.slug(on_missing="none", alternate="first")
        if not slug:
            return None, "refused"
        breaker = build_breaker(
            customer=slug, persona="_machine", audit_client=audit_client_from_env(customer_slug=slug)
        )
        breaker.assert_allowed()
    except Exception:  # noqa: BLE001 - no meter, no model: the broker reads nothing
        return None, "refused"
    return breaker, "allowed"


def _charge(breaker, answer: dict) -> None:
    cents = (answer.get("counts") or {}).get("cost_cents") if isinstance(answer.get("counts"), dict) else None
    if breaker is None or not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        return
    try:
        breaker.record_cost_cents(cents)
    except Exception:  # noqa: BLE001 - a record failure is reported, never raised
        sys.stderr.write("[pre_run] mailbox-keeper: the cost breaker could not record " + str(cents) + " cents\n")


def main() -> int:
    try:
        from shared.workspace_broker import BrokerError, request
    except Exception as exc:  # noqa: BLE001 - no client is a verdict, never a crash or a wake
        print(json.dumps({"wakeAgent": False, "status": "client_unavailable", "error": type(exc).__name__}))
        return 0
    breaker, allowed = _meter()
    try:
        answer = request(
            {"action": "mailbox_keeper_run", "mode": "scheduled", "model": allowed}, timeout=_TIMEOUT_SECONDS
        )
    except BrokerError as exc:
        basis, extra = "mailbox_keeper_failed", {"status": "broker_refused", "reason": _word(exc.code, 60)}
    except Exception as exc:  # noqa: BLE001 - an unreachable broker is a recorded verdict
        basis, extra = "mailbox_keeper_failed", {"status": "broker_unreachable", "error": type(exc).__name__}
    else:
        _charge(breaker, answer)
        basis, extra = _verdict(answer)
    if not _heartbeat(request, basis, extra):
        sys.stderr.write("[pre_run] mailbox-keeper: " + basis + "; heartbeat not recorded\n")
    print(json.dumps({"wakeAgent": False, **extra}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
