"""Tests for task-list-keeper: classification, planning, rendering, and the
pre_run's wake / suppress contract. No network, no seat: the pull is a dict in
the connector snippet's own output shape, built over the captured live task
list (``deadline-miss-escalator/tests/fixtures/live-pull-2026-08-24.json``).

Run::

    cd operator && python3 -m pytest skills/task-list-keeper -q
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import re
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_FIXTURE = _HERE.parent / "deadline-miss-escalator" / "tests" / "fixtures" / "live-pull-2026-08-24.json"


def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pre_run = _load("pre_run.py", "tlk_pre_run_under_test")
classify = _load("classify.py", "tlk_classify_under_test")
ledger = _load("casework_ledger.py", "tlk_ledger_under_test")
lines = _load("lines.py", "tlk_lines_under_test")

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)

M101 = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"
M104 = "cd710b6b-a7ae-44b0-bb8a-be79e8c5d351"
M102 = "404b292e-ec0f-4c12-aa53-3ea27784cd0e"
M001 = "54bc1371-5c82-47ae-b722-da1b29e79ef5"
NUMBERS = {M101: "2026-PI-101", M104: "2026-PI-104", M102: "2026-PI-102", M001: "PI-2026-0001"}

#: The three July rehearsal tasks on 2026-PI-101 the pilot's config lists.
LEGACY = [
    "1f5546c6-2e64-4879-b5c8-8fb7c09d0038",
    "1ec31561-1136-4986-9692-1457dae9513a",
    "95de85b8-7b9e-4d06-89f8-0ad88c8ce1ad",
]

ATTY = {"staff_id": "s-atty", "email": "atty@firm.test", "enabled": True, "former": False}
PARA = {"staff_id": "s-para", "email": "para@firm.test", "enabled": True, "former": False}


def _yaml(**case_manager) -> dict:
    block = {
        "own_tasks": {"level": "handles", "legacy_task_ids": LEGACY},
        "task_cleanup": {"level": "prepares", "keep_quiet_days": 30, "max_lines": 30},
        "quiet": {"level": "handles"},
    }
    block.update(case_manager)
    return {
        "scope": {"inbound_allow_from": ["@firm.test"]},
        "escalation": {"red_flag_recipients": ["triage@firm.test"], "case_alert_routing": {"mode": "matter_staff"}},
        "personas": [{"cron": [{"skill": "task-list-keeper", "schedule": "0 7 * * 1"}]}],
        "case_manager": {k: v for k, v in block.items() if v is not None},
    }


def _neutral(subject: str) -> str:
    """The live capture names a real provider; this public repo may not (the
    medchron scrub gate). The provider is swapped for a fictional one at load
    time, so no literal copy of it is written here."""
    head, sep, _provider = subject.partition(" from ")
    return f"{head} from Valley Imaging" if sep else subject


def _raw(files=None, events=None, status=None, extra_tasks=()) -> dict:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    tasks = []
    for t in list(fixture["tasks"]["value"]) + list(extra_tasks):
        mid = t["matter"]["id"]
        tasks.append({**t, "subject": _neutral(t["subject"]), "matterNumber": NUMBERS[mid]})
    matters = {}
    for mid in NUMBERS:
        matters[mid] = {
            "status": (status or {}).get(mid, "Open"),
            "responsible": ATTY,
            "assisting": [PARA],
            "files": (files or {}).get(mid, []),
            "events": (events or {}).get(mid, []),
        }
    return {"tasks": tasks, "matters": matters, "openTaskCount": len(tasks), "matterCount": len(matters)}


class _Writer:
    def __init__(self):
        self.rows = []

    async def write_suppressed_wake(self, **kw):
        self.rows.append(kw)


def _run(raw, yaml_doc, tmp_path, monkeypatch, *, casework_events=(), escalation_events=()):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    writer = _Writer()
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = asyncio.new_event_loop().run_until_complete(
            pre_run.run_once(
                pull_fn=lambda: raw,
                customer_yaml=yaml_doc,
                writer_factory=lambda: writer,
                today=TODAY,
                now=NOW,
                casework_events=list(casework_events),
                escalation_events=list(escalation_events),
            )
        )
    assert code == 0
    out = json.loads(buf.getvalue().strip().splitlines()[-1])
    env_path = tmp_path / ".smd" / "pre_run" / "task-list-keeper.casework.json"
    envelope = json.loads(env_path.read_text()) if env_path.exists() else None
    return out, envelope, writer


def _items(envelope):
    return [i for m in envelope["messages"] for i in m["items"]]


POS_FILE = {"name": "Proof of Service - Amended Special Interrogatories Set Two.pdf", "date": "2026-07-09T10:00:00Z"}

#: A firm task on the closed-matter test matter, with no money or court word.
INTAKE = {
    "id": "0c0c0c0c-0000-4000-8000-000000000001",
    "subject": "Send the intake packet to the client",
    "dueDate": "2026-07-20T17:00:00Z",
    "matter": {"id": M102},
}


def test_absent_case_manager_block_is_a_quiet_heartbeat(tmp_path, monkeypatch):
    doc = _yaml()
    doc.pop("case_manager")
    out, envelope, writer = _run(_raw(), doc, tmp_path, monkeypatch)
    assert out == {"wakeAgent": False}
    assert envelope is None
    assert writer.rows[0]["decision_basis"] == "case_manager_unauthored"


def test_the_lien_task_is_never_listed(tmp_path, monkeypatch):
    """PI-104's lien payoff chase is at_stake: never proposed, never closed."""
    out, envelope, _ = _run(_raw(), _yaml(), tmp_path, monkeypatch)
    assert out["casework_expected"] is True
    assert all(i["task_id"] != "843e1ade-2b39-4a11-9ae3-952eb78d856d" for i in _items(envelope))
    for m in envelope["messages"]:
        assert all(c["task_id"] != "843e1ade-2b39-4a11-9ae3-952eb78d856d" for c in m["closes"])


