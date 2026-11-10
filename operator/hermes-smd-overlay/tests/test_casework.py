"""Case-manager tools: the task review, the date-prep brief, and the reply to either.

The contract sentences (ss-console ``docs/specs/operator/case-manager-deadline-work.md``):

* a reply "yes except 2" to a task review writes ``approved`` for every line but 2
  and ``held`` for 2, and the task writes that follow are EXACTLY the approved
  tasks, taken from the raise rows, never from the model;
* a close the firm's level permits happens before the review is rendered, and
  the review says "Closed just now" only for what actually closed;
* a brief carries at most two decisions, each from the envelope's closed catalog;
* every refusal writes nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from shared import casework_acts, casework_ledger, inbound
from shared.casework_acts import CASEWORK_ACTS, TaskWrite
from tests.conftest import load_plugin

plugin = load_plugin("hermes-smd-escalation")
casework = plugin.casework
reply_items = plugin.reply_items

SESSION = "cron_job-tlk_20260928_080500"
REPLY_SESSION = "sess-reply-cw"
THREAD = "thread-review-0928"
REF = "e" * 32
ATTORNEY = "atty@firm.example"
PARALEGAL = "para@firm.example"
NOW = datetime(2026, 9, 28, 15, 6, tzinfo=timezone.utc)


def _key(task: str, matter: str = "m-104", kind: str = "task") -> str:
    return casework_ledger.item_key(matter_id=matter, kind=kind, source_id=task)


K1, K2, K3, K9 = _key("t-1"), _key("t-2"), _key("t-3"), _key("t-9")
K_DATE = _key("ev-1", "m-105", "date")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(inbound, "SESSION_INBOUND_ORIGIN", inbound.SessionInboundOrigin())
    for session in (SESSION, REPLY_SESSION):
        CASEWORK_ACTS.clear(session)
    from plugin_hermes_smd_escalation import casework_rules

    for session in (SESSION, REPLY_SESSION):
        casework_rules._REPLIES.pop(session)
    rows: list[dict] = []
    CASEWORK_ACTS.set_writer(lambda event: rows.append(event) or {"ok": True})
    yield rows
    CASEWORK_ACTS.set_writer(None)


@pytest.fixture
def rows(_clean):
    return _clean


# ---------------------------------------------------------------------------
# parse_reply_verdicts (pure)
# ---------------------------------------------------------------------------


def _verdicts(text):
    v = reply_items.parse_reply_verdicts(text)
    return (sorted(v["approve"]), sorted(v["hold"]), v["all"], v["conflict"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes to all", ([], [], True, False)),
        ("All", ([], [], True, False)),
        ("yes except 3", ([], [3], True, False)),
        ("all except 3", ([], [3], True, False)),
        ("all but 2", ([], [2], True, False)),
        ("yes to all except 2 and 4", ([], [2, 4], True, False)),
        ("leave 2", ([], [2], False, False)),
        ("keep 2, close the rest? no, just 1", ([1], [2], False, False)),
        ("not 3", ([], [3], False, False)),
        ("skip 4", ([], [4], False, False)),
        ("hold 5", ([], [5], False, False)),
        ("yes on 1", ([1], [], False, False)),
        ("1", ([1], [], False, False)),
        ("yes on 1, leave 2", ([1], [2], False, False)),
        ("leave 2 yes on 3", ([3], [2], False, False)),
        ("yes on 2, leave 2", ([2], [2], False, True)),
        ("Hi all, yes on 2", ([2], [], False, False)),
        # A leaked quoted list is nothing at all.
        ("yes\n> 1. 2026-PI-104: Discovery follow-up", ([], [], False, False)),
        ("thanks\n1. matter 2026-PI-101, due 2026-09-20", ([], [], False, False)),
        # A signature's digits are not lines.
        ("yes on 1\n\nThanks,\nDana\nSuite 200\n(602) 555-1234", ([1], [], False, False)),
        ("2\n-- \nDana | 602 555 1234", ([2], [], False, False)),
        ("call me at 602-555-1234", ([], [], False, False)),
    ],
)
def test_parse_reply_verdicts(text, expected):
    assert _verdicts(text) == expected


@pytest.mark.parametrize("text", ["yes", "Yes please", "ok", "sounds good", "go ahead."])
def test_a_bare_yes_is_flagged(text):
    assert reply_items.parse_reply_verdicts(text)["bare_yes"] is True


@pytest.mark.parametrize("text", ["yes on 1", "yes to all", "thanks", ""])
def test_not_a_bare_yes(text):
    assert reply_items.parse_reply_verdicts(text)["bare_yes"] is False


@pytest.mark.parametrize(
    ("text", "asked"),
    [
        ("Who is handling 3?", [3]),
        ("Can you move 2 to Monday?", [2]),
        ("can you reschedule 1", [1]),
        ("what about 4", [4]),
        ("is 2 done?", [2]),
        ("1 and 3 are fine, but when is 2 due?", [2]),
        ("Could you do all of them by Friday", [0]),
    ],
)
def test_a_question_about_a_line_approves_nothing(text, asked):
    v = reply_items.parse_reply_verdicts(text)
    assert sorted(v["asked"]) == asked
    assert _verdicts(text) == ([], [], False, False)
    assert v["complete"] == set() and v["bare_yes"] is False
    assert reply_items.parse_reply_items(text) == {"all": False, "numbers": []}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 and 3 are fine", ([1, 3], [], False, False)),
        ("approve 2", ([2], [], False, False)),
        ("yes on 1. Thanks!", ([1], [], False, False)),
        # A question with no line in it does not undo the answer beside it.
        ("yes on 1, how was your weekend?", ([1], [], False, False)),
        ("thank you, 2 is done", ([2], [], False, False)),
    ],
)
def test_plain_approvals_still_approve(text, expected):
    assert _verdicts(text) == expected
    assert reply_items.parse_reply_verdicts(text)["asked"] == set()


def test_the_wrapper_is_approve_or_all_minus_holds():
    assert reply_items.parse_reply_items("yes except 3") == {
        "all": True,
        "numbers": [],
        "except": [3],
    }
    assert reply_items.parse_reply_items("yes on 2, leave 2") == {"all": False, "numbers": []}
    assert reply_items.parse_reply_items("1, not 2") == {"all": False, "numbers": [1]}


# ---------------------------------------------------------------------------
# The replay register
# ---------------------------------------------------------------------------


def _write(task_id="t-1", key=K1) -> TaskWrite:
    return TaskWrite(
        task_id=task_id,
        staff_id="staff-atty",
        item_key=key,
        matter_id="m-104",
        skill="task-list-keeper",
        is_completed=True,
    )


def test_a_session_with_no_queue_is_untouched(rows):
    args = {"task_id": "model-chosen", "is_completed": True}
    replay = CASEWORK_ACTS.replay("other-session", casework_acts.UPDATE_TASK_TOOL, args)
    assert replay.act is None and replay.refusal is None
    assert args == {"task_id": "model-chosen", "is_completed": True}


def test_the_model_task_id_is_replaced_by_the_queue_head(rows):
    CASEWORK_ACTS.load(SESSION, [_write()])
    args = {"task_id": "model-chosen", "subject": "rename it", "staff_id": "someone"}
    replay = CASEWORK_ACTS.replay(SESSION, casework_acts.UPDATE_TASK_TOOL, args)
    assert replay.act is not None
    assert args == {"task_id": "t-1", "staff_id": "staff-atty", "is_completed": True}


def test_a_call_beyond_the_queue_is_refused(rows):
    CASEWORK_ACTS.load(SESSION, [_write()])
    CASEWORK_ACTS.replay(SESSION, casework_acts.UPDATE_TASK_TOOL, {})
    CASEWORK_ACTS.on_post_tool(casework_acts.UPDATE_TASK_TOOL, SESSION, {"tool_call_id": "tc-1"})
    replay = CASEWORK_ACTS.replay(SESSION, casework_acts.UPDATE_TASK_TOOL, {"task_id": "t-9"})
    assert replay.refusal == casework_acts.BEYOND_QUEUE


def test_post_tool_writes_completed_with_its_call_id(rows):
    CASEWORK_ACTS.load(SESSION, [_write()])
    CASEWORK_ACTS.replay(SESSION, casework_acts.UPDATE_TASK_TOOL, {})
    CASEWORK_ACTS.on_post_tool(
        casework_acts.UPDATE_TASK_TOOL, SESSION, {"tool_call_id": "tc-1", "result": "{}"}
    )
    [row] = rows
    assert row["event"] == "completed"
    assert row["tool_call_id"] == "tc-1"
    assert row["kind"] == "task" and row["source_id"] == "t-1"
    assert row["item_key"] == K1 and row["ts"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "error"},
        {"error_type": "ToolError"},
        {"result": json.dumps({"error": "400 Bad Request"})},
    ],
)
def test_a_failed_call_writes_write_failed(rows, kwargs):
    CASEWORK_ACTS.load(SESSION, [_write()])
    CASEWORK_ACTS.replay(SESSION, casework_acts.UPDATE_TASK_TOOL, {})
    CASEWORK_ACTS.on_post_tool(casework_acts.UPDATE_TASK_TOOL, SESSION, kwargs)
    assert [r["event"] for r in rows] == ["write_failed"]


def test_an_outcome_that_never_arrived_is_not_a_success(rows):
    CASEWORK_ACTS.load(SESSION, [_write("t-1", K1), _write("t-2", K2)])
    CASEWORK_ACTS.replay(SESSION, casework_acts.UPDATE_TASK_TOOL, {})
    CASEWORK_ACTS.replay(SESSION, casework_acts.UPDATE_TASK_TOOL, {})
    assert [(r["item_key"], r["event"]) for r in rows] == [(K1, "write_failed")]


def test_a_malformed_write_is_never_queued(rows):
    bad = TaskWrite(
        task_id="t-1",
        staff_id="s",
        item_key="k",
        matter_id=None,
        skill="x",
        is_completed=True,
        assignee_ids=("s2",),
    )
    assert CASEWORK_ACTS.load(SESSION, [bad]) == 0


# ---------------------------------------------------------------------------
# Through the trust gate
# ---------------------------------------------------------------------------


trust = load_plugin("hermes-smd-trust")
enforce = trust.enforce


def test_the_gate_replays_and_a_refused_ceiling_is_write_failed(rows, monkeypatch):
    CASEWORK_ACTS.load(SESSION, [_write()])
    monkeypatch.setattr(
        enforce,
        "_evaluate_tool_call",
        lambda *a, **k: {"action": "block", "message": "Refused: internal_write is refused"},
    )
    args = {"task_id": "model-chosen"}
    verdict = enforce.evaluate_tool_call(
        casework_acts.UPDATE_TASK_TOOL, args, "pilot", session_id=SESSION
    )
    assert verdict["action"] == "block"
    assert args["task_id"] == "t-1"
    assert [r["event"] for r in rows] == ["write_failed"]


def test_the_gate_refuses_beyond_the_queue(rows, monkeypatch):
    CASEWORK_ACTS.load(SESSION, [_write()])
    monkeypatch.setattr(enforce, "_evaluate_tool_call", lambda *a, **k: None)
    assert (
        enforce.evaluate_tool_call(casework_acts.UPDATE_TASK_TOOL, {}, "p", session_id=SESSION)
        is None
    )
    trust.on_post_tool_call(
        tool_name=casework_acts.UPDATE_TASK_TOOL,
        session_id=SESSION,
        tool_call_id="tc-7",
        result="{}",
    )
    verdict = enforce.evaluate_tool_call(
        casework_acts.UPDATE_TASK_TOOL, {"task_id": "t-9"}, "p", session_id=SESSION
    )
    assert verdict["action"] == "block"
    assert "no task update waiting" in verdict["message"]
    assert [r["event"] for r in rows] == ["completed"]


def test_other_tools_are_untouched_by_a_queue(rows, monkeypatch):
    CASEWORK_ACTS.load(SESSION, [_write()])
    seen = {}
    monkeypatch.setattr(
        enforce, "_evaluate_tool_call", lambda name, args, *a, **k: seen.update(args)
    )
    enforce.evaluate_tool_call("mcp_smokeball_create_task", {"subject": "x"}, "p", SESSION)
    assert seen == {"subject": "x"}
    assert CASEWORK_ACTS.pending(SESSION) == 1


# ---------------------------------------------------------------------------
# reply_verdicts
# ---------------------------------------------------------------------------


class _Config:
    def sender_on_roster(self, address):
        return address == ATTORNEY


def _raise_row(n, key, task, action="close", *, event="proposed", thread=THREAD, ref=REF, **p):
    payload = {"action": action, "staff_id": "staff-atty", "class": "done", **p}
    return {
        "ts": "2026-09-28T15:07:00Z",
        "skill": "task-list-keeper",
        "matter_id": "m-104",
        "item_key": key,
        "event": event,
        "n": n,
        "kind": "task" if task else "date",
        "source_id": task or "ev-1",
        "dispatch_ref": ref,
        "thread_ref": thread,
        "payload": payload,
    }


def _ledger(tmp_path, rows_):
    path = tmp_path / "casework-ledger.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows_), encoding="utf-8")
    return str(path)


def _reply_origin(text, *, thread=THREAD, address=ATTORNEY):
    inbound.SESSION_INBOUND_ORIGIN.record(
        REPLY_SESSION,
        inbound.InboundOrigin(
            sender_address=address,
            message_id="msg-r",
            conversation_id=thread,
            reply_text=text,
        ),
    )


def _answer(path, broker, fallthrough=lambda: json.dumps({"status": "digest"})):
    return json.loads(
        casework.reply_verdicts(
            session_id=REPLY_SESSION,
            load_config=_Config,
            verified_acker=lambda _s: {"name": "Dana Whitfield", "key": "k" * 64},
            append=lambda event: broker.append(event) or {"ok": True},
            fallthrough=fallthrough,
            casework_ledger_path=path,
        )
    )


def _three(tmp_path):
    return _ledger(
        tmp_path,
        [
            _raise_row(1, K1, "t-1"),
            _raise_row(2, K2, "t-2"),
            _raise_row(3, K3, "t-3", "reassign", to_staff_id="staff-para"),
        ],
    )


def test_yes_except_2_changes_exactly_the_approved_tasks(tmp_path, rows):
    path = _three(tmp_path)
    _reply_origin("yes except 2")
    broker: list = []
    out = _answer(path, broker)
    assert out["status"] == "writes_queued" and out["writes"] == 2
    assert [(e["event"], e.get("n"), e["item_key"]) for e in broker] == [
        ("approved", 1, K1),
        ("held", 2, K2),
        ("kept", None, K2),
        ("approved", 3, K3),
    ]
    verdicts = [e for e in broker if e["event"] in ("approved", "held")]
    assert all(e["decided_by"]["name"] == "Dana Whitfield" for e in verdicts)
    assert all(e["thread_ref"] == THREAD for e in verdicts)
    assert all("dispatch_ref" not in e and "step" not in e for e in verdicts)
    executed = []
    for _ in range(2):
        args = {"task_id": "model-chosen"}
        CASEWORK_ACTS.replay(REPLY_SESSION, casework_acts.UPDATE_TASK_TOOL, args)
        executed.append(args)
        CASEWORK_ACTS.on_post_tool(
            casework_acts.UPDATE_TASK_TOOL, REPLY_SESSION, {"tool_call_id": "tc"}
        )
    assert executed == [
        {"task_id": "t-1", "staff_id": "staff-atty", "is_completed": True},
        {"task_id": "t-3", "staff_id": "staff-atty", "assignee_ids": ["staff-para"]},
    ]
    assert (
        CASEWORK_ACTS.replay(REPLY_SESSION, casework_acts.UPDATE_TASK_TOOL, {}).refusal is not None
    )
    final = _answer(path, broker)
    assert final["status"] == "done"
    assert final["confirmation_text"] == "Got it. Closed 1. Reassigned 3. Leaving 2 as it is."


def test_a_failed_write_is_named_in_the_confirmation(tmp_path, rows):
    path = _three(tmp_path)
    _reply_origin("yes on 1")
    broker: list = []
    _answer(path, broker)
    CASEWORK_ACTS.replay(REPLY_SESSION, casework_acts.UPDATE_TASK_TOOL, {})
    CASEWORK_ACTS.on_post_tool(casework_acts.UPDATE_TASK_TOOL, REPLY_SESSION, {"status": "failed"})
    final = _answer(path, broker)
    assert final["confirmation_text"] == (
        "Got it. I couldn't update 1 in Smokeball just now, so it is unchanged."
    )


@pytest.mark.parametrize(
    ("text", "status", "phrase"),
    [
        ("yes on 1 and 9", "unknown_numbers", "I don't see 9 on that list"),
        ("yes on 2, leave 2", "conflict", "both yes and leave it"),
        ("yes", "nothing_parsed", "Which ones should I go ahead with"),
        ("thanks", "nothing_parsed", "Which ones"),
        # A question about a line is not a yes to it: approving would close or
        # reassign a Smokeball task on a guess.
        ("Who is handling 3?", "nothing_parsed", "a question about 3, not a yes"),
        ("Can you move 2 to Monday?", "nothing_parsed", "a question about 2"),
        ("1 is fine. Why is 2 on here?", "nothing_parsed", "a question about 2"),
        ("Could you do all of them by Friday", "nothing_parsed", "a question, not a yes"),
    ],
)
def test_ambiguity_writes_nothing_and_asks(tmp_path, rows, text, status, phrase):
    path = _three(tmp_path)
    _reply_origin(text)
    broker: list = []
    out = _answer(path, broker)
    assert out["status"] == status and phrase in out["confirmation_text"]
    assert broker == [] and CASEWORK_ACTS.pending(REPLY_SESSION) == 0


def test_a_bare_yes_to_a_one_line_list_approves_it(tmp_path, rows):
    path = _ledger(tmp_path, [_raise_row(1, K1, "t-1")])
    _reply_origin("yes please")
    broker: list = []
    assert _answer(path, broker)["writes"] == 1


def test_a_thread_with_two_lists_is_ambiguous(tmp_path, rows):
    path = _ledger(tmp_path, [_raise_row(1, K1, "t-1"), _raise_row(1, K9, "t-9", ref="f" * 32)])
    _reply_origin("yes on 1")
    broker: list = []
    assert _answer(path, broker)["status"] == "ambiguous_thread"
    assert broker == []


def test_a_thread_with_no_casework_rows_falls_through(tmp_path, rows):
    path = _ledger(tmp_path, [_raise_row(1, K1, "t-1", thread="another-thread")])
    _reply_origin("got it on 1")
    assert _answer(path, [])["status"] == "digest"


def test_an_off_roster_sender_gets_nothing(tmp_path, rows):
    path = _three(tmp_path)
    _reply_origin("yes to all", address="stranger@elsewhere.example")
    broker: list = []
    out = _answer(path, broker)
    assert out["status"] == "not_rostered" and out["confirmation_text"] == ""
    assert broker == []


def test_an_answered_line_is_not_acted_on_twice(tmp_path, rows):
    rows_ = [_raise_row(1, K1, "t-1"), _raise_row(2, K2, "t-2")]
    rows_.append(
        {
            "ts": "2026-09-28T16:00:00Z",
            "event": "approved",
            "item_key": K1,
            "n": 1,
            "thread_ref": THREAD,
            "skill": "x",
        }
    )
    path = _ledger(tmp_path, rows_)
    _reply_origin("yes to all")
    broker: list = []
    out = _answer(path, broker)
    assert out["writes"] == 1
    assert [e["item_key"] for e in broker] == [K2]


def test_an_approved_step_is_returned_for_the_router(tmp_path, rows):
    step = {
        "catalog_id": "witness_list_finalize",
        "skill": "trial-binder-assembler",
        "level": "prepares",
        "params": {"document": "witness list"},
    }
    path = _ledger(
        tmp_path,
        [
            _raise_row(1, K_DATE, None, "step", event="briefed", step=step),
            _raise_row(2, K_DATE, None, "step", event="briefed", step={**step, "level": "handles"}),
        ],
    )
    _reply_origin("yes on 1, leave 2")
    broker: list = []
    out = _answer(path, broker)
    assert out["status"] == "done"
    assert out["steps_to_run"] == [
        {
            "n": 1,
            "catalog_id": "witness_list_finalize",
            "skill": "trial-binder-assembler",
            "level": "prepares",
            "matter_id": "m-104",
            "params": {"document": "witness list"},
        }
    ]
    assert out["confirmation_text"] == (
        "Got it. I'll prepare 1 and send it to you for review. Leaving 2 as it is."
    )
    approved = next(e for e in broker if e["event"] == "approved")
    assert approved["n"] == 1 and approved["kind"] == "date" and "step" not in approved


def test_the_tool_schemas_take_no_ids():
    assert casework.EMPTY_SCHEMA["properties"] == {}


# ---------------------------------------------------------------------------
# A reply's confirmation names what it closed or started (shared/sent_lines.py)
# ---------------------------------------------------------------------------


def _threaded(broker: list, event: str) -> list[dict]:
    """The raises a send wrote, as the broker would stamp them on the thread."""
    return [
        {**e, "ts": "2026-09-28T15:07:00Z", "thread_ref": THREAD}
        for e in broker
        if e["event"] == event
    ]


def _close_one(path, broker):
    CASEWORK_ACTS.replay(REPLY_SESSION, casework_acts.UPDATE_TASK_TOOL, {})
    CASEWORK_ACTS.on_post_tool(casework_acts.UPDATE_TASK_TOOL, REPLY_SESSION, {"tool_call_id": "t"})
    return _answer(path, broker)


def test_a_legacy_list_with_no_kept_labels_confirms_by_number(tmp_path, rows):
    # Rows written before labels were kept: nothing under their dispatch_ref.
    path = _three(tmp_path)
    _reply_origin("yes on 1")
    broker: list = []
    _answer(path, broker)
    assert _close_one(path, broker)["confirmation_text"] == "Got it. Closed 1."


def _casefile_row(n, task, kind="task"):
    action = "close" if kind == "task" else "add_row"
    return {
        **_raise_row(n, _key(task, kind=kind), task, action),
        "skill": "case-file-manager",
        "kind": kind,
        "payload": {
            "action": action,
            "class": "done" if kind == "task" else "open",
            "evidence": ["job:x"],
        },
    }


def _casefile_answer(path, broker, apply):
    return json.loads(
        casework.reply_verdicts(
            session_id=REPLY_SESSION,
            load_config=_Config,
            verified_acker=lambda _s: {"name": "Dana Whitfield", "key": "k" * 64},
            append=lambda event: broker.append(event) or {"ok": True},
            fallthrough=lambda: "{}",
            casework_ledger_path=path,
            casefile_apply=apply,
        )
    )


def test_case_file_lines_are_handed_to_the_broker_never_queued_as_task_writes(tmp_path, rows):
    path = _ledger(tmp_path, [_casefile_row(1, "t-1"), _casefile_row(2, "med-1", "medicals")])
    _reply_origin("yes to all")
    broker: list = []
    asked: list = []

    def apply(thread, origin, numbers):
        asked.append((thread, origin.sender_address, numbers))
        return {"ok": True, "accepted": True, "job_id": "J", "lines": [1, 2]}

    out = _casefile_answer(path, broker, apply)
    assert out["status"] == "done"
    assert [(e["event"], e["n"]) for e in broker] == [("approved", 1), ("approved", 2)]
    assert asked == [(THREAD, ATTORNEY, [1, 2])]  # only the numbers this reply named
    assert not CASEWORK_ACTS.pending(REPLY_SESSION)
    text = out["confirmation_text"]
    assert (
        "Making 1 and 2 now." in text and "I'll reply" not in text
    )  # no promise of a later message


def test_a_refused_apply_claims_smd_was_told_only_when_the_broker_paged(tmp_path, rows):
    path = _ledger(tmp_path, [_casefile_row(1, "t-1")])
    _reply_origin("yes")
    out = _casefile_answer(path, [], lambda *_a: {"ok": True, "accepted": False, "reason": "x"})
    assert "couldn't start 1" in out["confirmation_text"]
    assert (
        "SMD has been told" not in out["confirmation_text"]
        and "Making" not in out["confirmation_text"]
    )
    _reply_origin("yes")
    out = _casefile_answer(
        path, [], lambda *_a: {"ok": True, "accepted": False, "reason": "x", "paged": True}
    )
    assert "SMD has been told" in out["confirmation_text"]


def test_a_second_yes_retries_an_approved_line_the_apply_never_took(tmp_path, rows):
    approved = {
        **_casefile_row(1, "t-1"),
        "event": "approved",
        "decided_by": {"name": "D", "key": "k" * 64},
    }
    approved.pop("dispatch_ref")
    approved.pop("payload")
    path = _ledger(tmp_path, [_casefile_row(1, "t-1"), approved])
    _reply_origin("yes on 1")
    asked: list = []
    broker: list = []

    def apply(thread, origin, numbers):
        asked.append(numbers)
        return {"ok": True, "accepted": True, "job_id": "J", "lines": [1]}

    out = _casefile_answer(path, broker, apply)
    assert broker == []  # no second verdict row
    assert asked == [[1]] and "Making 1 now." in out["confirmation_text"]


def test_a_line_another_admin_approved_is_left_for_their_reply(tmp_path, rows):
    path = _ledger(tmp_path, [_casefile_row(1, "t-1"), _casefile_row(2, "t-2")])
    _reply_origin("yes to all")
    out = _casefile_answer(
        path,
        [],
        lambda *_a: {
            "ok": True,
            "accepted": True,
            "job_id": "J",
            "lines": [1],
            "approved_by_another": [2],
        },
    )
    text = out["confirmation_text"]
    assert "Making 1 now." in text and "approved by another administrator" in text
    assert "couldn't start" not in text
