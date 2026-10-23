"""A stub capability broker on a real Unix socket.

Lifted from ``tests/test_job_ledger_client.py`` (``_StubBroker``) and widened
for the harness: every newline-delimited JSON request is recorded, and the reply
is the canned response for its ``action`` or, for an action nobody scripted,
``GENERIC_RESPONSE``. The broker's own logic (grants, fencing, transmit) is
not under test here; what is under test is that a tool Hermes dispatched reached
the broker with the payload the model supplied.

``health`` answers the way a live broker answers the seat's gateway process
(``caller_is_gateway: true``), so the trust plugin's non-gateway wall
(``shared/gateway_identity.py``) admits this process whenever a test points
``SMD_AUDIT_BROKER_SOCKET`` here.
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Any

DEFAULT_RESPONSES: dict[str, dict[str, Any]] = {
    "health": {"ok": True, "jobs_ready": True, "caller_is_gateway": True},
    # The jobs plugin starts the seat's job worker (shared/job_worker_runtime),
    # which sweeps the ledger every 15 s on its own thread. An empty ledger
    # keeps it idle, so its sweeps never start an agent mid-test.
    "job_list_claimable": {"ok": True, "jobs": []},
    # The Workspace pair (shared/workspace_broker.py): the trust gate mints a
    # single-use grant in pre_tool_call, and the tool redeems it.
    "authorize": {"ok": True, "grant": "e2e-grant", "payload_digest": "sha256:e2e"},
    "execute": {
        "ok": True,
        "result": {"e2e": "executed"},
        "receipt": {
            "payload_digest": "sha256:e2e",
            "executed_at": "2026-01-01T00:00:00Z",
            "duration_ms": 1,
        },
    },
}

#: The reply to an action nobody scripted: success with an empty result, the
#: shape every verb's ``ok``/``result`` envelope shares.
GENERIC_RESPONSE: dict[str, Any] = {"ok": True, "result": None}


class StubBroker:
    """One request per connection, the broker's wire contract."""

    def __init__(self, sock_path: str, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.sock_path = sock_path
        self.responses: dict[str, dict[str, Any]] = {**DEFAULT_RESPONSES, **(responses or {})}
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(sock_path)
        self._srv.listen(16)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="e2e-stub-broker", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                buf = bytearray()
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65_536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                if not buf:
                    continue
                try:
                    req = json.loads(buf)
                except ValueError:
                    conn.sendall(b'{"ok": false, "error": "bad json"}\n')
                    continue
                with self._lock:
                    self.requests.append(req)
                action = req.get("action") if isinstance(req, dict) else None
                resp = self.responses.get(str(action), GENERIC_RESPONSE)
                conn.sendall(json.dumps(resp).encode() + b"\n")

    def mark(self) -> int:
        """The index the next request will land at, for "what arrived since"."""
        with self._lock:
            return len(self.requests)

    def since(self, mark: int) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.requests[mark:])

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._srv.close()
