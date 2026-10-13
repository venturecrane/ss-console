#!/usr/bin/env python3
"""connector-auth-check pre-run: the hourly Smokeball sign-in check, with no turn.

WHY (2026-10-13). A&P's Smokeball authorization died on Saturday 2026-10-10 and
nothing said so for 42 hours: connector_down needs calls to fail, and an idle
connection makes none. The agent-run version of this skill (SKILL.md) checks
once a day and spends a model turn doing it. This script is the same check as
code: ONE refresh grant at the vendor's auth host through the connector's own
client (``auth_status``), no data endpoint, no model, every hour.

Class N (ADR 0050): it never wakes a turn (``wakeAgent: false`` always). The
verdict is written as ONE SUPPRESSED_WAKE row through the broker's agent-uid
verb, ``decision_basis`` one of:

    connector_auth_ok:smokeball
    connector_auth_refused:smokeball:http_<4xx>     Smokeball refused the token
    connector_auth_unchecked:smokeball:<ErrorClass> the check could not run

Only a refusal is "dead"; a check that could not run says nothing about the
token, and is reported as exactly that (a false dead-token page is worse than
none, review 2026-10-13).

and the console's hourly work-health run (``operator/bin/work-health.py``) reads
that row back through the runtime-read seam: a failure is on team@ within the
hour, and a check that stops reporting is an item too. The row is also the
slot's wake row, so the cron-slot watchdog sees a missed check.

Rotation-safe: ``build_client_from_env`` passes the durable token file, so if
the vendor rotates on refresh the client persists the new token atomically,
exactly as the MCP server and the other pre_runs do. A persisted-token mismatch
(``refresh_token_persisted: false``) is a FAILURE: the connection works now and
bricks at the next restart.

WHAT STDOUT CARRIES: the verdict word only. Never a token, never a scope.

Self-contained on purpose: the scheduler stages this file ALONE.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

SKILL = "connector-auth-check"
SERVER = "smokeball"
_TIMEOUT_SECONDS = 45
_BROKER_TIMEOUT_SECONDS = 10
_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"

# Runs in the connector venv. Prints ONE json line: ok, or the error class.
_CHECK_SNIPPET = """\
import json, re
try:
    from smokeball_connector.client import SmokeballAuthError, build_client_from_env
except Exception as exc:
    print(json.dumps({"ok": False, "refused": False, "error": type(exc).__name__, "detail": "import failed"}))
    raise SystemExit(0)
try:
    status = build_client_from_env().auth_status()
    if status.get("refresh_token_persisted") is False:
        print(json.dumps({"ok": False, "refused": False, "error": "refresh_token_not_persisted", "detail": ""}))
    else:
        print(json.dumps({"ok": status.get("authenticated") is True, "refused": False, "error": "not_authenticated", "detail": ""}))
except SmokeballAuthError as exc:
    # The client never puts the response body in this message (it can echo the grant).
    found = re.search(r"rejected with HTTP (4[0-9][0-9])", str(exc))
    print(json.dumps({"ok": False, "refused": bool(found), "error": f"http_{found.group(1)}" if found else "SmokeballAuthError", "detail": str(exc)[:160]}))
except Exception as exc:
    print(json.dumps({"ok": False, "refused": False, "error": type(exc).__name__, "detail": str(exc)[:160]}))
"""


def check() -> tuple[str, str, str]:
    """(outcome, code, detail): outcome is ``ok``, ``refused`` (Smokeball's token
    endpoint answered 4xx: the token is dead) or ``unchecked`` (the check itself
    could not run, which says nothing about the token). Never conflate the last
    two: a false "dead" page is worse than none."""
    python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    if not Path(python).exists():
        return "unchecked", "connector_python_missing", python
    try:
        result = subprocess.run(  # noqa: S603 - connector-venv interpreter and a module-constant snippet; no shell
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the connector-venv interpreter from the Machine's own boot env (same trust domain as this file); the snippet is a module constant; no request data reaches argv.
            [python, "-c", _CHECK_SNIPPET],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return "unchecked", "timeout", f"no answer in {_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is reported as such
        return "unchecked", type(exc).__name__, str(exc)[:160]
    try:
        verdict = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "unchecked", f"exit_{result.returncode}", (result.stderr or "").strip()[-160:]
    if verdict.get("ok") is True:
        return "ok", "", ""
    code = "".join(ch for ch in str(verdict.get("error") or "unknown") if ch.isalnum() or ch in "_.-")[:64]
    detail = str(verdict.get("detail") or "")[:160]
    return ("refused" if verdict.get("refused") is True else "unchecked"), code or "unknown", detail


def _broker(request: dict) -> dict:
    path = os.environ.get("SMD_WORKSPACE_BROKER_SOCKET") or os.environ.get("SMD_AUDIT_BROKER_SOCKET") or ""
    if not path:
        raise RuntimeError("no broker socket in this environment")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(_BROKER_TIMEOUT_SECONDS)
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


def record(basis: str, detail: str = "") -> bool:
    """The verdict as one SUPPRESSED_WAKE row. True only on the broker's ack."""
    metadata = {
        "decision_basis": basis,
        "platform": "cron-pre-run",
        "customer": os.environ.get("CUSTOMER_SLUG", ""),
        "server": SERVER,
    }
    if detail:
        metadata["error_detail"] = detail
    # A run by hand (seat-probe, a falsifier) is marked so it never reads as
    # the cron having adopted the schedule.
    run_by = "".join(ch for ch in os.environ.get("SMD_PROBE_RUN_BY", "") if ch.isalnum() or ch in "_.:@-")[:64]
    if run_by:
        metadata["run_by"] = run_by
    row = {
        "action_type": "SUPPRESSED_WAKE",
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": SKILL,
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
    try:
        return _broker({"action": "suppressed_wake_append", "row": row}).get("ok") is True
    except Exception:  # noqa: BLE001 - a record failure is reported, never raised
        return False


def main() -> int:
    outcome, code, detail = check()
    if outcome == "ok":
        basis = f"connector_auth_ok:{SERVER}"
    elif outcome == "refused":
        basis = f"connector_auth_refused:{SERVER}:{code}"
    else:
        basis = f"connector_auth_unchecked:{SERVER}:{code}"
    if not record(basis, detail):
        sys.stderr.write(f"[pre_run] {SKILL}: {basis}; row not recorded\n")
    print(json.dumps({"wakeAgent": False, "status": outcome}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