def test_classify_at_stake_beats_done_evidence():
    task = classify.TaskFacts("t1", "m1", "Serve lien notice on Medi-Cal", date(2026, 7, 1))
    matter = classify.MatterFacts("m1", files=(classify.FileRef("Proof of Service.pdf", date(2026, 7, 5)),))
    verdict = classify.classify(task, matter, today=TODAY, window_days=14, duplicates=set())
    assert verdict.klass == "at_stake"


def test_classify_unread_calendar_is_at_stake():
    task = classify.TaskFacts("t1", "m1", "Send the intake packet", date(2026, 7, 1))
    verdict = classify.classify(
        task, classify.MatterFacts("m1", calendar_read=False), today=TODAY, window_days=14, duplicates=set()
    )
    assert verdict.klass == "at_stake" and verdict.reason == "calendar_unread"


def test_classify_court_date_in_window_is_at_stake():
    task = classify.TaskFacts("t1", "m1", "Send the intake packet", date(2026, 7, 1))
    matter = classify.MatterFacts("m1", court_days=(date(2026, 10, 2),))
    assert classify.classify(task, matter, today=TODAY, window_days=14, duplicates=set()).klass == "at_stake"


def test_classify_records_evidence_needs_a_shared_word():
    task = classify.TaskFacts("t1", "m1", "Request certified medical records from Valley Imaging", date(2026, 6, 30))
    other = classify.MatterFacts("m1", files=(classify.FileRef("Coastal Imaging records.pdf", date(2026, 7, 2)),))
    mine = classify.MatterFacts("m1", files=(classify.FileRef("Valley Imaging records.pdf", date(2026, 7, 2)),))
    assert classify.classify(task, other, today=TODAY, window_days=14, duplicates=set()).klass == "open"
    verdict = classify.classify(task, mine, today=TODAY, window_days=14, duplicates=set())
    assert verdict.klass == "done" and verdict.evidence == ("document:records:2026-07-02",)


