"""Derived memo facts: extraction, the process boundary, and the handoff.

The shared gate reads every open matter's memos IN CODE and hands the model
atoms, so a scheduled routine never asks one session for a second matter's
content and never trips the overlay's matter-mixing fence. See the template's
module docstring for the incident.

What these tests are FOR, stated so a green run means something:

* The extractors turn fixture memo payloads into the right facts. Every case
  carries a memo that must NOT match beside one that must, because an
  extractor that returned nothing would otherwise pass half of them.
* No memo prose crosses the boundary. Asserted on the SHIPPED payload, by
  hunting the fixture's own sentences in the serialized JSON, not by reading
  the extractor and agreeing with it.
* Every failure wakes. A facts pull that dies must cost exactly what the
  gate cost before facts existed: one plain wake.
* The handoff carries dates and (matterNumber, dates) records at 0600, which
  is the only shape ``shared/pre_run_handoff.py`` will read back.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

_OPERATOR_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = _OPERATOR_ROOT / "templates" / "pre_run_gate.py"

# A distinctive sentence planted in every fixture memo. If any of it reaches a
# payload or a handoff, prose crossed the boundary.
PROSE = "the claimant telephoned about a confidential settlement posture"


def _load_gate():
    spec = importlib.util.spec_from_file_location("pre_run_gate_facts_test", _TEMPLATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def memo(**fields) -> dict:
    row = {"id": "memo-1", "createdDate": "2026-09-01T12:00:00Z", "plainText": PROSE}
    row.update(fields)
    return row


class FakeClient:
    """A Smokeball client stub. Records the paths it was asked for, so a test
    can prove the memo read happened rather than assume it."""

    def __init__(self, matters, memos_by_matter, fail_for=()):
        self._matters = matters
        self._memos = memos_by_matter
        self._fail_for = set(fail_for)
        self.paths: list[str] = []

    def get(self, path, **params):
        self.paths.append(path)
        if path == "/matters":
            assert params.get("Status") == "Open"
            return self._matters
        matter_id = path.split("/")[2]
        if matter_id in self._fail_for:
            raise RuntimeError(f"boom reading {matter_id} {PROSE}")
        return self._memos.get(matter_id, {"value": []})


# ---------------------------------------------------------------------------
# Extraction, per skill
# ---------------------------------------------------------------------------


def test_service_watcher_gets_the_file_ids_a_prior_capture_recorded() -> None:
    gate = _load_gate()
    payload = {
        "value": [
            memo(id="m1", plainText=f"[Operator] Captured the service confirmation. fileId FILE-AAA recorded. {PROSE}"),
            memo(id="m2", plainText=f"[Operator] second capture: fileId FILE-BBB recorded, surfaced. {PROSE}"),
            # Unstamped: a person's note quoting a fileId is not a capture.
            memo(id="m3", plainText="fileId FILE-CCC mentioned by the paralegal"),
            memo(id="m4", plainText=f"[Operator] a stamped memo with no capture at all. {PROSE}"),
        ]
    }
    assert gate.derive_matter_facts("service-confirmation-watcher", payload) == {
        "captured_file_ids": ["FILE-AAA", "FILE-BBB"]
    }


def test_a_repeated_file_id_is_recorded_once() -> None:
    gate = _load_gate()
    payload = {
        "value": [
            memo(id="m1", plainText="[Operator] fileId FILE-AAA recorded."),
            memo(id="m2", plainText="[Operator] fileId FILE-AAA recorded again."),
        ]
    }
    assert gate.derive_matter_facts("service-confirmation-watcher", payload) == {"captured_file_ids": ["FILE-AAA"]}


def test_motion_tracker_gets_the_day_of_its_own_latest_surface() -> None:
    gate = _load_gate()
    payload = {
        "value": [
            memo(id="m1", createdDate="2026-08-01T09:00:00Z", plainText="[Operator] Motion calendar assembled for ..."),
            memo(id="m2", createdDate="2026-09-14T09:00:00Z", plainText="[Operator] Motion calendar assembled for ..."),
            # A LATER memo that is not this skill's surface must not win.
            memo(id="m3", createdDate="2026-09-20T09:00:00Z", plainText=f"[Operator] Captured a lien. {PROSE}"),
            # Nor an unstamped one quoting the marker.
            memo(id="m4", createdDate="2026-09-21T09:00:00Z", plainText="motion calendar assembled by hand"),
        ]
    }
    assert gate.derive_matter_facts("motion-calendar-tracker", payload) == {"last_surface": "2026-09-14"}


def test_motion_tracker_reports_no_prior_surface_as_none() -> None:
    gate = _load_gate()
    payload = {"value": [memo(id="m1", plainText=f"[Operator] something else entirely. {PROSE}")]}
    assert gate.derive_matter_facts("motion-calendar-tracker", payload) == {"last_surface": None}


def test_discovery_tracker_gets_memo_ids_and_dates_for_extension_mentions() -> None:
    gate = _load_gate()
    payload = {
        "value": [
            memo(
                id="memo-ext",
                plainText=f"Opposing counsel asked for an extension to 2026-10-15 and again to 2026-11-01. {PROSE}",
            ),
            memo(id="memo-stip", plainText=f"Stipulation filed; responses now due 2026-12-02. {PROSE}"),
            # No extension language: must not become a candidate.
            memo(id="memo-other", plainText=f"Responses served 2026-09-09. {PROSE}"),
        ]
    }
    assert gate.derive_matter_facts("discovery-response-tracker", payload) == {
        "extension_candidates": [
            {"memoId": "memo-ext", "dates": ["2026-10-15", "2026-11-01"]},
            {"memoId": "memo-stip", "dates": ["2026-12-02"]},
        ]
    }


def test_an_impossible_date_is_not_a_date() -> None:
    """The falsifier for the ISO scan: a well-shaped string that is not a day."""
    gate = _load_gate()
    payload = {"value": [memo(id="memo-ext", plainText="extension to 2026-13-45 (typo) and 2026-02-30")]}
    assert gate.derive_matter_facts("discovery-response-tracker", payload) == {
        "extension_candidates": [{"memoId": "memo-ext", "dates": []}]
    }


def test_a_skill_outside_the_table_derives_nothing() -> None:
    gate = _load_gate()
    payload = {"value": [memo(id="m1", plainText="[Operator] fileId FILE-AAA recorded.")]}
    assert gate.derive_matter_facts("trial-binder-assembler", payload) == {}


def test_an_unrecognised_memo_envelope_is_unreadable_not_empty() -> None:
    """ "No prior capture" and "could not read the memos" are opposite
    instructions to the model, so they must not share a representation."""
    gate = _load_gate()
    assert gate.derive_matter_facts("service-confirmation-watcher", {"weird": "envelope"}) == {"unreadable": True}
    assert gate.derive_matter_facts("service-confirmation-watcher", {"value": []}) == {"captured_file_ids": []}


def test_facts_per_matter_are_capped_and_the_cap_is_announced() -> None:
    gate = _load_gate()
    memos = [memo(id=f"m{i}", plainText=f"[Operator] fileId FILE-{i:03d} recorded.") for i in range(40)]
    row = gate.derive_matter_facts("service-confirmation-watcher", {"value": memos})
    assert row["truncated"] is True
    assert len(row["captured_file_ids"]) == gate._FACTS_PER_MATTER_CAP


# ---------------------------------------------------------------------------
# The pull, and the process boundary
# ---------------------------------------------------------------------------


def _service_pull(gate, fail_for=()):
    client = FakeClient(
        matters={"value": [{"id": "mat-1", "number": "2026-0142"}, {"id": "mat-2", "number": "2026-0177"}]},
        memos_by_matter={
            "mat-1": {"value": [memo(id="m1", plainText=f"[Operator] fileId FILE-AAA recorded. {PROSE}")]},
            "mat-2": {"value": [memo(id="m2", plainText=f"[Operator] fileId FILE-BBB recorded. {PROSE}")]},
        },
        fail_for=fail_for,
    )
    return client, gate.pull_facts_payload(client, "service-confirmation-watcher")


def test_the_pull_reads_every_open_matter_and_returns_only_facts() -> None:
    gate = _load_gate()
    client, payload = _service_pull(gate)
    assert client.paths == ["/matters", "/matters/mat-1/memos", "/matters/mat-2/memos"]
    assert payload["openMatterCount"] == 2
    assert payload["mattersTruncated"] is False
    assert payload["matters"] == [
        {"captured_file_ids": ["FILE-AAA"], "matterId": "mat-1", "matterNumber": "2026-0142"},
        {"captured_file_ids": ["FILE-BBB"], "matterId": "mat-2", "matterNumber": "2026-0177"},
    ]


def test_no_memo_prose_reaches_the_payload() -> None:
    """Hunted in the SHIPPED serialization, not inferred from the extractor."""
    gate = _load_gate()
    _, payload = _service_pull(gate)
    serialized = json.dumps(payload)
    assert PROSE not in serialized
    # Distinctive words only: "a" and "the" appear in JSON keys, and a check
    # that cannot pass on correct code measures nothing either.
    for word in [w for w in PROSE.split() if len(w) > 4]:
        assert word not in serialized, f"memo prose reached the payload: {word!r}"


def test_one_unreadable_matter_degrades_its_own_row_and_nothing_else() -> None:
    gate = _load_gate()
    _, payload = _service_pull(gate, fail_for=("mat-1",))
    assert payload["unreadableMatters"] == 1
    assert [row["matterId"] for row in payload["matters"]] == ["mat-2"]


def test_more_matters_than_the_cap_are_announced_as_truncated() -> None:
    gate = _load_gate()
    matters = {"value": [{"id": f"mat-{i}", "number": f"2026-{i:04d}"} for i in range(gate._FACTS_MATTER_CAP + 5)]}
    client = FakeClient(matters=matters, memos_by_matter={})
    payload = gate.pull_facts_payload(client, "service-confirmation-watcher")
    assert payload["mattersTruncated"] is True
    assert len(payload["matters"]) == gate._FACTS_MATTER_CAP


def test_an_unknown_matter_envelope_yields_no_count_and_no_facts() -> None:
    gate = _load_gate()
    client = FakeClient(matters={"weird": "envelope"}, memos_by_matter={})
    assert gate.pull_facts_payload(client, "service-confirmation-watcher") == {"envelopeUnknown": True}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"a": ["FILE-1", "2026-09-01"], "b": 3, "c": True, "d": None}, True),
        ({"a": "a sentence with spaces"}, False),
        ({"a": ["ok", "not ok"]}, False),
        ({"a": {"b": "still-fine"}}, True),
        ({"a": object()}, False),
    ],
)
def test_the_atom_guard_accepts_atoms_and_refuses_sentences(value, expected) -> None:
    gate = _load_gate()
    assert gate._atoms_only(value) is expected


# ---------------------------------------------------------------------------
# The subprocess seam — the real snippet, against a stub connector
# ---------------------------------------------------------------------------

_STUB_CLIENT = """\
import json
import os


