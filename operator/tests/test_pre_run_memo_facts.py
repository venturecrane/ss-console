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
from datetime import datetime, timezone
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
            # The SURFACE form, which SKILL.md step 6 also sanctions. Matching
            # only the log body would read this matter as never surfaced.
            memo(id="m2", createdDate="2026-09-14T09:00:00Z", plainText="[Operator] # Motion Calendar - Roe ..."),
            # A LATER memo that is not this skill's surface must not win.
            memo(id="m3", createdDate="2026-09-20T09:00:00Z", plainText=f"[Operator] Captured a lien. {PROSE}"),
            # Nor an unstamped one quoting the marker.
            memo(id="m4", createdDate="2026-09-21T09:00:00Z", plainText="motion calendar assembled by hand"),
        ]
    }
    assert gate.derive_matter_facts("motion-calendar-tracker", payload) == {"last_surface": "2026-09-14"}


def test_a_note_updated_in_place_surfaces_on_its_last_update_day() -> None:
    # Since 2026-09-29 a routine's note is updated in place (connector
    # memo_tools.upsert_memo): its createdDate is the FIRST surface, and the
    # latest is max(createdDate, lastUpdated).
    gate = _load_gate()
    note = "[Operator] Motion calendar as of 2026-09-28\nMotion calendar assembled: 1 hearing."
    payload = {
        "value": [
            memo(id="m1", createdDate="2026-09-01T09:00:00Z", lastUpdated="2026-09-28T15:00:00Z", plainText=note),
            memo(id="m2", createdDate="2026-09-14T09:00:00Z", plainText="[Operator] Motion calendar assembled for ..."),
        ]
    }
    assert gate.derive_matter_facts("motion-calendar-tracker", payload) == {"last_surface": "2026-09-28"}
    assert gate._memo_day({"createdDate": "2026-09-20T00:00:00Z", "lastUpdated": "garbage"}) == "2026-09-20"
    assert gate._memo_day({"lastUpdated": "2026-09-21T00:00:00Z"}) == "2026-09-21"


def test_a_no_motions_note_is_found_by_its_header() -> None:
    # Since 2026-09-29 a matter with no motions gets "No motions on file." and
    # "Nothing to do.", which carry no surface marker; the header marks it.
    gate = _load_gate()
    payload = {
        "value": [
            memo(
                id="m1",
                createdDate="2026-09-29T09:00:00Z",
                plainText="[Operator] Motion calendar as of Sep 29, 2026\nNo motions on file.\nNothing to do.",
            ),
            # Another routine's note on the same day is not this one.
            memo(
                id="m2",
                createdDate="2026-09-30T09:00:00Z",
                plainText="[Operator] Trial binder as of Sep 30, 2026\nThe motion calendar is clear.",
            ),
        ]
    }
    assert gate.derive_matter_facts("motion-calendar-tracker", payload) == {"last_surface": "2026-09-29"}


RENAMED = {
    "value": [
        memo(
            id="m1",
            createdDate="2026-09-29T09:00:00Z",
            plainText="[Operator] Hearings and motions as of Sep 29, 2026\nNo motions on file.\nNothing to do.",
        ),
        # The fallback phrase in ANOTHER routine's name must not win when the
        # seat authored a label for this one.
        memo(
            id="m2",
            createdDate="2026-09-30T09:00:00Z",
            plainText="[Operator] Motion calendar review as of Sep 30, 2026\nSomething else.",
        ),
    ]
}


def test_a_renamed_routine_is_found_by_the_firms_label() -> None:
    gate = _load_gate()
    assert gate.derive_matter_facts("motion-calendar-tracker", RENAMED, routine_label="Hearings and  motions") == {
        "last_surface": "2026-09-29"
    }


def test_with_no_label_the_fallback_phrase_decides() -> None:
    # The falsifier for the label path: without it, the renamed note is not
    # found and the other routine's note (which says "motion calendar") is.
    gate = _load_gate()
    assert gate.derive_matter_facts("motion-calendar-tracker", RENAMED) == {"last_surface": "2026-09-30"}


