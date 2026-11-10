"""The rep desk at $0, in the guide rehearsal (ss-console #3946, slice 3).

Reads the one guide rehearsal of this pytest process
(``test_guide_rehearsal.guide_rehearsal``); the desk's turns are
``_desk_turns.py``. Two enrolled people keep a lead and a reminder, look across
the wall, take a brief and a follow-up draft; then the hourly job runs the real
``rep-desk`` pre_run and a cron turn whose ``pre_llm_call`` sends each person
their own digest through the full gate. The foundation's canary inventory in
``test_guide_rehearsal.py`` already sorts every place A's and B's markers
landed, these turns included, into the purge and retained sets.

What these tests add:

* the wall holds for the desk's own store and tool;
* the receipt line, date included, passes the REAL reply relay: the reply is
  sent, not held;
* the digest reaches each person once, to them only, with only their words, on
  the skill's roster lane, as the full body (no fallback);
* nothing in the run was sent to anyone but the two people (a follow-up draft
  goes back to the rep as text);
* every tool the desk procedure names is offered on the email surface;
* the reply checks pass every desk reply and every fixed line the procedure
  tells the model to write;
* a projected cost per turn type, from the recorded requests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests_e2e.test_guide_rehearsal import guide_rehearsal
from tests_e2e.test_guide_rehearsal_run import A_MARK, ADMIN, B_MARK, A, B

OVERLAY = Path(__file__).resolve().parents[1]
DESK_SKILL_MD = OVERLAY.parent / "skills" / "rep-desk" / "SKILL.md"
PRICING = OVERLAY.parent / "adapter" / "cost_telemetry" / "anthropic_pricing.json"


@pytest.fixture(scope="module")
def rehearsal() -> dict[str, Any]:
    return guide_rehearsal()


def _turn(rehearsal: dict, label: str) -> dict:
    return next(t for t in rehearsal["evidence"]["turns"] if t["label"] == label)


def _results(turn: dict, tool: str) -> list[str]:
    return [c["result"] for c in turn["calls"] if c["tool"] == tool]


def _broker(rehearsal: dict) -> list[dict]:
    return json.loads(
        (Path(rehearsal["root"]) / "broker-requests.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# the wall, the tool, the receipt
# ---------------------------------------------------------------------------


def test_reminders_are_kept_behind_each_persons_wall(rehearsal) -> None:
    a = _turn(rehearsal, "d-a-desk-lead-and-reminder")
    kept = [json.loads(r) for r in _results(a, "reminder_set")]
    assert [k["kept"] for k in kept] == [True, True], kept
    assert kept[0]["receipt"].startswith("Meeting kept for ")
    assert a["reply"].startswith(kept[1]["receipt"]), (
        "the reply must use the receipt line as written"
    )
    b = _turn(rehearsal, "d-b-desk-reminder-and-look-across")
    [b_kept] = [json.loads(r) for r in _results(b, "reminder_set")]
    assert B_MARK in b_kept["receipt"] and A_MARK not in b_kept["receipt"]
    reminders_list, leads_list = (json.loads(r) for r in _results(b, "record_store_list"))
    assert len(reminders_list["records"]) == 1 and leads_list["records"] == []
    [read] = _results(b, "record_store_read")
    assert "no record named 'globex.md'" in read and A_MARK not in read
    [write] = _results(b, "record_store_write")
    assert "kept by the seat itself" in write


def test_the_receipt_reply_went_out_through_the_reply_checks(rehearsal) -> None:
    """The reply carrying the receipt line (a date three days out, not the
    seat's own today) was SENT by the relay, not held: a held reply never
    reaches the broker. (This turn read no dated record, so the identifier
    gate's empty-register carve would pass the date either way; that the
    receipt's date is refused before reminder_set and allowed after, on a turn
    that did read one, is ``tests/test_rep_desk.py::
    test_the_drafted_reply_with_the_receipt_passes_the_trust_gate``.)"""
    a = _turn(rehearsal, "d-a-desk-lead-and-reminder")
    replies = [q for q in _broker(rehearsal) if q.get("action") == "agentmail_reply"]
    texts = [str((q.get("payload") or {}).get("text") or "") for q in replies]
    assert a["reply"].startswith("Reminder kept for ")
    assert a["reply"] in texts, "the receipt reply was held or never sent"


def test_a_brief_reads_only_the_persons_own_records(rehearsal) -> None:
    brief = _turn(rehearsal, "d-a-brief-from-own-records")
    results = " ".join(c["result"] for c in brief["calls"])
    assert A_MARK in results and B_MARK not in results
    assert all('"error"' not in c["result"] for c in brief["calls"]), brief["calls"]


def test_the_follow_up_draft_goes_back_to_the_rep_and_nobody_else_is_sent_anything(
    rehearsal,
) -> None:
    draft = _turn(rehearsal, "d-a-follow-up-draft")
    assert draft["reply"].startswith("Draft for you to send:")
    replies = [q for q in _broker(rehearsal) if q.get("action") == "agentmail_reply"]
    assert any(
        str((q.get("payload") or {}).get("text") or "") == draft["reply"] for q in replies
    ), "the draft was not returned to the rep in their thread"
    sends = [q for q in _broker(rehearsal) if q.get("action") == "agentmail_send"]
    to = {addr for q in sends for addr in (q.get("payload") or {}).get("to") or []}
    assert to <= {A, B, ADMIN}, to


# ---------------------------------------------------------------------------
# the hourly digest
# ---------------------------------------------------------------------------


def test_the_hourly_digest_reaches_each_person_with_only_their_own(rehearsal) -> None:
    desk = rehearsal["evidence"]["desk"]
    verdict = desk["verdict"]
    assert desk["code"] == 0 and verdict["wakeAgent"] is True and verdict["chunk"] == 2, verdict
    assert set(verdict) <= {
        "wakeAgent",
        "dispatch_expected",
        "status",
        "people",
        "due",
        "chunk",
        "deferred",
        "unreadable",
        "mark_failed",
        "orphans",
        "orphans_unreadable",
    }
    # person_orphans ran from the hourly job and looked: nobody's directory
    # here belongs to nobody listed (counts only, never a slug).
    assert verdict["orphans"] == 0 and "orphans_unreadable" not in verdict, verdict
    turn = _turn(rehearsal, desk["turn"])
    assert (
        turn["exit"].startswith("text_response") and turn["job_name"] == "op-managed:guide:rep-desk"
    )
    sends = [q for q in turn["broker"] if q.get("action") == "agentmail_send"]
    by_person = {}
    for send in sends:
        payload = send.get("payload") or {}
        [to] = payload["to"]
        by_person[to] = payload["text"]
        assert send["lane"] == "skill:rep-desk"
        assert send["audit_extra"]["body_variant"] == "full", send["audit_extra"]
        assert send["audit_extra"]["skill_name"] == "rep-desk"
    assert set(by_person) == {A, B}
    assert A_MARK in by_person[A] and B_MARK not in by_person[A]
    assert B_MARK in by_person[B] and A_MARK not in by_person[B]


def test_the_digest_output_carries_counts_only(rehearsal) -> None:
    out = (Path(rehearsal["root"]) / "digest-pre-run.out").read_text(encoding="utf-8")
    assert A not in out and B not in out and A_MARK not in out and B_MARK not in out
    [row] = [
        q["row"]
        for q in _broker(rehearsal)
        if q.get("action") == "emitted_wake_append"
        and (q.get("row") or {}).get("skill_name") == "rep-desk"
    ]
    assert row["action_type"] == "EMITTED_WAKE"
    metadata = json.loads(row["metadata"])
    assert metadata["decision_basis"] == "digest_ready" and metadata["chunk"] == 2
    text = json.dumps(row)
    assert A not in text and B not in text and A_MARK not in text and B_MARK not in text


# ---------------------------------------------------------------------------
# the procedure against the seat
# ---------------------------------------------------------------------------


def _named_tools() -> set[str]:
    text = DESK_SKILL_MD.read_text(encoding="utf-8")
    return {
        m for m in re.findall(r"`([a-z_]+)`", text) if m.startswith(("record_store_", "reminder_"))
    }


def test_every_tool_the_procedure_names_is_offered_on_the_email_surface(rehearsal) -> None:
    named = _named_tools()
    assert named == {"record_store_list", "record_store_read", "record_store_write", "reminder_set"}
    for label in (
        "d-a-desk-lead-and-reminder",
        "d-a-brief-from-own-records",
        "d-a-follow-up-draft",
    ):
        offered = {t.removeprefix("mcp__") for t in _turn(rehearsal, label)["offered"]}
        assert named <= offered, (label, named - offered)


#: The lines the procedure tells the model to write as fixed text, filled with
#: realistic values (a date, an amount, a clock time).
FIXED_LINES = (
    "Draft for you to send:",
    "Revised draft:",
    "Company: Globex\nContact: Dana Reyes, CFO\nStage: proposal\nNext step: send the pilot outline\n"
    "Amount: 12,000 dollars a year\nNotes: wants fewer handoffs",
    "Meeting: Thursday at 2:30 PM, in their words",
    "Meeting kept for Thursday, November 12, 2026 at 2:30 PM: Call the Globex CFO",
    "Reminder kept for Monday, November 30, 2026: Send Initech the 1.5 million dollars renewal",
)


def test_the_reply_checks_pass_every_desk_reply_and_fixed_line(rehearsal) -> None:
    from shared import outbound_gate, output_checklist

    replies = [
        _turn(rehearsal, label)["reply"]
        for label in (
            "d-a-desk-lead-and-reminder",
            "d-a-brief-from-own-records",
            "d-a-follow-up-draft",
        )
    ]
    for text in [*replies, *FIXED_LINES]:
        for surface in (output_checklist.STAFF_SEND, output_checklist.EVERY_OUTPUT):
            assert output_checklist.check(text, surface) == [], (surface, text)
        assert outbound_gate.evaluate(text, None, None).allowed, text


# ---------------------------------------------------------------------------
# projected cost per turn type
# ---------------------------------------------------------------------------

#: The one measured live turn (premise 2 of the plan): an answered email on the
#: guide seat, 7 model calls, 2,267 input, 279,951 cache-read, 55,537
#: cache-write and 3,353 output tokens, 20.49 cents.
MEASURED = {"calls": 7, "input": 2267, "cache_read": 279951, "cache_write": 55537, "output": 3353}


def _rates() -> dict[str, float]:
    pricing = json.loads(PRICING.read_text(encoding="utf-8"))
    row = pricing["models"]["claude-sonnet-5-5"]
    mult = pricing["_meta"]["multipliers"]
    inp = row["input_per_million_cents"] / 1e6
    return {
        "input": inp,
        "output": row["output_per_million_cents"] / 1e6,
        "cache_read": inp * row.get("cache_read_multiplier", mult["cache_read"]),
        "cache_write": inp * mult["cache_write_5m"],
    }


def _cents(tokens: dict[str, float], rates: dict[str, float]) -> float:
    return sum(tokens[k] * rates[k] for k in ("input", "cache_read", "cache_write", "output"))


def projected_costs(rehearsal: dict) -> dict[str, dict[str, Any]]:
    """Projected cents per turn type, built on the measured live turn.

    THE MODEL, stated. A live email turn re-reads its cached prefix on every
    model call. The measured turn's per-call cache read is the prefix; a desk
    turn adds (a) the reminder tool's schema and the desk's skill line to every
    email's prefix (measured here from the recorded requests), (b) the desk
    procedure, read once with ``read_file`` and re-read from cache on each later
    call, and (c) its tool calls: one model call per tool round plus the reply.
    A digest run is one woken turn (the stub saw its requests); its hourly
    cache is cold, so its prefix is written, not read. Output per call is the
    measured turn's average. The quality of a real turn, and how many rounds a
    live model takes, are not measurable at $0.
    """
    rates = _rates()
    base = _cents(MEASURED, rates)
    per_call_prefix = MEASURED["cache_read"] / MEASURED["calls"]
    out_per_call = MEASURED["output"] / MEASURED["calls"]
    log = [
        json.loads(line)
        for line in (Path(rehearsal["root"]) / "model-requests.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    tools = next(req["body"].get("tools") or [] for req in log if req["body"].get("tools"))
    desk_schema = (
        sum(len(json.dumps(t)) for t in tools if str(t.get("name", "")).endswith("reminder_set"))
        / 4
    )
    skill_line = len("rep-desk: A listed person's own notes, leads and reminders.") / 4
    added_prefix = desk_schema + skill_line
    procedure = len(DESK_SKILL_MD.read_text(encoding="utf-8")) / 4

    def email(rounds: int) -> float:
        # measured turn: 7 calls; a desk turn: 1 read of the procedure + its
        # tool rounds + the reply, on the prefix grown by the desk's additions.
        calls = 2 + rounds
        tokens = {
            "input": MEASURED["input"],
            "cache_read": calls * (per_call_prefix + added_prefix) + (calls - 1) * procedure,
            "cache_write": MEASURED["cache_write"] + added_prefix + procedure,
            "output": calls * out_per_call,
        }
        return _cents(tokens, rates)

    digest_calls = sum(1 for req in log if req["turn"] == rehearsal["evidence"]["desk"]["turn"])
    digest = _cents(
        {
            "input": 0,
            "cache_read": max(0, digest_calls - 1) * (per_call_prefix + added_prefix),
            "cache_write": per_call_prefix + added_prefix,
            "output": digest_calls * out_per_call,
        },
        rates,
    )
    return {
        "measured_answered_email": {"cents": round(base, 2), "calls": MEASURED["calls"]},
        "every_email_added_prefix": {
            "tokens": round(added_prefix),
            "cents_per_email": round(
                MEASURED["calls"] * added_prefix * rates["cache_read"]
                + added_prefix * rates["cache_write"],
                3,
            ),
        },
        "note, lead or reminder (1 tool round)": {"cents": round(email(1), 2), "calls": 3},
        "lead update or debrief (2 tool rounds: read, write)": {
            "cents": round(email(2), 2),
            "calls": 4,
        },
        "brief or follow-up (3 tool rounds)": {"cents": round(email(3), 2), "calls": 5},
        "pipeline (5 tool rounds)": {"cents": round(email(5), 2), "calls": 7},
        "digest run, any number of people up to ten": {
            "cents": round(digest, 2),
            "calls": digest_calls,
        },
        "nothing due": {"cents": 0.0, "calls": 0},
    }


def test_projected_cost_per_turn_type(rehearsal) -> None:
    costs = projected_costs(rehearsal)
    (Path(rehearsal["root"]) / "desk-cost-projection.json").write_text(json.dumps(costs, indent=1))
    print("\nprojected cents per turn type:\n" + json.dumps(costs, indent=1))
    assert 15 < costs["measured_answered_email"]["cents"] < 25  # 20.49 reproduced
    assert costs["digest run, any number of people up to ten"]["calls"] >= 1
    for name, row in costs.items():
        assert row.get("cents", row.get("cents_per_email", 0)) >= 0, name
