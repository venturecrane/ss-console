"""Broker heartbeat writer for the escalator pre_run (SUPPRESSED/EMITTED_WAKE).

Split out of ``pre_run.py`` as a sibling module (the module-size ratchet:
``tests/operator-module-size.test.ts``), loaded by the same candidates walk as
the vendored ``escalation_ledger.py`` — the scheduler stages ``pre_run.py``
alone, and the skill dir on the volume carries the siblings.

FAILURE DIRECTION IS THE ALREADY-DESIGNED ONE. If this module cannot be
loaded, ``pre_run``'s ``_writer_factory`` returns None, and ``run_once``
treats None as "no writer wired" — every tick wakes
(``no_audit_writer_fail_open``), and the missing heartbeat trail is exactly
what the watcher-health view alarms on. Loud and safe, never silent.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket

_HEARTBEAT_TIMEOUT_SECONDS = 10


class BrokerSuppressedWakeWriter:
    """SuppressedWakeWriter over the broker's uid-gated heartbeat verbs.

    Two verbs, one per action_type — `suppressed_wake_append` for the quiet
    tick, `emitted_wake_append` for the firing one (#2253). The broker pins each
    verb to exactly one action_type, so neither can forge the other's row.
    """

    def __init__(self, socket_path: str, customer_slug: str) -> None:
        self._socket_path = socket_path
        self._customer_slug = customer_slug

    async def write_suppressed_wake(
        self,
        *,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        extra_metadata: dict | None = None,
    ) -> str:
        return self._append(
            verb="suppressed_wake_append",
            action_type="SUPPRESSED_WAKE",
            skill_name=skill_name,
            pre_run_inputs=pre_run_inputs,
            decision_basis=decision_basis,
            next_scheduled_at=next_scheduled_at,
            extra_metadata=extra_metadata,
        )

    async def write_emitted_wake(
        self,
        *,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        extra_metadata: dict | None = None,
    ) -> str:
        """Same payload shape, the wake-path verb. Raises like its sibling; the
        caller (`_try_write_emitted_wake`) is the one that swallows."""
        return self._append(
            verb="emitted_wake_append",
            action_type="EMITTED_WAKE",
            skill_name=skill_name,
            pre_run_inputs=pre_run_inputs,
            decision_basis=decision_basis,
            next_scheduled_at=next_scheduled_at,
            extra_metadata=extra_metadata,
        )

    def _append(
        self,
        *,
        verb: str,
        action_type: str,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        extra_metadata: dict | None,
    ) -> str:
        request = {
            "action": verb,
            "row": {
                "action_type": action_type,
                "actor": "agent",
                "actor_role": "agent",
                "skill_name": skill_name,
                "input_digest": hashlib.sha256(pre_run_inputs).hexdigest(),
                "metadata": json.dumps(
                    {
                        "decision_basis": decision_basis,
                        "next_scheduled_at": next_scheduled_at,
                        "platform": "cron-pre-run",
                        "customer": self._customer_slug,
                        **(extra_metadata or {}),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            },
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_HEARTBEAT_TIMEOUT_SECONDS)
            sock.connect(self._socket_path)
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        response = json.loads(raw.decode("utf-8"))
        if response.get("ok") is not True:
            raise RuntimeError(f"heartbeat rejected: {response}")
        return str(response.get("id", ""))


def writer_factory():
    """The live writer, or None when no broker socket is wired (dev mode)."""
    socket_path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not socket_path:
        return None  # run_once treats None as "no writer wired" -> wake
    return BrokerSuppressedWakeWriter(socket_path, os.environ.get("CUSTOMER_SLUG", ""))
