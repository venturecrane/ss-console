"""The training slice of the guide rehearsal, at $0 (ss-console #3946).

Reads the one rehearsal of this process (``test_guide_rehearsal.guide_rehearsal``):
the authored guide seat with training switched on, driven through the webhook
router's real intake and Hermes' own agent loop with a scripted model. The
turns are in ``_training_turns.py``; what each test proves is in its name.

The canary inventory in ``test_guide_rehearsal.py`` runs over the same volume,
so every place this slice wrote is already checked to be in the purge set or
the named-retained set, before and after A's purge. The tests here add what is
specific to training: A's progress and the report that names her are gone after
the purge, B's stay, and no marker of anyone reaches the report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from shared import sender_enrollment, training, training_report
from tests_e2e._training_turns import C_MARK, C
from tests_e2e.test_guide_rehearsal import _person_purge, guide_rehearsal
from tests_e2e.test_guide_rehearsal_run import A_MARK, ADMIN, B_MARK, A, B

SKILLS = Path(__file__).resolve().parents[2] / "skills"

#: The one turn the plan measured on the live seat (premise 2): an answered
#: email cost 20.49 cents over 7 model calls.
MEASURED_CENTS_PER_EMAIL = 20.49
MEASURED_CALLS_PER_EMAIL = 7


@pytest.fixture(scope="module")
def rehearsal() -> Any:
    return guide_rehearsal()


def _turn(rehearsal: dict, label: str) -> dict:
    return next(t for t in rehearsal["evidence"]["turns"] if t["label"] == label)


def _results(rehearsal: dict, label: str, tool: str = "training_record") -> list[dict]:
    return [json.loads(c["result"]) for c in _turn(rehearsal, label)["calls"] if c["tool"] == tool]


def _training(rehearsal: dict) -> dict:
    return rehearsal["evidence"]["training"]


# ---------------------------------------------------------------------------
# lessons, quiz, readiness: decided by code
# ---------------------------------------------------------------------------


def test_a_works_the_whole_path_and_code_decides_each_step(rehearsal) -> None:
    [start] = _results(rehearsal, "t-a-starts")
    assert start["next_lesson"]["id"] == "l01" and start["ready"] is False
    skipped, first = _results(rehearsal, "t-a-lesson-1")
    assert skipped["ok"] is False and "in order" in skipped["say"]
    assert first["ok"] is True and first["lessons_complete"] == 1
    assert all(r["ok"] for r in _results(rehearsal, "t-a-lessons-2-to-8"))
    [tone] = _results(rehearsal, "t-a-draft-with-misses", "tone_check")
    assert tone["misses"] >= 4
    assert {"no-amount", "no-timeframe", "solution-not-systems", "no-exclamation"} <= {
        m["rule"] for m in tone["rules_missed"]
    }
    [clean] = _results(rehearsal, "t-a-clean-draft", "tone_check")
    assert clean == {"ok": True, "misses": 0, "rules_missed": []}
    out_of_order, one = _results(rehearsal, "t-a-objection-1")
    assert out_of_order["ok"] is False and one["ok"] is True
    for n in range(2, 7):
        assert _results(rehearsal, f"t-a-objection-{n}")[0]["ok"]
    [quiz] = _results(rehearsal, "t-a-asks-quiz")
    assert len(quiz["questions"]) == 10
    assert all(set(q) == {"number", "ask", "choices"} for q in quiz["questions"])
    graded, status = _results(rehearsal, "t-a-quiz-answers")
    assert graded["quiz"]["correct"] == 9 and graded["quiz"]["passed"] is True
    assert [m["number"] for m in graded["quiz"]["missed"]] == [9]
    assert status["ready"] is True
    assert [c["id"] for c in status["readiness"] if c["met"]] == [
        "lessons",
        "quiz",
        "mock_pitch",
        "tone",
    ]


def test_b_sees_only_b_and_fails_the_quiz(rehearsal) -> None:
    [start] = _results(rehearsal, "t-b-starts")
    assert start["lessons_complete"] == 0, "B's status shows nothing of A's ten lessons"
    graded, status = _results(rehearsal, "t-b-quiz-answers")
    assert graded["quiz"]["correct"] == 3 and graded["quiz"]["passed"] is False
    assert status["ready"] is False and status["lessons_complete"] == 1


def test_a_scheduled_turn_reaches_no_ones_progress(rehearsal) -> None:
    turn = _turn(rehearsal, "t-scheduled-training-call")
    for call in turn["calls"]:
        assert json.loads(call["result"]) == {"ok": False, "say": training.NO_PERSON}


def test_every_tool_a_procedure_names_is_on_the_email_surface(rehearsal) -> None:
    named: set[str] = set()
    for skill in ("product-guide", "pitch-practice", "rep-training"):
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        named |= set(re.findall(r"[Cc]all `([a-z_]+)`", text))
        named |= set(re.findall(r"`([a-z_]+)` tool", text))
        if "`read_file`" in text:
            named.add("read_file")
    assert {"training_record", "tone_check", "invite_code_issue", "read_file"} <= named
    offered = {t.removeprefix("mcp__") for t in _turn(rehearsal, "t-a-starts")["offered"]}
    assert named <= offered, sorted(named - offered)


# ---------------------------------------------------------------------------
# enrollment: the progress record by code, the welcome with the approved words
# ---------------------------------------------------------------------------


def test_enrollment_creates_the_progress_record_and_sends_the_training_welcome(rehearsal) -> None:
    redeem = _training(rehearsal)["redeem"]
    assert redeem["router_verdict"] == {"action": "skip"}, "no model turn for a code"
    [welcome] = [r for r in redeem["broker"] if r["action"] == "agentmail_reply"]
    text = welcome["payload"]["text"]
    assert sender_enrollment.PRIVACY_NOTICE in text and '"start training"' in text
    assert "code" not in text.lower() and "invite" not in text.lower()
    volume = Path(rehearsal["root"]) / "opt-data"
    slug = _person_purge().person_slug(C)
    record = json.loads((volume / "rep" / "progress" / slug / "progress.json").read_text())
    assert record == json.loads(training.dumps(training.new_progress()))


# ---------------------------------------------------------------------------
# the weekly report: code, from progress only, to the administrator
# ---------------------------------------------------------------------------


def _report_send(rehearsal: dict) -> dict:
    sends = [
        b for b in _training(rehearsal)["report_turn"]["broker"] if b["action"] == "agentmail_send"
    ]
    assert len(sends) == 1, sends
    return sends[0]


def test_the_report_is_rendered_by_code_and_sent_through_the_gate_to_the_administrator(
    rehearsal,
) -> None:
    pre = _training(rehearsal)["report_pre_run"]
    assert pre["code"] == 0 and pre["verdict"]["status"] == "report_ready"
    assert pre["verdict"]["people"] == 3 and pre["verdict"]["ready"] == 1
    turn = _training(rehearsal)["report_turn"]
    assert turn["job_name"].startswith("op-managed:guide:rep-training")
    send = _report_send(rehearsal)
    assert send["payload"]["to"] == [ADMIN]
    assert send["payload"]["subject"] == "Representative progress and readiness"
    assert (
        send["audit_extra"]["skill_name"] == "rep-training"
        and send["audit_extra"]["body_variant"] == "full"
    )
    envelope = _training(rehearsal)["report_envelope"]
    body = envelope["dispatches"][0]["full_body"]
    assert f"{A}\n- Readiness: ready" in body
    assert f"{B}\n- Readiness: not ready yet" in body
    assert f"Casey Example ({C})" in body
    from shared.sent_lines import recipient_digest

    named = envelope["dispatches"][0][training_report.NAMED_FIELD]
    assert named == sorted({recipient_digest(x) for x in (A, B, C)})


def test_no_marker_note_or_lead_reaches_the_report(rehearsal) -> None:
    wire = json.dumps(_report_send(rehearsal)) + json.dumps(_training(rehearsal)["report_envelope"])
    for marker in (A_MARK, B_MARK, C_MARK, "Acme", "Beta", "CFO", "Tuesday"):
        assert marker not in wire, marker


# ---------------------------------------------------------------------------
# the purge
# ---------------------------------------------------------------------------


def test_after_a_is_purged_her_progress_and_the_report_naming_her_are_gone(rehearsal) -> None:
    volume = Path(rehearsal["root"]) / "opt-data"
    slug = _person_purge().person_slug
    assert not (volume / "rep" / "progress" / slug(A)).exists()
    assert (volume / "rep" / "progress" / slug(B) / "progress.json").is_file()
    consumed = (
        volume / "profiles" / "guide" / ".smd" / "pre_run" / "rep-training.dispatch.consumed.json"
    )
    assert not consumed.exists() or A not in consumed.read_text(encoding="utf-8")
    layers = {f.layer for f in rehearsal["purge_report"].removed}
    assert "scheduled-send envelope" in layers and "partitioned store directory" in layers


# ---------------------------------------------------------------------------
# projected cost per turn type
# ---------------------------------------------------------------------------


def _projection() -> dict[str, tuple[int, float]]:
    """``{turn type: (model calls, projected cents)}``. Model calls per type are
    the rounds the procedure takes (one per tool round, plus the reply); cents
    are those calls at the per-call cost the live seat measured (20.49 cents
    over 7 calls), which already includes the cached prefix. The two training
    tool schemas add about 360 tokens to that prefix, a fraction of a cent."""
    cur = training.load_curriculum(SKILLS / "rep-training", SKILLS / "pitch-practice")
    per_call = MEASURED_CENTS_PER_EMAIL / MEASURED_CALLS_PER_EMAIL
    docs = max(len({s.document for s in x.read}) + (1 if x.guide else 0) for x in cur.lessons)
    calls = {
        "lesson taught (status, the lesson's documents, the reply)": 1 + 1 + docs + 1,
        "exercise answered (documents, complete_lesson, the reply)": 1 + docs + 1 + 1,
        "draft tone check (tone_check, complete_lesson, the reply)": 1 + 3,
        "quiz sent or graded (one tool, the reply)": 1 + 2,
        "mock pitch objection (practice files, objection_answered, the reply)": 1 + 2 + 1 + 1,
        "weekly report (dispatched before the model; the turn closes)": 2,
    }
    return {k: (n, round(n * per_call, 1)) for k, n in calls.items()}


def test_projected_cost_per_turn_type(rehearsal, capsys) -> None:
    turn = _training(rehearsal)["report_turn"]
    assert turn["model_requests"] <= 2, "a report turn asks the model only to close"
    projection = _projection()
    with capsys.disabled():
        print("\nprojected cost per training turn type (cents, from the measured per-call cost):")
        for kind, (n, cents) in projection.items():
            print(f"  {kind}: {n} calls, about {cents}")
    assert all(cents < MEASURED_CENTS_PER_EMAIL * 1.5 for _, cents in projection.values())
