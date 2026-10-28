#!/usr/bin/env python3
"""case-file-manager pre-run: submit this fire's scheduled case file jobs.

Class N (ADR 0050): no model composes anything and no turn is woken. The
skill's cron row (the firm's enable act; it ships nowhere until the firm says
yes) runs this script hourly on weekday mornings. It makes ONE broker call,
``casefile_job_submit`` with ``{"trigger": "scheduled",
"smokeball_token_age_seconds": <age or null>}``, and prints
``wakeAgent: false``. The broker decides the rest in the firm's own time zone
(``workspace_broker/casefile_schedule.py``): a business day or not (weekends
and the firm's authored ``holidays``), ``full`` on the month's first business
day and ``recheck`` otherwise, one job per authored caseload per day (a second
fire the same day queues nothing), a caseload whose last job is still running
deferred to the next hourly fire until the firm's cutoff, and a day that ends
with no job paged to SMD once. Each job runs on the Machine; its completion
wake sends that list's one email.

THE SMOKEBALL CONNECTION GATE. The age of the seat's durable Smokeball token
is read with the overlay's own reader (``shared.connector_check.token_ages``,
the heartbeat's field) and passed to the broker, which refuses to queue when
under seven days of the token's lifetime are left, or when the age is not
known (fail closed: this script sends ``null`` when the reader is missing or
fails), and pages SMD once per episode.

WHAT STDOUT CARRIES. The verdict, a status word, the firm date and per-caseload
statuses keyed by the caseload's hash. Never a matter, never a name.

EVERY RUN LEAVES A ROW. The outcome is written as one SUPPRESSED_WAKE row
through the agent-uid verb (``decision_basis`` ``casefile_scheduled:<status>``),
so no fire is traceless, and a paused day is a line in SMD's daily work digest.

Self-contained on purpose: the scheduler stages this file ALONE into the
profile's scripts dir, so it imports nothing beside it (``shared`` is the
installed overlay package, not a sibling file).

Exit code is 0 whenever a verdict line was printed.
"""

from __future__ import annotations

import json
import os
import socket
import sys

SKILL = "case-file-manager"
_TIMEOUT_SECONDS = 20


def _cfm_socket_path() -> str:
    return os.environ.get("SMD_WORKSPACE_BROKER_SOCKET") or os.environ.get("SMD_AUDIT_BROKER_SOCKET") or ""


def _cfm_broker(request: dict) -> dict:
    """One request on the broker's socket; the parsed answer, or raises."""
    path = _cfm_socket_path()
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


def _cfm_token_age() -> int | None:
    """Seconds since the Smokeball token file was last written, or None when
    it cannot be known. None is refused by the broker, never guessed."""
    try:
        from shared.connector_check import token_ages
    except Exception:  # noqa: BLE001 - no reader on this interpreter: unknown
        return None
    try:
        age = token_ages().get("smokeball")
    except Exception:  # noqa: BLE001 - a failed read is unknown, never zero
        return None
    return age if isinstance(age, int) and not isinstance(age, bool) and age >= 0 else None


def _cfm_heartbeat(status: str, extra: dict) -> bool:
    """The run's outcome as one SUPPRESSED_WAKE row. True only on ack."""
    metadata = {
        "decision_basis": f"casefile_scheduled:{status}",
        "platform": "cron-pre-run",
        "customer": os.environ.get("CUSTOMER_SLUG", ""),
    }
    metadata.update(extra)
    row = {
        "action_type": "SUPPRESSED_WAKE",
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": SKILL,
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
    try:
        return _cfm_broker({"action": "suppressed_wake_append", "row": row}).get("ok") is True
    except Exception:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        return False


def _cfm_verdict(answer: dict) -> tuple[str, dict]:
    """(status, the content-free fields kept) from the broker's answer."""
    if answer.get("ok") is not True:
        return "broker_error", {}
    extra: dict = {k: answer[k] for k in ("date", "mode") if isinstance(answer.get(k), str)}
    results = answer.get("results")
    if not isinstance(results, list):
        return str(answer.get("status") or "refused"), extra
    kept = [
        {k: r[k] for k in ("caseload", "status", "job_id") if isinstance(r, dict) and isinstance(r.get(k), str)}
        for r in results
    ]
    extra["caseloads"] = kept
    seen = {r.get("status") for r in kept}
    for status in ("missed", "queued", "deferred", "already_queued_today", "refused"):
        if status in seen:
            return status, extra
    return "refused", extra


def main() -> int:
    envelope = {"trigger": "scheduled", "smokeball_token_age_seconds": _cfm_token_age()}
    try:
        answer = _cfm_broker({"action": "casefile_job_submit", "envelope": envelope})
    except Exception as exc:  # noqa: BLE001 - an unreachable broker is a recorded verdict
        status = "broker_unreachable"
        if not _cfm_heartbeat(status, {"error": type(exc).__name__}):
            sys.stderr.write("[pre_run] case-file-manager: broker unreachable; heartbeat not recorded\n")
        print(json.dumps({"wakeAgent": False, "status": status}, sort_keys=True))
        return 0
    status, extra = _cfm_verdict(answer)
    if not _cfm_heartbeat(status, extra):
        sys.stderr.write("[pre_run] case-file-manager: " + status + "; heartbeat not recorded\n")
    print(json.dumps({"wakeAgent": False, "status": status, **extra}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
