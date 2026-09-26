"""The casework ledger's rules, driven directly (no broker).

Each refusal here is a door into the firm's record: a close on money or a court
date, an approval nobody was asked for, a write nothing authorized, a raise that
reached nobody. Every test that expects a refusal first shows the same event
accepted with the one fact changed, so a refusal that fires on everything cannot
pass (Law 12: a check that cannot fail measured nothing).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import casework_ledger as cl
from workspace_broker import escalation_ledger as el

MATTER = "2026-PI-106"
TASK = "5b1f0e2a-7c44-4c1e-9d0a-2f6b8e1c3a90"
THREAD = "thr_casework_0925"
REF = "5f0c3a9e2b7d4e1f8a6c0b3d9e2f7a41"
YES = lambda _event: True  # noqa: E731 - a witness stub is clearest as a lambda
NO = lambda _event: False  # noqa: E731 - a witness stub is clearest as a lambda


def _base(kind: str = "task", source_id: str = TASK) -> dict:
    return {
        "skill": "task-list-keeper",
        "matter_id": MATTER,
        "kind": kind,
        "source_id": source_id,
        "item_key": cl.item_key(matter_id=MATTER, kind=kind, source_id=source_id),
        "session_id": "cron_1b149663f32f_20260925_140005",
    }


def _payload(**over) -> dict:
    payload = {"action": "close", "class": "done", "staff_id": "st-1", "reason": "proof of service on file"}
    payload["evidence"] = ["Proof of Service (2026-07-10)"]
    payload.update(over)
    return payload


def _raise(event: str = "proposed", n: int = 1, **over) -> dict:
    row = {**_base(), "event": event, "n": n, "dispatch_ref": REF, "thread_ref": THREAD, "payload": _payload()}
    row.update(over)
    return row


def _verdict(event: str = "approved", n: int = 1, thread: str = THREAD, **over) -> dict:
    return {**_base(), "event": event, "n": n, "thread_ref": thread, **over}


def _check(existing: list[dict], event: dict, *, send=YES, audit=YES) -> None:
    cl.validate_append(existing, event, send_witness=send, audit_witness=audit)


def _ledger(*events: dict) -> list[dict]:
    rows: list[dict] = []
    for event in events:
        _check(rows, event)
        rows.append(cl.stamp_event(event))
    return rows


# --- identity ---------------------------------------------------------------


def test_item_key_is_the_escalation_key_of_the_namespaced_id() -> None:
    assert cl.item_key(matter_id=MATTER, kind="task", source_id=TASK) == el.item_key(
        MATTER, "__cm_task:" + TASK, None, None
    )
    assert cl.item_key(matter_id=MATTER, kind="date", source_id=TASK) == el.item_key(
        MATTER, "__cm_date:" + TASK, None, None
    )
    # The namespace keeps a casework key off the escalation key of the raw id.
    assert cl.item_key(matter_id=MATTER, kind="task", source_id=TASK) != el.item_key(MATTER, TASK, None, None)


def test_item_key_normalizes_and_refuses_what_it_cannot_key() -> None:
    assert cl.item_key(matter_id=f" {MATTER} ", kind="task", source_id=TASK.upper()) == cl.item_key(
        matter_id=MATTER, kind="task", source_id=TASK
    )
    with pytest.raises(ValueError, match="kind"):
        cl.item_key(matter_id=MATTER, kind="deadline", source_id=TASK)
    with pytest.raises(ValueError, match="source_id"):
        cl.item_key(matter_id=MATTER, kind="task", source_id="  ")
    with pytest.raises(TypeError):
        cl.item_key(MATTER, "task", TASK)  # type: ignore[misc]  # keyword-only on purpose


def test_a_key_not_derived_from_the_row_is_refused() -> None:
    _check([], _raise())
    with pytest.raises(ValueError, match="item_key must be derived"):
        _check([], _raise(item_key=cl.item_key(matter_id=MATTER, kind="task", source_id="other")))


def test_unknown_fields_are_refused_per_event() -> None:
    _check([], _raise())
    with pytest.raises(ValueError, match="unknown fields"):
        _check([], _raise(snooze_days=7))
    with pytest.raises(ValueError, match="unknown fields"):
        _check([], {**_base(), "event": "kept", "n": 1})


# --- raises -----------------------------------------------------------------


@pytest.mark.parametrize("event", ["proposed", "closed_by_record"])
def test_a_close_on_an_at_stake_item_is_refused(event: str) -> None:
    ok = _raise() if event == "proposed" else {**_base(), "event": event, "payload": _payload()}
    _check([], ok)
    with pytest.raises(ValueError, match="at_stake"):
        _check([], {**ok, "payload": _payload(**{"class": "at_stake"})})


def test_at_stake_may_still_be_offered_for_reassignment() -> None:
    _check([], _raise(payload=_payload(action="reassign", to_staff_id="st-2", **{"class": "at_stake"})))


def test_a_raise_nobody_received_is_refused() -> None:
    _check([], _raise(), send=YES)
    with pytest.raises(ValueError, match="dispatched no message"):
        _check([], _raise(), send=NO)


@pytest.mark.parametrize("drop", ["thread_ref", "dispatch_ref", "n"])
def test_a_raise_without_its_thread_number_or_dispatch_is_refused(drop: str) -> None:
    row = _raise()
    row.pop(drop)
    with pytest.raises(ValueError, match="could not tie this raise"):
        _check([], row)


def test_a_handover_is_never_repeated() -> None:
    rows = _ledger(_raise("named"))
    _check(rows, _raise("proposed", n=2))
    with pytest.raises(ValueError, match="never repeated"):
        _check(rows, _raise("named", n=2))


def test_payload_shape_is_enforced() -> None:
    with pytest.raises(ValueError, match="to_staff_id"):
        _check([], _raise(payload=_payload(action="reassign")))
    with pytest.raises(ValueError, match="payload.action"):
        _check([], _raise(payload=_payload(action="delete")))
    with pytest.raises(ValueError, match="evidence"):
        _check([], _raise(payload=_payload(evidence=["x" * 201])))
    with pytest.raises(ValueError, match="payload.step is required"):
        _check([], _raise(payload=_payload(action="step")))


def _step(**over) -> dict:
    step = {"catalog_id": "records_refresh:valley-imaging", "skill": "medical-records-chaser", "level": "prepares"}
    step["params"] = {"provider": "Valley Imaging"}
    step.update(over)
    return step


def test_a_step_names_a_bounded_catalog_entry() -> None:
    date_raise = {**_raise("briefed"), **_base("date", "evt-okafor-fsc")}
    _check([], {**date_raise, "payload": _payload(action="step", **{"class": "open"}, step=_step())})
    for bad in (_step(level="always"), _step(skill="Rm -rf"), _step(params={"p": 3}), _step(extra=1), "catalog"):
        with pytest.raises(ValueError, match="payload.step is"):
            _check([], {**date_raise, "payload": _payload(action="step", **{"class": "open"}, step=bad)})


def test_closed_by_record_needs_a_done_task_and_its_evidence() -> None:
    ok = {**_base(), "event": "closed_by_record", "payload": _payload()}
    _check([], ok, send=NO)  # not a message: no send witness needed
    for payload in (_payload(evidence=[]), _payload(**{"class": "stale"}), _payload(action="keep")):
        with pytest.raises(ValueError, match="record shows a task is done"):
            _check([], {**ok, "payload": payload})
    with pytest.raises(ValueError, match="record shows a task is done"):
        _check([], {**ok, **_base("date", "evt-1")})


# --- verdicts ---------------------------------------------------------------


def test_an_approval_answers_a_line_that_was_raised_on_that_thread() -> None:
    rows = _ledger(_raise())
    _check(rows, _verdict())
    with pytest.raises(ValueError, match="no line 2 was raised"):
        _check(rows, _verdict(n=2))
    with pytest.raises(ValueError, match="no line 1 was raised"):
        _check(rows, _verdict(thread="thr_some_other_digest"))
    with pytest.raises(ValueError, match="no line 1 was raised"):
        _check([], _verdict())


def test_a_line_is_answered_once() -> None:
    rows = _ledger(_raise(), _verdict("held"))
    with pytest.raises(ValueError, match="already answered"):
        _check(rows, _verdict("approved"))


def test_decided_by_is_a_name_and_a_sender_key() -> None:
    rows = _ledger(_raise())
    _check(rows, _verdict(decided_by={"name": "Christa Ramos", "key": "a" * 64}))
    with pytest.raises(ValueError, match="decided_by"):
        _check(rows, _verdict(decided_by={"name": "Christa Ramos"}))


# --- outcomes ---------------------------------------------------------------


def _done(call: str = "toolu_01") -> dict:
    return {**_base(), "event": "completed", "tool_call_id": call}


def test_a_write_needs_an_authorization_and_consumes_it() -> None:
    with pytest.raises(ValueError, match="nothing authorized a write"):
        _check(_ledger(_raise()), _done())
    rows = _ledger(_raise(), _verdict(), _done())
    with pytest.raises(ValueError, match="nothing authorized a write"):
        _check(rows, _done("toolu_02"))
    rows = _ledger({**_base(), "event": "closed_by_record", "payload": _payload()}, _done())
    assert cl.derive_state(rows)[rows[0]["item_key"]].completed_via == "closed_by_record"


def test_approving_a_keep_line_licenses_no_write() -> None:
    rows = _ledger(_raise(payload=_payload(action="keep", **{"class": "open"})), _verdict())
    with pytest.raises(ValueError, match="nothing authorized a write"):
        _check(rows, _done())


def test_completed_needs_the_update_task_call_on_file_once() -> None:
    rows = _ledger(_raise(), _verdict())
    with pytest.raises(ValueError, match="holds no successful"):
        _check(rows, _done(), audit=NO)
    other = _base(source_id="task-two")
    rows = _ledger(_raise(), _verdict(), _done(), {**_raise(), **other}, {**_verdict(), **other})
    _check(rows, {**other, "event": "completed", "tool_call_id": "toolu_02"})
    with pytest.raises(ValueError, match="already backs another casework row"):
        _check(rows, {**other, "event": "completed", "tool_call_id": "toolu_01"})


def test_a_failed_write_is_recorded_and_ends_the_license() -> None:
    rows = _ledger(_raise(), _verdict(), {**_base(), "event": "write_failed", "error": "exposure ceiling refused"})
    state = cl.derive_state(rows)[rows[0]["item_key"]]
    assert state.write_failed and not state.completed and state.authorization is None
    with pytest.raises(ValueError, match="error"):
        _check(_ledger(_raise(), _verdict()), {**_base(), "event": "write_failed"})


# --- the rest ---------------------------------------------------------------


def test_kept_needs_a_hold_and_quiets_for_the_window() -> None:
    kept = {**_base(), "event": "kept"}
    with pytest.raises(ValueError, match="no held line"):
        _check(_ledger(_raise(), _verdict()), kept)
    rows = _ledger(_raise(), _verdict("held"), kept)
    state = cl.derive_state(rows)[rows[0]["item_key"]]
    today = state.last_kept_date
    assert today is not None
    assert cl.is_kept_quiet(state, today, 30)
    assert not cl.is_kept_quiet(state, date.fromordinal(today.toordinal() + 30), 30)


def test_mention_follows_a_record_close_once() -> None:
    mention = {**_base(), "event": "mentioned"}
    with pytest.raises(ValueError, match="has neither"):
        _check([], mention)
    rows = _ledger({**_base(), "event": "closed_by_record", "payload": _payload()}, _done())
    state = cl.derive_state(rows)[rows[0]["item_key"]]
    assert cl.needs_mention(state)
    with pytest.raises(ValueError, match="dispatched no message"):
        _check(rows, mention, send=NO)
    _check(rows, mention)
    rows = [*rows, cl.stamp_event(mention)]
    assert not cl.needs_mention(cl.derive_state(rows)[rows[0]["item_key"]])


def test_a_step_starts_only_on_an_approved_step_line() -> None:
    base = _base("date", "evt-okafor-fsc")
    briefed = {**_raise("briefed"), **base, "payload": _payload(action="step", **{"class": "open"}, step=_step())}
    started = {**base, "event": "step_started", "n": 1, "thread_ref": THREAD}
    with pytest.raises(ValueError, match="approved step line"):
        _check(_ledger(briefed), started)
    rows = _ledger(briefed, {**_verdict(), **base})
    _check(rows, started)
    assert cl.pending_decisions(cl.derive_state(rows)[base["item_key"]]) == []


def test_read_ledger_skips_what_it_cannot_parse(tmp_path: Path) -> None:
    path = tmp_path / "casework-ledger.jsonl"
    good = cl.stamp_event(_raise())
    cl.append_line(str(path), good)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n" + json.dumps({"event": "invented", "item_key": "x"}) + "\n\n")
    assert cl.read_ledger(str(path)) == [good]
    assert cl.read_ledger(str(tmp_path / "absent.jsonl")) == []
    assert oct(path.stat().st_mode & 0o777) == "0o644"


def test_the_broker_stamps_ts_id_and_version() -> None:
    stamped = cl.stamp_event({**_raise(), "ts": "2020-01-01T00:00:00Z", "id": "forged", "v": 99})
    assert stamped["ts"].startswith("20") and stamped["ts"] != "2020-01-01T00:00:00Z"
    assert stamped["id"] != "forged" and len(stamped["id"]) == 26 and stamped["v"] == cl.SCHEMA_VERSION