def test_the_label_is_read_from_the_seats_routine_names(tmp_path, monkeypatch) -> None:
    gate = _load_gate()
    config = tmp_path / "customer.yaml"
    config.write_text("routine_names:\n  motion-calendar-tracker: 'Hearings and motions'\n  other: 'x'\n")
    assert gate.routine_label("motion-calendar-tracker", str(config)) == "Hearings and motions"
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(config))
    assert gate.routine_label("motion-calendar-tracker") == "Hearings and motions"
    # No entry, no block, no file, no path: None, and the caller falls back.
    assert gate.routine_label("service-confirmation-watcher", str(config)) is None
    (tmp_path / "bare.yaml").write_text("cron: []\n")
    assert gate.routine_label("motion-calendar-tracker", str(tmp_path / "bare.yaml")) is None
    assert gate.routine_label("motion-calendar-tracker", str(tmp_path / "missing.yaml")) is None
    monkeypatch.delenv("SMD_CUSTOMER_YAML_PATH")
    assert gate.routine_label("motion-calendar-tracker") is None


def test_the_pull_carries_the_label_to_every_matter() -> None:
    gate = _load_gate()
    client = FakeClient(matters={"value": [{"id": "mat-1", "number": "2026-0142"}]}, memos_by_matter={"mat-1": RENAMED})
    labelled = gate.pull_facts_payload(client, "motion-calendar-tracker", routine_label="Hearings and motions")
    assert labelled["matters"][0]["last_surface"] == "2026-09-29"
    bare = gate.pull_facts_payload(client, "motion-calendar-tracker")
    assert bare["matters"][0]["last_surface"] == "2026-09-30"


def test_motion_tracker_reports_no_prior_surface_as_none() -> None:
    gate = _load_gate()
    payload = {
        "value": [
            memo(id="m1", plainText=f"[Operator] something else entirely. {PROSE}"),
            # A passing mention mid-sentence is not a surface.
            memo(id="m2", plainText="[Operator] The attorney asked about the motion calendar this week."),
        ]
    }
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


def test_a_full_memo_page_marks_the_row_truncated() -> None:
    """A full page is a PARTIAL view of the matter. An absent capture in it is
    unknown, not absent, and the skills read `truncated` as exactly that."""
    gate = _load_gate()
    memos = [memo(id=f"m{i}", plainText="a paralegal's note") for i in range(gate._MEMO_PAGE_LIMIT)]
    assert gate.derive_matter_facts("motion-calendar-tracker", {"value": memos}) == {
        "last_surface": None,
        "truncated": True,
    }
    # The falsifier: one memo short of a full page is NOT truncated, so the
    # flag tracks the page boundary rather than being always on.
    assert gate.derive_matter_facts("motion-calendar-tracker", {"value": memos[:-1]}) == {"last_surface": None}


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
# The facts digest: same facts, same note, whatever the wording (2026-09-29)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)

EVENTS = [
    {"id": "e1", "subject": "MSJ hearing", "startTime": "2026-10-06T16:30:00", "location": "Dept 31"},
    {"id": "e2", "subject": "MTC hearing", "startTime": "2026-10-20T15:30:00", "location": "Dept 31"},
]
TASKS = [
    {"id": "t1", "subject": "File opposition to MSJ", "dueDateOnly": "2026-09-22", "isCompleted": False},
    {"id": "t2", "subject": "Serve notice", "dueDate": "2026-10-01T07:00:00Z", "isCompleted": False},
]


def test_the_digest_is_twelve_hex_and_the_same_in_any_order() -> None:
    gate = _load_gate()
    one = gate.facts_digest(EVENTS, TASKS, now=_NOW)
    assert len(one) == 12 and all(ch in "0123456789abcdef" for ch in one)
    assert gate.facts_digest(list(reversed(EVENTS)), list(reversed(TASKS)), now=_NOW) == one
    # Ids and whitespace inside a subject are not facts the note reports.
    shuffled = [{**EVENTS[0], "id": "other", "subject": "MSJ   hearing"}, EVENTS[1]]
    assert gate.facts_digest(shuffled, TASKS, now=_NOW) == one
    # Twice in a row, in a fresh module: deterministic across runs.
    assert _load_gate().facts_digest(EVENTS, TASKS, now=_NOW) == one


@pytest.mark.parametrize(
    "change",
    [
        lambda e, t: ([{**e[0], "startTime": "2026-10-07T16:30:00"}, e[1]], t),
        lambda e, t: ([{**e[0], "location": "Dept 3"}, e[1]], t),
        lambda e, t: ([{**e[0], "subject": "MSJ hearing (continued)"}, e[1]], t),
        lambda e, t: (e[:1], t),
        lambda e, t: (e, [{**t[0], "dueDateOnly": "2026-09-23"}, t[1]]),
        lambda e, t: (e, t[:1]),
    ],
)
def test_any_reported_fact_changing_moves_the_digest(change) -> None:
    """The falsifier: a digest that never moved would call every run unchanged."""
    gate = _load_gate()
    events, tasks = change(EVENTS, TASKS)
    assert gate.facts_digest(events, tasks, now=_NOW) != gate.facts_digest(EVENTS, TASKS, now=_NOW)


