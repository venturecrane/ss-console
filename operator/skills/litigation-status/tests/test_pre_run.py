"""litigation-status pre_run: one scheduled submit, never a wake, always a row.

Drives the script against a fake broker on a real unix socket. Each test fails
if the line it defends is removed: the envelope must be exactly the scheduled
form (no requester: the broker fills it), the verdict must never wake a turn,
and the outcome must land as a SUPPRESSED_WAKE row.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "pre_run.py"


def _load():
    spec = importlib.util.spec_from_file_location("litigation_status_pre_run", HERE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeBroker:
    def __init__(self, submit_answer: dict) -> None:
        self.dir = tempfile.mkdtemp(prefix="litpr")
        self.path = os.path.join(self.dir, "b.sock")
        self.requests: list[dict] = []
        self.submit_answer = submit_answer
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(4)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                raw = b""
                while not raw.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                req = json.loads(raw.decode())
                self.requests.append(req)
                answer = self.submit_answer if req["action"] == "litigation_job_submit" else {"ok": True}
                conn.sendall(json.dumps(answer).encode() + b"\n")


def _run(monkeypatch, capsys, broker: FakeBroker | None) -> dict:
    if broker is None:
        monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
        monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    else:
        monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", broker.path)
    assert _load().main() == 0
    return json.loads(capsys.readouterr().out.strip())


def test_a_queued_run_submits_the_scheduled_form_and_wakes_nothing(monkeypatch, capsys) -> None:
    broker = FakeBroker({"ok": True, "accepted": True, "job_id": "01JOB"})
    out = _run(monkeypatch, capsys, broker)
    assert out == {"wakeAgent": False, "status": "litigation_job_queued", "job_id": "01JOB"}
    submit, row = broker.requests
    assert submit == {"action": "litigation_job_submit", "envelope": {"trigger": "scheduled"}}
    assert row["action"] == "suppressed_wake_append"
    assert json.loads(row["row"]["metadata"])["decision_basis"] == "litigation_job_queued"


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("today's scheduled run already queued litigation job 01X; nothing new was queued", "already_queued_today"),
        ("the weekday litigation status run is not enabled on this seat; nothing was queued", "not_enabled"),
    ],
)
def test_a_refusal_is_recorded_and_wakes_nothing(monkeypatch, capsys, reason: str, status: str) -> None:
    broker = FakeBroker({"ok": True, "accepted": False, "reason": reason})
    out = _run(monkeypatch, capsys, broker)
    assert out["wakeAgent"] is False and out["status"] == status
    assert json.loads(broker.requests[-1]["row"]["metadata"])["decision_basis"] == status


def test_no_broker_is_a_verdict_not_a_crash(monkeypatch, capsys) -> None:
    out = _run(monkeypatch, capsys, None)
    assert out == {"wakeAgent": False, "status": "broker_unreachable"}
