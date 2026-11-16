"""mailbox-keeper pre_run: one broker call, never a wake, always a counts-only row.

Drives the script against a fake broker on a real unix socket. Each test fails
if the line it defends is removed: the request names only the mode, the LAST
stdout line is always ``wakeAgent: false``, the outcome lands as a
SUPPRESSED_WAKE row, and nothing but counts and a status word reaches stdout
or the row.
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
    spec = importlib.util.spec_from_file_location("mailbox_keeper_pre_run", HERE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeBroker:
    def __init__(self, run_answer: dict) -> None:
        self.dir = tempfile.mkdtemp(prefix="mkpr")
        self.path = os.path.join(self.dir, "b.sock")
        self.requests: list[dict] = []
        self.run_answer = run_answer
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
                answer = self.run_answer if req["action"] == "mailbox_keeper_run" else {"ok": True}
                conn.sendall(json.dumps(answer).encode() + b"\n")


def _run(monkeypatch, capsys, broker: FakeBroker | None) -> tuple[dict, list[str]]:
    if broker is None:
        monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
        monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    else:
        monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", broker.path)
    assert _load().main() == 0
    lines = capsys.readouterr().out.strip().splitlines()
    return json.loads(lines[-1]), lines


def _row_meta(broker: FakeBroker) -> dict:
    row = broker.requests[-1]
    assert row["action"] == "suppressed_wake_append"
    assert row["row"]["action_type"] == "SUPPRESSED_WAKE" and row["row"]["skill_name"] == "mailbox-keeper"
    return json.loads(row["row"]["metadata"])


def test_a_run_names_only_the_mode_and_wakes_nothing(monkeypatch, capsys) -> None:
    counts = {"pages": 2, "threads": 9, "settled": 3, "trashed": 3, "trash_failed": 0, "waiting": 1}
    broker = FakeBroker({"ok": True, "status": "done", "mode": "daily", "counts": counts})
    out, lines = _run(monkeypatch, capsys, broker)
    # No seat slug, so no cost breaker: the broker is told the model is refused.
    assert broker.requests[0] == {"action": "mailbox_keeper_run", "mode": "scheduled", "model": "refused"}
    assert out == {"wakeAgent": False, "status": "done", "mode": "daily", **counts}
    assert len(lines) == 1
    meta = _row_meta(broker)
    assert meta["decision_basis"] == "mailbox_keeper_ran" and meta["trashed"] == 3


def test_a_failed_read_is_a_failed_heartbeat(monkeypatch, capsys) -> None:
    broker = FakeBroker({"ok": True, "status": "read_failed", "reason": "search_error:OSError"})
    out, _ = _run(monkeypatch, capsys, broker)
    assert out["wakeAgent"] is False and out["status"] == "read_failed"
    meta = _row_meta(broker)
    assert meta["decision_basis"] == "mailbox_keeper_failed" and meta["reason"] == "search_error:OSError"


def test_nothing_but_counts_and_status_words_reach_stdout_or_the_row(monkeypatch, capsys) -> None:
    broker = FakeBroker(
        {
            "ok": True,
            "status": "done",
            "counts": {"waiting": 1, "subject": "Proposal question"},
            "decisions": {"waiting": [{"thread_id": "t-wait"}]},
            "reason": "dana@prospect.example wrote",
        }
    )
    _, lines = _run(monkeypatch, capsys, broker)
    text = "\n".join(lines) + json.dumps(_row_meta(broker))
    for leaked in ("Proposal question", "t-wait", "dana@prospect.example"):
        assert leaked not in text


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        ({"ok": False, "error": "PermissionError"}, "broker_refused"),
        ({"ok": True, "status": "digest_failed"}, "digest_failed"),
    ],
)
def test_a_broker_refusal_is_recorded_and_wakes_nothing(monkeypatch, capsys, answer: dict, status: str) -> None:
    broker = FakeBroker(answer)
    out, _ = _run(monkeypatch, capsys, broker)
    assert out["wakeAgent"] is False and out["status"] == status
    assert _row_meta(broker)["decision_basis"] == "mailbox_keeper_failed"


def test_no_broker_is_a_verdict_not_a_crash(monkeypatch, capsys) -> None:
    out, _ = _run(monkeypatch, capsys, None)
    assert out == {"wakeAgent": False, "status": "broker_refused", "reason": "not_configured"}


def test_a_dead_socket_is_a_verdict_not_a_crash(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", str(tmp_path / "nothing.sock"))
    assert _load().main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["wakeAgent"] is False and out["status"] == "broker_unreachable"


def test_a_missing_client_still_prints_a_verdict(monkeypatch, capsys) -> None:
    """The scheduler runs this under the gateway interpreter, where the overlay's
    `shared` is installed; if that ever stops holding, the run says so and
    wakes nothing."""
    import builtins

    real_import = builtins.__import__

    def no_shared(name, *args, **kwargs):
        if name.startswith("shared"):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_shared)
    assert _load().main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"wakeAgent": False, "status": "client_unavailable", "error": "ModuleNotFoundError"}


def _metered(monkeypatch, tmp_path, broker: FakeBroker) -> Path:
    db = tmp_path / "sticky_stop.db"
    monkeypatch.setenv("CUSTOMER_SLUG", "example-seat")
    monkeypatch.setenv("SMD_STICKY_STOP_DB_PATH", str(db))
    monkeypatch.setenv("SMD_AUDIT_BROKER_SOCKET", broker.path)
    return db


def test_the_cost_breaker_is_asked_before_and_charged_after(monkeypatch, capsys, tmp_path) -> None:
    import sqlite3

    broker = FakeBroker({"ok": True, "status": "done", "mode": "daily", "classify": "ok", "counts": {"cost_cents": 5}})
    db = _metered(monkeypatch, tmp_path, broker)
    out, _ = _run(monkeypatch, capsys, broker)
    assert broker.requests[0]["model"] == "allowed"
    assert out["classify"] == "ok" and out["cost_cents"] == 5
    (cents,) = sqlite3.connect(db).execute("SELECT cost_cents_today FROM sticky_stop_state").fetchone()
    assert cents == 5


def test_a_stopped_breaker_refuses_the_model(monkeypatch, capsys, tmp_path) -> None:
    import sqlite3

    broker = FakeBroker({"ok": True, "status": "done", "mode": "daily", "counts": {}})
    db = _metered(monkeypatch, tmp_path, broker)
    first = FakeBroker({"ok": True, "status": "done", "counts": {}})
    monkeypatch.setenv("SMD_AUDIT_BROKER_SOCKET", first.path)
    _run(monkeypatch, capsys, first)  # creates the table
    assert first.requests[0]["model"] == "allowed"  # the control: the same seat, not stopped
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sticky_stop_state SET level = 'HARD_STOP'")
    if conn.total_changes == 0:
        conn.execute(
            "INSERT INTO sticky_stop_state (customer, persona, level, updated_at) VALUES "
            "('example-seat', '_machine', 'HARD_STOP', '2026-11-16T00:00:00Z')"
        )
    conn.commit()
    monkeypatch.setenv("SMD_AUDIT_BROKER_SOCKET", broker.path)
    _run(monkeypatch, capsys, broker)
    assert broker.requests[0]["model"] == "refused"
