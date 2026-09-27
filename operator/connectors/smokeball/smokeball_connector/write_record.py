"""Every write to a firm's Smokeball is recorded in the seat's audit ledger first.

WHY (A&P, found 2026-09-26). The ledger recorded the Operator's own tool calls,
but writes made by any OTHER process went nowhere: on 2026-09-25 SMD filed 52
scanned documents on 31 of the firm's matters by hand, in folders named "by A&P
Operator Agent", and on 2026-09-08 a chronology was filed the same way. Both used
this client from a script run over seat-probe, and neither left a row. The
Operator's own Smokeball writes left a row that named the tool but not the task,
event or file it changed.

THE RULE. Before any request that is not a read, this module appends a
``SMOKEBALL_WRITE`` row through the broker (``smokeball_write_append``) naming
WHO is writing, the method and path, the matter, and a digest of the body. If
that row cannot be written, the request is refused and nothing reaches
Smokeball. After the response, a second row records the status and the id the
vendor returned. A result row that fails to write cannot undo the write, so it
is reported on stderr instead of raised.

WHO IS WRITING. Decided from the process, never from anything the model sends:

* the Operator: this process's parent is the running gateway, read from
  ``/proc`` (its command line is ``... gateway run``). Not from the environment:
  Hermes starts MCP servers with none of the ``SMD_*`` variables. At boot the
  gateway's PID still runs ``bootstrap.sh``, which does not pass;
* anyone else: ``SMD_DIRECT_WRITE_ACTOR`` must name them. ``seat-probe.sh``
  sets it for every script it runs, and the boot/connect webhook reconciler sets
  its own. A process with neither is refused;
* the chronology runner is exempt when BOTH hold: it runs as the ``medchron``
  uid AND the daemon stamped ``MEDCHRON_DAEMON_JOB_ID`` on the job. It cannot
  reach the broker socket by design, and its daemon records each job as
  ``MEDCHRON_JOB_*`` rows (a delivery names the folder and every file). A person
  running as medchron by hand has no marker and is treated like anyone else.

OFF A SEAT (no ``SMD_AUDIT_BROKER_SOCKET`` and no socket at the seat's fixed
path: tests, local development) nothing is recorded and nothing is refused. The firm's credentials live only on the seat's
volume (ADR 0010), so a real write can only happen where the socket exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import socket
import sys
import uuid
from typing import Any

SOCKET_ENV = "SMD_AUDIT_BROKER_SOCKET"
#: operator/templates/fly.toml.template: the one broker socket on every seat.
DEFAULT_SOCKET = "/run/smd-workspace-broker/broker.sock"
_GATEWAY_MARK = "gateway run"
ACTOR_ENV = "SMD_DIRECT_WRITE_ACTOR"
VERB = "smokeball_write_append"
ACTION_TYPE = "SMOKEBALL_WRITE"
#: The chronology runner's own uid, exempt ONLY when the daemon launched this
#: job (it stamps MEDCHRON_JOB_MARKER_ENV in the job's filtered environment,
#: operator/runners/medchron/medchron/daemon.py). A person running as medchron by
#: hand has no marker and must name themselves like anyone else.
EXEMPT_USER = "medchron"
MEDCHRON_JOB_MARKER_ENV = "MEDCHRON_DAEMON_JOB_ID"
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: Above the broker's 5 s sqlite busy timeout (workspace_broker/audit_ledger.py),
#: so a slow lock is waited out rather than refused after the row landed.
_TIMEOUT_SECONDS = 10.0


class WriteNotRecorded(RuntimeError):
    """The audit row for this write could not be written, so the write was not sent."""


def _proc_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def _is_operator() -> bool:
    """True when this process is a child of the running gateway.

    Read from /proc, not the environment: Hermes starts MCP servers with a
    trimmed environment that carries NONE of the SMD_* variables (probed on
    pilot-smokeball 2026-09-26: the smokeball-mcp process, parent 654 = the
    gateway, held no SMD_ key at all). At boot the gateway's PID still runs
    bootstrap.sh, whose command line is not the gateway's, so a boot-time child
    does not pass as the Operator.
    """
    return _GATEWAY_MARK in _proc_cmdline(os.getppid())


def socket_path() -> str:
    """The broker socket: the environment's, else the seat's fixed path when it
    exists (the MCP server's environment lacks the variable), else ''."""
    configured = os.environ.get(SOCKET_ENV, "").strip()
    if configured:
        return configured
    return DEFAULT_SOCKET if os.path.exists(DEFAULT_SOCKET) else ""


def _user() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def _exempt() -> bool:
    return _user() == EXEMPT_USER and bool(os.environ.get(MEDCHRON_JOB_MARKER_ENV, "").strip())


def resolve_actor() -> str | None:
    """Who is making this write, or None when nobody has said."""
    if _is_operator():
        return "operator"
    named = os.environ.get(ACTOR_ENV, "").strip()
    return named[:200] if named else None


def _send(socket_path: str, row: dict[str, Any]) -> None:
    payload = json.dumps({"action": VERB, "row": row}, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(_TIMEOUT_SECONDS)
        client.connect(socket_path)
        client.sendall(payload)
        buf = bytearray()
        while not buf.endswith(b"\n"):
            chunk = client.recv(65_536)
            if not chunk:
                break
            buf.extend(chunk)
    reply = json.loads(buf)
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        message = reply.get("message") or reply.get("error") if isinstance(reply, dict) else None
        raise WriteNotRecorded(f"audit broker refused the row: {str(message or 'unknown error')[:160]}")


def body_digest(json_body: Any) -> str | None:
    if json_body is None:
        return None
    blob = json.dumps(json_body, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _returned_id(result: Any) -> str | None:
    """The id Smokeball gave the thing written. A file POST answers ``fileId``, a
    folder create can answer ``folderId`` (the 52 hand-filed documents were both)."""
    if isinstance(result, dict):
        for key in ("id", "Id", "fileId", "folderId", "documentId"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value[:80]
    return None


class WriteRecord:
    """One write's two rows. ``begin`` before the request, ``finish`` after it."""

    def __init__(self, method: str, path: str, matter_id: str | None, json_body: Any) -> None:
        self.socket_path = socket_path()
        self.active = bool(self.socket_path) and method.upper() not in _READ_METHODS and not _exempt()
        self.base = {
            "action_type": ACTION_TYPE,
            "write_id": uuid.uuid4().hex,
            "method": method.upper(),
            "path": path[:300],
            "matter_id": matter_id,
            "body_sha256": body_digest(json_body),
        }

    def begin(self) -> None:
        """Record the intent, or refuse the write."""
        if not self.active:
            return
        actor = resolve_actor()
        if actor is None:
            raise WriteNotRecorded(
                f"refused: this process is not the Operator and {ACTOR_ENV} does not name who is "
                "writing to the firm's Smokeball. Run it through operator/bin/seat-probe.sh, which "
                "sets it. Nothing was sent."
            )
        self.base["actor"] = actor
        try:
            _send(self.socket_path, {**self.base, "phase": "intent"})
        except WriteNotRecorded as exc:
            raise WriteNotRecorded(f"{exc}. Nothing was sent to Smokeball.") from exc
        except (OSError, ValueError) as exc:
            # The exception TYPE only, never its text: an OS socket message ("timed
            # out", "Connection refused") is on the overlay's connection-class list
            # (shared/connector_signatures.py), so it would page "Smokeball down" and
            # open the MCP circuit for reads too, over an audit-broker problem.
            raise WriteNotRecorded(
                f"refused: audit broker unreachable ({type(exc).__name__}); the row for this "
                "write could not be written. Nothing was sent to Smokeball."
            ) from exc

    def finish(self, status: int | None, result: Any = None, error: str | None = None) -> None:
        """Record the outcome. Never raises: the write has already happened."""
        if not self.active or "actor" not in self.base:
            return
        row = {**self.base, "phase": "result", "status": status, "returned_id": _returned_id(result)}
        if error:
            row["error"] = error[:300]
        try:
            _send(self.socket_path, row)
        except Exception as exc:  # noqa: BLE001 - the write is done; say so loudly and go on
            print(
                f"[smokeball] WARNING: write {self.base['write_id']} was sent but its result row "
                f"was not recorded: {exc}",
                file=sys.stderr,
            )


__all__ = ["ACTION_TYPE", "VERB", "WriteNotRecorded", "WriteRecord", "body_digest", "resolve_actor"]
