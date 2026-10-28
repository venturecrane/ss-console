"""case-file-manager pre_run: one scheduled submit per fire, never a wake,
always a row, and the Smokeball token age read with the overlay's own reader.

Drives the script against a fake broker on a real unix socket. Each test fails
if the line it defends is removed. The calendar, the per-day idempotency, the
deferral and the token gate are the broker's, and are tested against the real
broker in ``workspace_broker/tests/test_casefile_schedule.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import types
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "pre_run.py"


def _load():
    spec = importlib.util.spec_from_file_location("case_file_manager_pre_run", HERE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeBroker:
    def __init__(self, submit_answer: dict) -> None:
        self.dir = tempfile.mkdtemp(prefix="cfmpr")
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
                answer = self.submit_answer if req["action"] == "casefile_job_submit" else {"ok": True}
                conn.sendall(json.dumps(answer).encode() + b"\n")


def _reader(monkeypatch, ages):
    """Install a stand-in for the overlay's ``shared.connector_check``."""
    shared = types.ModuleType("shared")
    check = types.ModuleType("shared.connector_check")

    def token_ages():
        if isinstance(ages, Exception):
            raise ages
        return ages

    check.token_ages = token_ages  # type: ignore[attr-defined]
    shared.connector_check = check  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shared", shared)
    monkeypatch.setitem(sys.modules, "shared.connector_check", check)


def _run(monkeypatch, capsys, broker: FakeBroker | None) -> dict:
    if broker is None:
        monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
        monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    else:
        monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", broker.path)
    assert _load().main() == 0
    return json.loads(capsys.readouterr().out.strip())


QUEUED = {
    "ok": True,
    "accepted": True,
    "date": "2026-11-02",
    "mode": "full",
    "results": [{"caseload": "a1b2c3d4e5f6", "status": "queued", "job_id": "01JOB"}],
}


def test_a_fire_submits_the_scheduled_form_with_the_token_age_and_wakes_nothing(monkeypatch, capsys) -> None:
    _reader(monkeypatch, {"smokeball": 86400})
    broker = FakeBroker(QUEUED)
    out = _run(monkeypatch, capsys, broker)
    assert out["wakeAgent"] is False and out["status"] == "queued" and out["mode"] == "full"
    submit, row = broker.requests
    assert submit == {
        "action": "casefile_job_submit",
        "envelope": {"trigger": "scheduled", "smokeball_token_age_seconds": 86400},
    }
    assert row["action"] == "suppressed_wake_append"
    meta = json.loads(row["row"]["metadata"])
    assert meta["decision_basis"] == "casefile_scheduled:queued"
    assert row["row"]["skill_name"] == "case-file-manager"


def test_an_unknowable_token_age_is_sent_as_null_never_guessed(monkeypatch, capsys) -> None:
    _reader(monkeypatch, {})  # no token file: the reader reports nothing
    broker = FakeBroker({"ok": True, "accepted": False, "status": "token_gate", "reason": "x"})
    out = _run(monkeypatch, capsys, broker)
    assert broker.requests[0]["envelope"]["smokeball_token_age_seconds"] is None
    assert out["status"] == "token_gate"


def test_a_failing_reader_is_unknown_too(monkeypatch, capsys) -> None:
    _reader(monkeypatch, OSError("stat failed"))
    broker = FakeBroker({"ok": True, "accepted": False, "status": "token_gate", "reason": "x"})
    _run(monkeypatch, capsys, broker)
    assert broker.requests[0]["envelope"]["smokeball_token_age_seconds"] is None


def test_a_missing_reader_is_unknown(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "shared", None)  # import fails
    monkeypatch.setitem(sys.modules, "shared.connector_check", None)
    broker = FakeBroker({"ok": True, "accepted": False, "status": "token_gate", "reason": "x"})
    _run(monkeypatch, capsys, broker)
    assert broker.requests[0]["envelope"]["smokeball_token_age_seconds"] is None


def test_a_weekend_is_recorded_as_not_a_business_day(monkeypatch, capsys) -> None:
    _reader(monkeypatch, {"smokeball": 1})
    broker = FakeBroker({"ok": True, "accepted": False, "status": "not_business_day", "date": "2026-10-31"})
    out = _run(monkeypatch, capsys, broker)
    assert out == {"wakeAgent": False, "status": "not_business_day", "date": "2026-10-31"}
    assert json.loads(broker.requests[1]["row"]["metadata"])["decision_basis"] == "casefile_scheduled:not_business_day"


def test_a_missed_caseload_outranks_a_queued_one_in_the_status(monkeypatch, capsys) -> None:
    _reader(monkeypatch, {"smokeball": 1})
    answer = {
        "ok": True,
        "accepted": True,
        "date": "2026-11-03",
        "mode": "recheck",
        "results": [
            {"caseload": "aaaaaaaaaaaa", "status": "queued", "job_id": "01A"},
            {"caseload": "bbbbbbbbbbbb", "status": "missed", "paged": True},
        ],
    }
    out = _run(monkeypatch, capsys, FakeBroker(answer))
    assert out["status"] == "missed"
    assert out["caseloads"] == [
        {"caseload": "aaaaaaaaaaaa", "status": "queued", "job_id": "01A"},
        {"caseload": "bbbbbbbbbbbb", "status": "missed"},
    ]


def test_an_unreachable_broker_is_a_verdict_not_a_crash(monkeypatch, capsys) -> None:
    _reader(monkeypatch, {"smokeball": 1})
    out = _run(monkeypatch, capsys, None)
    assert out == {"wakeAgent": False, "status": "broker_unreachable"}


def test_stdout_never_carries_a_reason_sentence(monkeypatch, capsys) -> None:
    _reader(monkeypatch, {"smokeball": 1})
    answer = {"ok": True, "accepted": False, "status": "refused", "reason": "Pat Example is not authored"}
    out = _run(monkeypatch, capsys, FakeBroker(answer))
    assert "reason" not in out and "Pat" not in json.dumps(out)
