"""Shared empty-seat pre-run gate: sync, decision core, probe, heartbeat wire.

The canonical source is ``operator/templates/pre_run_gate.py``; the copies
stamped into skill dirs must be byte-identical (edit template, restamp).
"""

from __future__ import annotations

import importlib.util
import json
import socketserver
import sys
import threading
from pathlib import Path

import pytest

_OPERATOR_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = _OPERATOR_ROOT / "templates" / "pre_run_gate.py"

# The always-wake PI-pack skills gated by the empty-seat gate (#1748).
# deadline-miss-escalator keeps its bespoke deadline pre_run and is NOT here;
# client-verification-tracker graduated to its own bespoke cadence gate (WP-B,
# #1889) and medical-records-chaser to its ledger-backed cadence gate
# (ss #2404), and lien-ledger-tracker to its settlement-closeout obligation
# ledger (ss #2455) — none of the three is on the shared template any longer.
GATED_SKILLS = (
    "daily-needs-you-digest",
    "discovery-response-tracker",
    "motion-calendar-tracker",
    "service-confirmation-watcher",
    "medical-chronology-maintainer",
    "mediation-settlement-tracker",
    "minors-compromise-packet",
    "trial-binder-assembler",
)


def _load_gate():
    spec = importlib.util.spec_from_file_location("pre_run_gate_template", _TEMPLATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Stamp sync
# ---------------------------------------------------------------------------


def test_all_gated_skills_carry_a_byte_identical_stamp() -> None:
    template_bytes = _TEMPLATE.read_bytes()
    missing, drifted = [], []
    for skill in GATED_SKILLS:
        stamp = _OPERATOR_ROOT / "skills" / skill / "pre_run.py"
        if not stamp.is_file():
            missing.append(skill)
        elif stamp.read_bytes() != template_bytes:
            drifted.append(skill)
    assert not missing, f"missing pre_run.py stamp: {missing}"
    assert not drifted, (
        f"pre_run.py drifted from templates/pre_run_gate.py: {drifted} — "
        "edit the template and restamp, never the copy"
    )


# ---------------------------------------------------------------------------
# Decision core
# ---------------------------------------------------------------------------


def _emitted(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_open_matters_wake(capsys) -> None:
    gate = _load_gate()
    gate.decide_and_emit(3, "discovery-response-tracker")
    assert _emitted(capsys) == {"wakeAgent": True}


def test_unknown_count_wakes(capsys) -> None:
    gate = _load_gate()
    gate.decide_and_emit(None, "discovery-response-tracker")
    assert _emitted(capsys) == {"wakeAgent": True}


def test_empty_seat_suppresses_after_heartbeat(capsys, monkeypatch) -> None:
    gate = _load_gate()
    written = []
    monkeypatch.setattr(
        gate, "write_suppressed_wake_heartbeat", lambda skill: written.append(skill) or True
    )
    gate.decide_and_emit(0, "lien-ledger-tracker")
    assert _emitted(capsys) == {"wakeAgent": False}
    assert written == ["lien-ledger-tracker"]


def test_empty_seat_with_failed_heartbeat_wakes(capsys, monkeypatch) -> None:
    """Mirror-don't-gate: no heartbeat row → no suppress."""
    gate = _load_gate()
    monkeypatch.setattr(gate, "write_suppressed_wake_heartbeat", lambda skill: False)
    gate.decide_and_emit(0, "lien-ledger-tracker")
    assert _emitted(capsys) == {"wakeAgent": True}


def test_assume_empty_flag_exercises_suppress_path(capsys, monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setattr(gate, "write_suppressed_wake_heartbeat", lambda skill: True)
    rc = gate.main(["--assume-empty"])
    assert rc == 0
    assert _emitted(capsys) == {"wakeAgent": False}


# ---------------------------------------------------------------------------
# Probe — execute the real snippet against a stubbed smokeball_connector
# ---------------------------------------------------------------------------

_STUB_CLIENT = """\
class _Client:
    def __init__(self, payload):
        self._payload = payload

    def get(self, path, **params):
        assert path == "/matters"
        assert params.get("Status") == "Open"
        assert params.get("Limit") == 1
        return self._payload


def build_client_from_env():
    import json, os
    return _Client(json.loads(os.environ["STUB_MATTERS_PAYLOAD"]))
"""


@pytest.fixture()
def stub_connector(tmp_path, monkeypatch):
    pkg = tmp_path / "smokeball_connector"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "client.py").write_text(_STUB_CLIENT)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    # Probe subprocess uses this interpreter instead of the connector venv.
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", sys.executable)

    def set_payload(payload) -> None:
        monkeypatch.setenv("STUB_MATTERS_PAYLOAD", json.dumps(payload))

    return set_payload


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"items": []}, 0),
        ({"items": [{"id": "m1"}]}, 1),
        ({"value": [{"id": "m1"}]}, 1),
        ([], 0),
        ([{"id": "m1"}], 1),
        ({"weird": "envelope"}, None),  # unknown shape → wake
        ("bare-string", None),
    ],
)
def test_probe_envelope_shapes(stub_connector, payload, expected) -> None:
    gate = _load_gate()
    stub_connector(payload)
    assert gate.probe_open_matter_count() == expected