class _Client:
    def __init__(self, payload):
        self._payload = payload

    def get(self, path, **params):
        if path == "/matters":
            return self._payload["matters"]
        return self._payload["memos"].get(path.split("/")[2], {"value": []})


def build_client_from_env():
    return _Client(json.loads(os.environ["STUB_SMOKEBALL_PAYLOAD"]))
"""


@pytest.fixture()
def stub_connector(tmp_path, monkeypatch):
    pkg = tmp_path / "smokeball_connector"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "client.py").write_text(_STUB_CLIENT)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", sys.executable)

    def set_payload(payload) -> None:
        monkeypatch.setenv("STUB_SMOKEBALL_PAYLOAD", json.dumps(payload))

    return set_payload


def test_the_snippet_runs_this_very_file_and_brings_back_facts(stub_connector) -> None:
    """The seam the design rests on: the snippet loads the gate module and calls
    ``pull_facts_payload``, so the code these tests exercise in-process is the
    code that runs against the live tenant. If the snippet drifted from the
    module, this is what would notice."""
    gate = _load_gate()
    stub_connector(
        {
            "matters": {"value": [{"id": "mat-1", "number": "2026-0142"}]},
            "memos": {
                "mat-1": {
                    "value": [
                        {
                            "id": "memo-ext",
                            "createdDate": "2026-09-14T00:00:00Z",
                            "plainText": f"Extension granted to 2026-10-15. {PROSE}",
                        }
                    ]
                }
            },
        }
    )
    count, facts = gate.fetch_memo_facts("discovery-response-tracker")
    assert count == 1
    assert facts is not None
    assert facts["matters"] == [
        {
            "extension_candidates": [{"memoId": "memo-ext", "dates": ["2026-10-15"]}],
            "matterId": "mat-1",
            "matterNumber": "2026-0142",
        }
    ]
    assert PROSE not in json.dumps(facts)


def test_a_missing_connector_python_yields_no_count_and_no_facts(monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", "/nonexistent/python")
    assert gate.fetch_memo_facts("discovery-response-tracker") == (None, None)


def test_a_crashing_pull_yields_no_count_and_no_facts(tmp_path, monkeypatch) -> None:
    """No stub connector on the path, so the import inside the snippet fails."""
    gate = _load_gate()
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", sys.executable)
    assert gate.fetch_memo_facts("discovery-response-tracker") == (None, None)


def test_a_payload_carrying_prose_is_dropped_at_the_boundary(monkeypatch) -> None:
    """The reading side re-checks, because the writer ran in another process.
    A stub 'connector venv' that prints a sentence must buy nothing."""
    gate = _load_gate()

    class _Result:
        returncode = 0
        stdout = json.dumps({"openMatterCount": 1, "matters": [{"matterId": "m", "note": "a whole sentence"}]})
        stderr = ""

    monkeypatch.setattr(gate.Path, "exists", lambda self: True)
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Result())
    assert gate.fetch_memo_facts("discovery-response-tracker") == (None, None)


# ---------------------------------------------------------------------------
# Wake behaviour
# ---------------------------------------------------------------------------


def _emitted(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_facts_ride_the_wake_line(capsys, monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setattr(gate, "write_emitted_wake_heartbeat", lambda *a, **k: True)
    gate.decide_and_emit(2, "service-confirmation-watcher", {"matters": [{"matterId": "mat-1"}]})
    assert _emitted(capsys) == {"wakeAgent": True, "memo_facts": {"matters": [{"matterId": "mat-1"}]}}


def test_a_skill_outside_the_table_emits_exactly_the_bare_wake(capsys, monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setattr(gate, "write_emitted_wake_heartbeat", lambda *a, **k: True)
    monkeypatch.setattr(gate, "probe_open_matter_count", lambda: 4)
    monkeypatch.setattr(gate, "_skill_name", lambda: "trial-binder-assembler")
    monkeypatch.setattr(gate, "fetch_memo_facts", lambda skill: pytest.fail("a non-table skill must not pull facts"))
    assert gate.main([]) == 0
    assert _emitted(capsys) == {"wakeAgent": True}


def test_a_failed_facts_pull_still_wakes_fact_free(capsys, monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setattr(gate, "write_emitted_wake_heartbeat", lambda *a, **k: True)
    monkeypatch.setattr(gate, "_skill_name", lambda: "motion-calendar-tracker")
    monkeypatch.setattr(gate, "fetch_memo_facts", lambda skill: (None, None))
    assert gate.main([]) == 0
    assert _emitted(capsys) == {"wakeAgent": True}


def test_an_empty_seat_still_suppresses_on_the_facts_path(capsys, monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setattr(gate, "write_suppressed_wake_heartbeat", lambda skill: True)
    monkeypatch.setattr(gate, "_skill_name", lambda: "motion-calendar-tracker")
    monkeypatch.setattr(gate, "fetch_memo_facts", lambda skill: (0, {"matters": [], "openMatterCount": 0}))
    assert gate.main([]) == 0
    assert _emitted(capsys) == {"wakeAgent": False}


# ---------------------------------------------------------------------------
# The provenance handoff
# ---------------------------------------------------------------------------


@pytest.fixture()
def handoff_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path / ".smd" / "pre_run"


def _discovery_facts() -> dict:
    return {
        "skill": "discovery-response-tracker",
        "openMatterCount": 2,
        "matters": [
            {
                "matterId": "mat-1",
                "matterNumber": "2026-0142",
                "extension_candidates": [{"memoId": "memo-ext", "dates": ["2026-10-15"]}],
            },
            {
                "matterId": "mat-2",
                "matterNumber": "2026-0177",
                "extension_candidates": [{"memoId": "memo-two", "dates": ["2026-11-01", "2026-12-02"]}],
            },
        ],
    }


def test_the_handoff_carries_dates_and_pairs_and_nothing_else(handoff_home) -> None:
    gate = _load_gate()
    gate.write_pre_run_handoff("discovery-response-tracker", _discovery_facts())
    written = json.loads((handoff_home / "discovery-response-tracker.json").read_text())
    assert set(written) == {"skill", "started_at", "dates", "matter_ids", "records"}
    assert written["dates"] == ["2026-10-15", "2026-11-01", "2026-12-02"]
    assert written["records"] == [
        {"matterNumber": "2026-0142", "dates": ["2026-10-15"]},
        {"matterNumber": "2026-0177", "dates": ["2026-11-01", "2026-12-02"]},
    ]
    assert written["started_at"].endswith("Z")
    # A memo id is not a date and must not have been smuggled in as one.
    assert "memo-ext" not in json.dumps(written["dates"] + written["records"])


def test_the_handoff_file_is_private(handoff_home) -> None:
    gate = _load_gate()
    gate.write_pre_run_handoff("discovery-response-tracker", _discovery_facts())
    mode = stat.S_IMODE((handoff_home / "discovery-response-tracker.json").stat().st_mode)
    assert mode == 0o600, f"handoff mode {mode:o}; it names the matters the firm is working on"


def test_the_motion_surface_day_reaches_the_handoff(handoff_home) -> None:
    gate = _load_gate()
    facts = {"matters": [{"matterId": "mat-1", "matterNumber": "2026-0142", "last_surface": "2026-09-14"}]}
    gate.write_pre_run_handoff("motion-calendar-tracker", facts)
    written = json.loads((handoff_home / "motion-calendar-tracker.json").read_text())
    assert written["dates"] == ["2026-09-14"]
    assert written["records"] == [{"matterNumber": "2026-0142", "dates": ["2026-09-14"]}]


def test_a_skill_whose_facts_are_ids_writes_no_handoff(handoff_home) -> None:
    """The service watcher hands over fileIds. Nothing there is rendered as a
    date, so nothing needs certifying, and an empty handoff would consume
    itself for nothing."""
    gate = _load_gate()
    facts = {"matters": [{"matterId": "mat-1", "matterNumber": "2026-0142", "captured_file_ids": ["FILE-AAA"]}]}
    gate.write_pre_run_handoff("service-confirmation-watcher", facts)
    assert not handoff_home.exists()


def test_facts_with_no_dates_write_no_handoff(handoff_home) -> None:
    gate = _load_gate()
    gate.write_pre_run_handoff("motion-calendar-tracker", {"matters": [{"matterId": "m", "last_surface": None}]})
    assert not (handoff_home / "motion-calendar-tracker.json").exists()


def test_a_handoff_write_failure_never_raises(monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setenv("HERMES_HOME", "/proc/nonexistent-root/cannot-create")
    gate.write_pre_run_handoff("discovery-response-tracker", _discovery_facts())  # must not raise


def test_a_stale_temp_file_does_not_wedge_the_writer(handoff_home) -> None:
    gate = _load_gate()
    handoff_home.mkdir(mode=0o700, parents=True)
    (handoff_home / ".discovery-response-tracker.json.tmp").write_text("left by a crashed run")
    gate.write_pre_run_handoff("discovery-response-tracker", _discovery_facts())
    assert json.loads((handoff_home / "discovery-response-tracker.json").read_text())["dates"]