def test_a_hearing_date_passing_moves_the_digest() -> None:
    gate = _load_gate()
    later = _NOW.replace(month=10, day=7)
    assert gate.facts_digest(EVENTS, TASKS, now=later) != gate.facts_digest(EVENTS, TASKS, now=_NOW)


def test_a_probe_task_is_not_a_fact() -> None:
    gate = _load_gate()
    probe = {"id": "p", "subject": "[SMD-PROBE 1] self test", "dueDateOnly": "2026-10-01"}
    assert gate.facts_digest(EVENTS, [*TASKS, probe], now=_NOW) == gate.facts_digest(EVENTS, TASKS, now=_NOW)


class DigestClient(FakeClient):
    def __init__(self, *args, events=None, tasks=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._events = events if events is not None else {"value": EVENTS}
        self._tasks = tasks if tasks is not None else {"value": TASKS}
        self.params: list[tuple[str, dict]] = []

    def get(self, path, **params):
        self.params.append((path, params))
        if path == "/events":
            self.paths.append(path)
            return self._events
        if path == "/tasks":
            self.paths.append(path)
            return self._tasks
        return super().get(path, **params)


def _motion_pull(gate, **kwargs):
    client = DigestClient(
        matters={"value": [{"id": "mat-1", "number": "2026-0142"}]},
        memos_by_matter={"mat-1": {"value": []}},
        **kwargs,
    )
    return client, gate.pull_facts_payload(client, "motion-calendar-tracker")


def test_the_motion_tracker_row_carries_the_digest_on_the_wake_line() -> None:
    gate = _load_gate()
    client, payload = _motion_pull(gate)
    (row,) = payload["matters"]
    assert len(row["facts_digest"]) == 12
    assert row["facts_digest"] == gate.facts_digest(EVENTS, TASKS)
    assert ("/events", {"MatterId": "mat-1", "ExcludeDeletedEvents": True, "Limit": 500}) in client.params
    assert ("/tasks", {"MatterId": "mat-1", "IsCompleted": False, "Limit": 500}) in client.params
    # Atoms only: no subject, place or date reaches the payload.
    serialized = json.dumps(payload)
    assert "MSJ" not in serialized and "Dept 31" not in serialized


def test_a_partial_or_unreadable_facts_read_gives_no_digest_and_keeps_the_row() -> None:
    gate = _load_gate()
    full = {"value": [dict(EVENTS[0], id=f"e{i}") for i in range(gate._DIGEST_PAGE_LIMIT)]}
    for kwargs in ({"events": full}, {"tasks": {"weird": "envelope"}}):
        _, payload = _motion_pull(gate, **kwargs)
        (row,) = payload["matters"]
        assert "facts_digest" not in row and row["matterId"] == "mat-1"


def test_a_skill_outside_the_digest_list_reads_no_events_or_tasks() -> None:
    gate = _load_gate()
    client = DigestClient(
        matters={"value": [{"id": "mat-1", "number": "2026-0142"}]},
        memos_by_matter={"mat-1": {"value": []}},
    )
    payload = gate.pull_facts_payload(client, "service-confirmation-watcher")
    assert client.paths == ["/matters", "/matters/mat-1/memos"]
    assert "facts_digest" not in payload["matters"][0]


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


def test_the_snippet_receives_the_seats_label(stub_connector, tmp_path, monkeypatch) -> None:
    """End to end through the subprocess: the label read from customer.yaml in
    this process reaches the connector-venv pull as argv, and a renamed routine's
    note is found by it."""
    gate = _load_gate()
    config = tmp_path / "customer.yaml"
    config.write_text("routine_names:\n  motion-calendar-tracker: 'Hearings and motions'\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(config))
    stub_connector({"matters": {"value": [{"id": "mat-1", "number": "2026-0142"}]}, "memos": {"mat-1": RENAMED}})
    count, facts = gate.fetch_memo_facts("motion-calendar-tracker")
    assert count == 1
    assert facts["matters"][0]["last_surface"] == "2026-09-29"


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
