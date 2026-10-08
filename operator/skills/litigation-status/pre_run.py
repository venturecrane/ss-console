#!/usr/bin/env python3
"""litigation-status pre-run: submit today's scheduled litigation status job.

Class N (ADR 0050): no model composes anything and no turn is woken. The
weekday cron row for this skill (shipped commented out; uncommenting it is the
firm's enable act) runs this script, which makes ONE broker call,
``litigation_job_submit`` with the envelope ``{"trigger": "scheduled"}``, and
prints ``wakeAgent: false``. The broker fills the requester from the skill's
authored ``scheduled_recipients``, refuses unless the cron row is live, and
queues at most one scheduled job per Pacific date, so a re-run of this script
on the same day queues nothing. The job runs on the Machine; its completion
wake sends the one email.

WHAT STDOUT CARRIES. Hermes injects stdout into a woken turn's prompt; this
script never wakes one, and stdout carries the verdict, a status word and the
job id only. Never a matter, never a name.

EVERY RUN LEAVES A ROW. The outcome (queued, refused with the broker's reason
code, or the broker unreachable) is written as a SUPPRESSED_WAKE heartbeat row
through the agent-uid verb, so a scheduled day with no list is never traceless.

Self-contained on purpose: the scheduler stages this file ALONE into the
profile's scripts dir, so it imports nothing beside it.

Exit code is 0 whenever a verdict line was printed.
"""

from __future__ import annotations

import json
import os
import socket
import sys

SKILL = "litigation-status"
_TIMEOUT_SECONDS = 20


def _lit_socket_path() -> str:
    return os.environ.get("SMD_WORKSPACE_BROKER_SOCKET") or os.environ.get("SMD_AUDIT_BROKER_SOCKET") or ""


def _lit_broker(request: dict) -> dict:
    """One request on the broker's socket; the parsed answer, or raises."""
    path = _lit_socket_path()
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


def _lit_heartbeat(status: str, extra: dict) -> bool:
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
        return _lit_broker({"action": "suppressed_wake_append", "row": row}).get("ok") is True
    except Exception:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        return False


def _lit_reason_code(reason: str) -> str:
    """A short, content-free label for a refusal (the broker's sentences name
    no matter; the label keeps the row shorter still)."""
    text = reason.lower()
    for needle, code in (
        ("already queued", "already_queued_today"),
        ("already underway", "already_underway"),
        ("not enabled", "not_enabled"),
        ("budget", "budget"),
        ("scheduled_recipients", "recipient_unauthored"),
        ("filed", "filing_target_unauthored"),
        ("not switched on", "initiation_off"),
    ):
        if needle in text:
            return code
    return "refused"


def main() -> int:
    try:
        answer = _lit_broker({"action": "litigation_job_submit", "envelope": {"trigger": "scheduled"}})
    except Exception as exc:  # noqa: BLE001 - an unreachable broker is a recorded verdict
        status = "broker_unreachable"
        if not _lit_heartbeat(status, {"error": type(exc).__name__}):
            sys.stderr.write("[pre_run] litigation-status: broker unreachable; heartbeat not recorded\n")
        print(json.dumps({"wakeAgent": False, "status": status}, sort_keys=True))
        return 0
    if answer.get("ok") is True and answer.get("accepted") is True:
        status, extra = "litigation_job_queued", {"job_id": str(answer.get("job_id") or "")}
    elif answer.get("ok") is True:
        status = _lit_reason_code(str(answer.get("reason") or ""))
        extra = {"job_id": str(answer.get("job_id") or "")} if answer.get("job_id") else {}
    else:
        status, extra = "broker_error", {}
    if not _lit_heartbeat(status, extra):
        sys.stderr.write("[pre_run] litigation-status: " + status + "; heartbeat not recorded\n")
    print(json.dumps({"wakeAgent": False, "status": status, **extra}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
