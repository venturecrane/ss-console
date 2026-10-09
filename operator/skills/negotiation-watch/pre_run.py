#!/usr/bin/env python3
"""negotiation-watch pre-run: submit this slot's scheduled negotiation watch job.

Class N (ADR 0050): no model composes anything and no turn is woken. The
weekday cron rows for this skill (the firm's enable act) run this script, which
makes ONE broker call, ``negotiation_job_submit`` with the envelope
``{"trigger": "scheduled"}``, and prints ``wakeAgent: false``. The broker fills
the requester from the skill's authored ``scheduled_recipients``, refuses unless
a cron row is live, and queues at most one job per cron slot (Pacific date and
hour) and one at a time, so a re-run of this script in the same slot queues
nothing. The job runs on the Machine; each new offer it enters gets its own
completion wake, which sends that offer's one email.

WHAT STDOUT CARRIES. Hermes injects stdout into a woken turn's prompt; this
script never wakes one, and stdout carries the verdict, a status word and the
job id only. Never a matter, never a name.

EVERY RUN LEAVES A ROW. The outcome (queued, refused with the broker's reason
code, or the broker unreachable) is written as a SUPPRESSED_WAKE heartbeat row
through the agent-uid verb, so a scheduled slot with no run is never traceless.

Self-contained on purpose: the scheduler stages this file ALONE into the
profile's scripts dir, so it imports nothing beside it.

Exit code is 0 whenever a verdict line was printed.
"""

from __future__ import annotations

import json
import os
import socket
import sys

SKILL = "negotiation-watch"
_TIMEOUT_SECONDS = 20


def _neg_socket_path() -> str:
    return os.environ.get("SMD_WORKSPACE_BROKER_SOCKET") or os.environ.get("SMD_AUDIT_BROKER_SOCKET") or ""


def _neg_broker(request: dict) -> dict:
    """One request on the broker's socket; the parsed answer, or raises."""
    path = _neg_socket_path()
    if not path:
        raise RuntimeError("no broker socket in this environment")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(_TIMEOUT_SECONDS)
        sock.connect(path)
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    answer = json.loads(raw.decode("utf-8"))
    if not isinstance(answer, dict):
        raise RuntimeError("the broker's answer is not an object")
    return answer


def _neg_heartbeat(status: str, extra: dict) -> bool:
    """The run's outcome as one SUPPRESSED_WAKE row. True only on ack."""
    metadata = {"decision_basis": status, "platform": "cron-pre-run", "customer": os.environ.get("CUSTOMER_SLUG", "")}
    metadata.update(extra)
    row = {
        "action_type": "SUPPRESSED_WAKE",
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": SKILL,
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
    try:
        return _neg_broker({"action": "suppressed_wake_append", "row": row}).get("ok") is True
    except Exception:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        return False


def _neg_reason_code(reason: str) -> str:
    """A short, content-free label for a refusal (the broker's sentences name
    no matter; the label keeps the row shorter still)."""
    text = reason.lower()
    for needle, code in (
        ("already queued", "already_queued_this_slot"),
        ("already underway", "already_underway"),
        ("not enabled", "not_enabled"),
        ("budget", "budget"),
        ("scheduled_recipients", "recipient_unauthored"),
        ("not switched on", "initiation_off"),
    ):
        if needle in text:
            return code
    return "refused"


def main() -> int:
    try:
        answer = _neg_broker({"action": "negotiation_job_submit", "envelope": {"trigger": "scheduled"}})
    except Exception as exc:  # noqa: BLE001 - an unreachable broker is a recorded verdict
        status = "broker_unreachable"
        if not _neg_heartbeat(status, {"error": type(exc).__name__}):
            sys.stderr.write("[pre_run] negotiation-watch: broker unreachable; heartbeat not recorded\n")
        print(json.dumps({"wakeAgent": False, "status": status}, sort_keys=True))
        return 0
    if answer.get("ok") is True and answer.get("accepted") is True:
        status, extra = "negotiation_job_queued", {"job_id": str(answer.get("job_id") or "")}
    elif answer.get("ok") is True:
        status = _neg_reason_code(str(answer.get("reason") or ""))
        extra = {"job_id": str(answer.get("job_id") or "")} if answer.get("job_id") else {}
    else:
        status, extra = "broker_error", {}
    if not _neg_heartbeat(status, extra):
        sys.stderr.write("[pre_run] negotiation-watch: " + status + "; heartbeat not recorded\n")
    print(json.dumps({"wakeAgent": False, "status": status, **extra}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
