#!/usr/bin/env python3
"""mailbox-keeper pre-run: one broker call keeps the managed mailbox, no turn.

Class N (ADR 0050, doctrine Law 18): no model reads a message, decides a
thread or writes a word. The cron rows for this skill run this script, which
makes ONE broker call, ``mailbox_keeper_run`` with mode ``scheduled``, and
prints ``{"wakeAgent": false, ...}`` as its LAST stdout line, always. The
broker reads the inbox to its last page, trashes the settled SMD Ops alert
threads, and on the day's first run sends the one list to the skill's authored
``digest_recipient`` (``operator/workspace_broker/mailbox_keeper_verbs.py``).

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
# A full inbox read is one Gmail call per thread, broker-side; Hermes' own
# script budget is an hour, so this bounds the wait, not the work.
_TIMEOUT_SECONDS = 300.0
_COUNT_KEYS = (
    "pages",
    "threads",
    "settled",
    "trashed",
    "trash_failed",
    "open_alerts",
    "never_clears",
    "waiting",
    "listed_waiting",
    "listed_alerts",
)
#: The broker's statuses that mean the keeper did its job.
_OK_STATUSES = frozenset({"done", "already_sent_today"})


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
    return ("mailbox_keeper_ran" if status in _OK_STATUSES else "mailbox_keeper_failed"), extra


def main() -> int:
    try:
        from shared.workspace_broker import BrokerError, request
    except Exception as exc:  # noqa: BLE001 - no client is a verdict, never a crash or a wake
        print(json.dumps({"wakeAgent": False, "status": "client_unavailable", "error": type(exc).__name__}))
        return 0
    try:
        answer = request({"action": "mailbox_keeper_run", "mode": "scheduled"}, timeout=_TIMEOUT_SECONDS)
    except BrokerError as exc:
        basis, extra = "mailbox_keeper_failed", {"status": "broker_refused", "reason": _word(exc.code, 60)}
    except Exception as exc:  # noqa: BLE001 - an unreachable broker is a recorded verdict
        basis, extra = "mailbox_keeper_failed", {"status": "broker_unreachable", "error": type(exc).__name__}
    else:
        basis, extra = _verdict(answer)
    if not _heartbeat(request, basis, extra):
        sys.stderr.write("[pre_run] mailbox-keeper: " + basis + "; heartbeat not recorded\n")
    print(json.dumps({"wakeAgent": False, **extra}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