def test_classify_document_before_the_task_is_not_evidence():
    task = classify.TaskFacts("t1", "m1", "Confirm service of the complaint", date(2026, 7, 14))
    matter = classify.MatterFacts("m1", files=(classify.FileRef("Proof of Service.pdf", date(2026, 7, 1)),))
    assert classify.classify(task, matter, today=TODAY, window_days=14, duplicates=set()).klass == "open"


def test_duplicates_keep_the_latest_copy():
    a = classify.TaskFacts("a", "m1", "[Operator] Confirm the RFA set", date(2026, 7, 1))
    b = classify.TaskFacts("b", "m1", "confirm the rfa set!", date(2026, 7, 8))
    c = classify.TaskFacts("c", "m2", "Confirm the RFA set", date(2026, 7, 1))
    assert classify.duplicate_ids([a, b, c]) == {"a"}


def test_own_done_task_closes_and_the_rest_are_handed_over_once(tmp_path, monkeypatch):
    raw = _raw(files={M101: [POS_FILE]})
    out, envelope, _ = _run(raw, _yaml(), tmp_path, monkeypatch)
    closes = [c for m in envelope["messages"] for c in m["closes"]]
    # Only the task the proof of service names ("Amended Special Interrogatories
    # Set Two") closes; one document never closes every service task.
    assert {c["task_id"] for c in closes} == {"1ec31561-1136-4986-9692-1457dae9513a"}
    for c in closes:
        assert c["reason"] == "document_on_file" and c["evidence"] == ["document:proof_of_service:2026-07-09"]
        assert c["staff_id"] == "s-atty"
        assert c["item_key"] == ledger.item_key(matter_id=c["matter_id"], kind="task", source_id=c["task_id"])
    handovers = [i for i in _items(envelope) if i["task_id"] in LEGACY and i["payload"]["action"] == "reassign"]
    for h in handovers:
        assert h["event"] == "proposed" and h["payload"]["to_staff_id"] == "s-para"
    para = [m for m in envelope["messages"] if m["recipients"] == ["para@firm.test"]]
    assert para, "the handover goes to the matter's assisting staff"


def _raise(task_id, matter_id, event="proposed", n=1, action="reassign", verdict=None, ts="2026-09-21T14:00:00Z"):
    key = ledger.item_key(matter_id=matter_id, kind="task", source_id=task_id)
    rows = [
        {
            "item_key": key,
            "matter_id": matter_id,
            "kind": "task",
            "source_id": task_id,
            "event": event,
            "n": n,
            "thread_ref": "thread-1",
            "dispatch_ref": "a" * 32,
            "payload": {
                "action": action,
                "class": "open",
                "staff_id": "s-atty",
                "to_staff_id": "s-para",
                "evidence": [],
            },
            "ts": ts,
        }
    ]
    if verdict:
        rows.append(
            {
                "item_key": key,
                "matter_id": matter_id,
                "kind": "task",
                "source_id": task_id,
                "event": verdict,
                "n": n,
                "thread_ref": "thread-1",
                "ts": "2026-09-22T14:00:00Z",
            }
        )
    return rows


def test_a_second_run_never_repeats_a_handover(tmp_path, monkeypatch):
    first, envelope, _ = _run(_raw(), _yaml(), tmp_path, monkeypatch)
    raised = [i for i in _items(envelope) if i["task_id"] in LEGACY]
    assert raised
    rows = [r for i in raised for r in _raise(i["task_id"], i["matter_id"], event=i["event"], n=i["n"])]
    _out, again, _ = _run(_raw(), _yaml(), tmp_path / "second", monkeypatch, casework_events=rows)
    assert not [i for i in _items(again or {"messages": []}) if i["task_id"] in LEGACY]