def test_probe_missing_connector_python_returns_unknown(monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", "/nonexistent/python")
    assert gate.probe_open_matter_count() is None


# ---------------------------------------------------------------------------
# Heartbeat wire — dummy UDS broker
# ---------------------------------------------------------------------------


class _DummyBroker(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True
    received: list = []
    reply: dict = {"ok": True, "id": "01HTESTULID00000000000000"}


class _DummyHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline()
        self.server.received.append(json.loads(raw))
        self.wfile.write(json.dumps(self.server.reply).encode() + b"\n")


@pytest.fixture()
def dummy_broker(monkeypatch):
    # AF_UNIX sun_path caps at ~104 chars on macOS; pytest's tmp_path routinely
    # exceeds it. A short mkdtemp under the system tmp root stays portable.
    import shutil
    import tempfile

    sock_dir = tempfile.mkdtemp(prefix="prg-")
    sock_path = str(Path(sock_dir) / "b.sock")
    server = _DummyBroker(sock_path, _DummyHandler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SMD_AUDIT_BROKER_SOCKET", sock_path)
    monkeypatch.setenv("CUSTOMER_SLUG", "pilot-smokeball")
    yield server
    server.shutdown()
    server.server_close()
    shutil.rmtree(sock_dir, ignore_errors=True)


def test_heartbeat_sends_locked_action_type_and_acks(dummy_broker) -> None:
    gate = _load_gate()
    assert gate.write_suppressed_wake_heartbeat("trial-binder-assembler") is True
    (request,) = dummy_broker.received
    assert request["action"] == "suppressed_wake_append"
    row = request["row"]
    assert row["action_type"] == "SUPPRESSED_WAKE"
    assert row["skill_name"] == "trial-binder-assembler"
    metadata = json.loads(row["metadata"])
    assert metadata["decision_basis"] == "empty_seat:no_open_matters"
    assert metadata["customer"] == "pilot-smokeball"


def test_heartbeat_broker_rejection_returns_false(dummy_broker) -> None:
    gate = _load_gate()
    dummy_broker.reply = {"ok": False, "error": "PermissionError"}
    assert gate.write_suppressed_wake_heartbeat("trial-binder-assembler") is False


def test_heartbeat_without_socket_env_returns_false(monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    assert gate.write_suppressed_wake_heartbeat("x") is False


# ---------------------------------------------------------------------------
# The wake half of the gate (ss-console #2253 carried to the shared template,
# #2498). Before this, the eight routines on this template left a row on every
# quiet tick and NOTHING on the ticks they fired: the one tick that mattered
# was the one tick with no row.
# ---------------------------------------------------------------------------


def test_hydrated_seat_writes_an_emitted_wake_row(dummy_broker, capsys) -> None:
    gate = _load_gate()
    gate.decide_and_emit(3, "discovery-response-tracker")
    assert _emitted(capsys) == {"wakeAgent": True}
    (request,) = dummy_broker.received
    assert request["action"] == "emitted_wake_append"
    row = request["row"]
    assert row["action_type"] == "EMITTED_WAKE"
    assert row["skill_name"] == "discovery-response-tracker"
    metadata = json.loads(row["metadata"])
    assert metadata["decision_basis"] == "hydrated_seat:open_matters_present"
    # Same field set as the suppress row, so a reader can diff the two.
    assert metadata["platform"] == "cron-pre-run"
    assert metadata["customer"] == "pilot-smokeball"


def test_an_unprobeable_seat_is_a_different_wake_reason(dummy_broker, capsys) -> None:
    """A hydrated seat and a seat we could not probe both wake, and only one of
    them is healthy. One basis for both would erase the difference."""
    gate = _load_gate()
    gate.decide_and_emit(None, "motion-calendar-tracker")
    assert _emitted(capsys) == {"wakeAgent": True}
    (request,) = dummy_broker.received
    metadata = json.loads(request["row"]["metadata"])
    assert metadata["decision_basis"] == "probe_unavailable:open_matter_count_unknown"


def test_a_quiet_tick_still_writes_only_the_suppress_row(dummy_broker, capsys) -> None:
    """The falsifier for the two above: if the wake row were written
    unconditionally, this would see two requests instead of one."""
    gate = _load_gate()
    gate.decide_and_emit(0, "trial-binder-assembler")
    assert _emitted(capsys) == {"wakeAgent": False}
    (request,) = dummy_broker.received
    assert request["action"] == "suppressed_wake_append"


def test_a_refused_wake_row_never_changes_the_wake(dummy_broker, capsys) -> None:
    """Best-effort inverts the suppress path's contract on purpose: the wake is
    already the decision, so a wake that a failed audit write could suppress or
    delay would be a gate made of observability."""
    gate = _load_gate()
    dummy_broker.reply = {"ok": False, "error": "PermissionError"}
    gate.decide_and_emit(5, "medical-chronology-maintainer")
    assert _emitted(capsys) == {"wakeAgent": True}


def test_no_broker_at_all_never_changes_the_wake(monkeypatch, capsys) -> None:
    gate = _load_gate()
    monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    gate.decide_and_emit(5, "minors-compromise-packet")
    assert _emitted(capsys) == {"wakeAgent": True}


def test_a_failed_suppress_heartbeat_does_not_then_try_the_wake_row(dummy_broker, capsys) -> None:
    """Matching deadline-miss-escalator's `suppress_heartbeat_failed_fail_open`:
    a write to this very writer just failed, so a second one is a delay, not a
    record. Exactly one request reaches the broker."""
    gate = _load_gate()
    dummy_broker.reply = {"ok": False, "error": "PermissionError"}
    gate.decide_and_emit(0, "mediation-settlement-tracker")
    assert _emitted(capsys) == {"wakeAgent": True}
    assert len(dummy_broker.received) == 1
    assert dummy_broker.received[0]["action"] == "suppressed_wake_append"


def test_both_verbs_carry_the_same_metadata_keys(dummy_broker) -> None:
    """One writer for both halves, so the fields cannot drift apart."""
    gate = _load_gate()
    gate.write_suppressed_wake_heartbeat("trial-binder-assembler")
    gate.write_emitted_wake_heartbeat(
        "trial-binder-assembler", "hydrated_seat:open_matters_present"
    )
    suppress, emitted = dummy_broker.received
    assert set(suppress["row"]) == set(emitted["row"])
    assert set(json.loads(suppress["row"]["metadata"])) == set(
        json.loads(emitted["row"]["metadata"])
    )
