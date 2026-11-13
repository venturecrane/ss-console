"""A local recording Unix-socket server standing in for the broker.

One server per input, in a thread of the per-input child. It records the exact
bytes the client put on the wire, then behaves per scenario:

  ok                a JSON object with ok true, newline-terminated, then close
  not-ok            a JSON object with ok false and an error, then close
  empty-reply       close without writing anything
  non-json          a line that is not JSON, then close
  fault-after-send  half a JSON object, then close (the reply is cut mid-write)
  no-socket         nothing listens and no socket file exists
  refused           a socket file exists but nothing accepts (bound, never listening)

The server stops reading the request at a newline, at EOF, or after a short
idle gap, so a client that neither shuts down its write side nor ends with a
newline is still served.
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Any

from .sandbox import original

REPLIES: dict[str, bytes | None] = {
    "ok": b'{"ok": true, "result": {"recorded": true}}\n',
    "not-ok": b'{"ok": false, "error": "refused-by-recorder"}\n',
    "empty-reply": b"",
    "non-json": b"this is not json\n",
    "fault-after-send": b'{"ok": tr',
}
IDLE = 0.4


class Recorder:
    def __init__(self, path: str, scenario: str) -> None:
        self.path = path
        self.scenario = scenario
        self.received: list[bytes] = []
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.scenario == "no-socket":
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)
        self._sock = sock
        if self.scenario == "refused":
            return
        sock.listen(4)
        sock.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _read_request(self, conn: socket.socket) -> bytes:
        conn.settimeout(IDLE)
        data = b""
        while b"\n" not in data:
            try:
                chunk = conn.recv(65536)
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            data += chunk
        return data

    def _serve(self) -> None:
        stop_at = 50  # 10 s of 0.2 s accept waits; the child's own timeout is the backstop
        while stop_at > 0 and self._sock is not None:
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                stop_at -= 1
                continue
            except OSError:
                return
            with conn:
                self.received.append(self._read_request(conn))
                reply = REPLIES[self.scenario]
                if reply:
                    try:
                        conn.sendall(reply)
                    except OSError:
                        pass

    def stop(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if os.path.exists(self.path):
            original("unlink")(self.path)

    def wire(self) -> list[dict[str, Any]]:
        """The recorded requests: the bytes as text (escaped where not UTF-8) and their length."""
        return [{"bytes": raw.decode("utf-8", "backslashreplace"), "len": len(raw)} for raw in self.received]