def test_a_held_line_keeps_the_task_quiet(tmp_path, monkeypatch):
    task_id = "57d7a3b8-a91b-4465-ad44-335e88dc4934"
    rows = _raise(task_id, M001, action="keep", verdict="held")
    _out, envelope, _ = _run(_raw(), _yaml(), tmp_path, monkeypatch, casework_events=rows)
    assert all(i["task_id"] != task_id for i in _items(envelope))


def test_firm_open_task_is_a_keep_proposal_and_done_is_a_close(tmp_path, monkeypatch):
    raw = _raw(files={M001: [{"name": "Valley Imaging certified records.pdf", "date": "2026-07-03"}]})
    _out, envelope, _ = _run(raw, _yaml(), tmp_path, monkeypatch)
    by_id = {i["task_id"]: i for i in _items(envelope)}
    records = by_id["57d7a3b8-a91b-4465-ad44-335e88dc4934"]
    assert records["payload"]["action"] == "close" and records["payload"]["class"] == "done"
    assert "a records file dated 2026-07-03 is on the matter" in records["line"]
    verification = by_id["be08487c-1d5c-4c29-a52b-987876336ad9"]
    assert verification["payload"]["action"] == "keep" and "30 days" in verification["line"]
    # Service of a summons carries a court deadline: at_stake, never listed.
    assert "e1f6b2db-fca3-4981-9f7e-4043f2b3916e" not in by_id


def test_surfaces_level_offers_no_write(tmp_path, monkeypatch):
    doc = _yaml(own_tasks={"level": "surfaces", "legacy_task_ids": LEGACY}, task_cleanup={"level": "surfaces"})
    _out, envelope, _ = _run(_raw(files={M101: [POS_FILE]}), doc, tmp_path, monkeypatch)
    assert envelope is not None
    for m in envelope["messages"]:
        assert m["closes"] == []
        for i in m["items"]:
            assert i["event"] == "named" and i["payload"]["action"] == "keep"


def test_closed_matter_tasks_are_stale(tmp_path, monkeypatch):
    doc = _yaml(own_tasks=None)
    raw = _raw(status={M102: "Closed"}, extra_tasks=[INTAKE])
    _out, envelope, _ = _run(raw, doc, tmp_path, monkeypatch)
    item = next(i for i in _items(envelope) if i["task_id"] == INTAKE["id"])
    assert item["payload"]["class"] == "stale" and item["payload"]["action"] == "close"


def test_rendering_is_deterministic_and_gate_safe(tmp_path, monkeypatch):
    raw = _raw(files={M101: [POS_FILE]})
    _o1, first, _ = _run(raw, _yaml(), tmp_path / "a", monkeypatch)
    _o2, second, _ = _run(raw, _yaml(), tmp_path / "b", monkeypatch)
    first.pop("started_at")
    second.pop("started_at")
    assert first == second
    handoff = json.loads((tmp_path / "a" / ".smd" / "pre_run" / "task-list-keeper.json").read_text())
    paired = {r["matterNumber"]: set(r["dates"]) for r in handoff["records"]}
    for m in first["messages"]:
        assert len(m["items"]) <= 30 and len(m["subject"]) <= 500
        texts = [m["lead"], m["footer"]] + [i["line"] for i in m["items"]] + [c["line"] for c in m["closes"]]
        for text in texts:
            assert "—" not in text and "ACK-" not in text
        for i in m["items"]:
            assert len(i["line"]) <= lines.ITEM_MAX
            number = i["group"].removeprefix("matter ")
            for day in re.findall(r"\d{4}-\d{2}-\d{2}", i["line"]):
                assert day in paired[number], (day, number)
        for c in m["closes"]:
            assert len(c["line"]) <= lines.CLOSE_MAX
        assert not re.search(r"\b\d+\b", m["footer"]), "no example numbers in the reply line"


def test_overflow_waits_for_the_next_review(tmp_path, monkeypatch):
    doc = _yaml(own_tasks=None, task_cleanup={"level": "prepares", "max_lines": 2})
    _out, envelope, _ = _run(_raw(), doc, tmp_path, monkeypatch)
    msg = envelope["messages"][0]
    assert len(msg["items"]) == 2
    assert "more tasks wait for the next review" in msg["lead"] or "more task waits" in msg["lead"]
    assert "Monday" in msg["lead"]


