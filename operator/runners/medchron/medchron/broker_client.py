"""The daemon's broker client: one JSON line over the broker's unix socket per
request. Split out of ``daemon.py`` so the chronology daemon and the demand lane
(``demand_lane.py``) speak through one client."""

from __future__ import annotations

import json
import socket
from typing import Any


class BrokerError(RuntimeError):
    pass


class BrokerClient:
    """The two verbs the daemon speaks: status (does the row exist) and record."""

    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        self.socket_path, self.timeout = socket_path, timeout

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(self.timeout)
                c.connect(self.socket_path)
                c.sendall(encoded)
                buf = bytearray()
                while not buf.endswith(b"\n"):
                    chunk = c.recv(65_536)
                    if not chunk:
                        break
                    buf.extend(chunk)
        except OSError as exc:
            raise BrokerError(f"broker socket: {exc}") from exc
        try:
            resp = json.loads(bytes(buf).decode() or "{}")
        except ValueError as exc:
            raise BrokerError("broker returned no JSON") from exc
        if resp.get("ok") is not True:
            raise BrokerError(f"broker refused: {resp.get('error')}: {resp.get('message')}")
        return resp

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self._request({"action": "medchron_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._request({"action": "medchron_job_record", "job_id": job_id, "state": state, "fields": fields})

    def allowance(self, exclude_job_id: str | None = None) -> dict[str, Any]:
        """The month's page allowance and spend as the broker counts them now.
        `exclude_job_id` leaves THIS job's own row out, so a resume does not
        meter a run against the cents it already recorded."""
        req: dict[str, Any] = {"action": "medchron_allowance"}
        if exclude_job_id:
            req["exclude_job_id"] = exclude_job_id
        return self._request(req)