def test_done_since_last_time_and_its_memo(tmp_path, monkeypatch):
    task_id = "d1daf4fd-0000-0000-0000-000000000001"
    key = ledger.item_key(matter_id=M001, kind="task", source_id=task_id)
    base = {"item_key": key, "matter_id": M001, "kind": "task", "source_id": task_id}
    rows = [
        {
            **base,
            "event": "closed_by_record",
            "payload": {"action": "close", "class": "done", "evidence": ["document:proof_of_service:2026-07-09"]},
            "ts": "2026-09-21T14:00:00Z",
        },
        {**base, "event": "completed", "tool_call_id": "call-1", "ts": "2026-09-21T14:01:00Z"},
    ]
    _out, envelope, _ = _run(_raw(), _yaml(), tmp_path, monkeypatch, casework_events=rows)
    since = [d for m in envelope["messages"] for d in m["done_since"]]
    assert since == [
        {
            "item_key": key,
            "matter_id": M001,
            "task_id": task_id,
            "line": "matter PI-2026-0001: a task I closed on 2026-09-21, a proof of service dated 2026-07-09 was on file",
        }
    ]
    assert envelope["memos"] == [
        {
            "matter_id": M001,
            "text": "Task list upkeep: I closed 1 task on this matter that the record showed were done (a proof of service dated 2026-07-09).",
        }
    ]


def test_unroutable_matters_send_nothing(tmp_path, monkeypatch):
    doc = _yaml()
    doc["scope"] = {"inbound_allow_from": ["@elsewhere.test"]}
    doc["escalation"] = {"case_alert_routing": {"mode": "matter_staff"}}
    out, envelope, writer = _run(_raw(), doc, tmp_path, monkeypatch)
    assert out == {"wakeAgent": False}
    assert writer.rows[0]["decision_basis"] == "nothing_to_review"


def test_pull_failure_is_recorded_not_silent(tmp_path, monkeypatch):
    out, _env, writer = _run({"tasksError": "401"}, _yaml(), tmp_path, monkeypatch)
    assert out == {"wakeAgent": False}
    assert writer.rows[0]["decision_basis"] == "pull_failed"


@pytest.mark.parametrize("subject", ["Chase Medi-Cal (DHCS) final lien payoff demand", "URGENT: call the adjuster"])
def test_money_and_priority_words_are_at_stake(subject):
    task = classify.TaskFacts("t", "m", subject, date(2026, 7, 1))
    assert (
        classify.classify(task, classify.MatterFacts("m"), today=TODAY, window_days=14, duplicates=set()).klass
        == "at_stake"
    )


def test_every_row_the_envelope_implies_passes_the_ledger_validator(tmp_path, monkeypatch):
    """The overlay refuses a whole envelope when one payload fails the casework
    ledger's own validator; run that validator (the vendored twin) over every
    item and every close this skill writes."""
    records = {"name": "Valley Imaging certified records.pdf", "date": "2026-07-03"}
    _out, envelope, _ = _run(_raw(files={M101: [POS_FILE], M001: [records]}), _yaml(), tmp_path, monkeypatch)
    checked = 0
    for message in envelope["messages"]:
        numbers = [i["n"] for i in message["items"]]
        assert numbers == list(range(1, len(numbers) + 1))
        for item in message["items"]:
            ledger._validate_payload(item["event"], item["payload"], "task")
            assert item["payload"]["action"] == "keep" or item["payload"]["staff_id"]
            checked += 1
        for close in message["closes"]:
            payload = {
                "action": "close",
                "class": "done",
                "staff_id": close["staff_id"],
                "evidence": close["evidence"],
                "reason": close["reason"],
            }
            ledger._validate_payload("closed_by_record", payload, "task")
            checked += 1
    assert checked >= 3
