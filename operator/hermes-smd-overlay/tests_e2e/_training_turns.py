"""The training slice of the guide rehearsal (ss-console #3946), at $0.

Called from ``test_guide_rehearsal_run.py``: :func:`author_training` adds the
training config to the rehearsal seat before it is translated, and
:func:`run_training` drives the turns after the foundation's own. Assertions
live in ``test_guide_training_rehearsal.py``.

What runs, each through the seat's own code path:

* rep A works the whole path: status, every lesson in order (with one skip
  refused), a draft with tone misses then a clean one, a whole mock pitch
  answered objection by objection (one out of order refused), the quiz sent and
  graded from her own numbered answers, and readiness;
* rep B starts, completes one lesson, and fails the quiz in the letters-only
  form; B's status shows B's progress and nothing of A's;
* a scheduled turn calls the training tool and is refused (no person);
* rep C emails an invite code: the router's redeem path creates C's progress
  record by code and sends the training welcome with the privacy wording;
* the weekly report: the skill's ``pre_run.py`` runs as its own process, writes
  the envelope from the progress store, and the scheduled turn's
  ``pre_llm_call`` dispatches it to the administrator through the full gate.

Every email carries the sender's marker string, so the parent's canary
inventory covers everything this slice wrote.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests_e2e._cron_turn import cron_turn, run_pre_run

C = "rep-c@example.test"
C_NAME = "Casey Example"
C_CODE_ID = "c_111111111111"
C_MARK = "CANARY-C-m3t6x1"
#: The weekly report's schedule on the rehearsal seat (Mondays). Any schedule
#: materializes the same managed job; this is the shape the seat authors.
REPORT_SCHEDULE = "0 15 * * 1"

#: Every quiz answer correct except question 9 (the key's answer is D).
A_QUIZ = "1 B\n2. C\nQ3: A\n4) D\n5 A\n6 C\n7 B\n8 C\n9 B\n10 A"
#: Three right (questions 3, 5 and 10), the rest wrong. The letters-only form is
#: covered by the unit tests: here every email body ends with the harness's
#: turn marker, which is not a letter.
B_QUIZ = "\n".join(f"{n} A" for n in range(1, 11))

#: Rep A's answers to the owner scenario's six objections, one per email.
A_ANSWERS = (
    "It is a worker we run for your business, not another tool you run yourself.",
    "Your limits are written down and every action is checked against them before it runs.",
    "That is for SMD to price with you, and I can bring you to them for it.",
    "It is early and selective, and I can show you what is in production today.",
    "Your people review its work, and the agreement says plainly who is responsible.",
    "You can pause it, and the agreement covers ending the service and getting your data back.",
)


def author_training(doc: dict[str, Any]) -> None:
    """The rehearsal seat authors training: the skill enabled on the guide
    persona and its weekly report job. The progress store (``rep-progress``,
    walled per person, managed) is already in the rehearsal's ``STORES``."""
    persona = doc["personas"][0]
    persona["skills"].append(
        {
            "name": "rep-training",
            "version": "pending",
            "initiation": {"manual": True, "scheduled": True, "webhook": True},
            "enabled": True,
        }
    )
    persona.setdefault("cron", []).append(
        {
            "skill": "rep-training",
            "schedule": REPORT_SCHEDULE,
            "pre_run": "pre_run.py",
            "wake_policy": "pre_run_decides",
        }
    )


def _code() -> str:
    """An invite-code-shaped token, built from parts so no code-shaped literal
    is committed. The stub broker decides it is valid."""
    return "-".join(["SMD", "TRNA", "B2CD", "EF34"])


def _status(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [json.loads(c["result"]) for c in record["calls"] if c["tool"] == "training_record"]


class _RedeemingResponses(dict):
    """The stub broker's canned answers, with the one side effect the real
    broker's redeem has that the seat then reads: the enrolled list gains C.
    (The broker owns ``enrolled.json`` on a seat; the stub stands in for it.)"""

    def __init__(self, base: dict[str, Any], enrolled: Path) -> None:
        super().__init__(base)
        self.enrolled = enrolled

    def get(self, key: Any, default: Any = None) -> Any:
        if key == "sender_enrollment_redeem":
            doc = json.loads(self.enrolled.read_text(encoding="utf-8"))
            if not any(e.get("address") == C for e in doc["enrolled"]):
                doc["enrolled"].append(
                    {
                        "address": C,
                        "label": "rehearsal",
                        "name": C_NAME,
                        "code_id": C_CODE_ID,
                        "enrolled_at": "x",
                    }
                )
                self.enrolled.write_text(json.dumps(doc), encoding="utf-8")
            return {"ok": True, "enrolled": True, "name": C_NAME, "code_id": C_CODE_ID}
        return super().get(key, default)


def _redeem(r: Any, mark_c: str) -> dict[str, Any]:
    import uuid

    enrolled = r.volume / "sender-enrollment" / "enrolled.json"
    r.broker.responses = _RedeemingResponses(r.broker.responses, enrolled)
    broker_mark = r.broker.mark()
    mid = f"<{uuid.uuid4().hex}@mail.example.test>"
    payload = r._payload(C, mid, f"Here is my code {_code()} {mark_c}")
    verdict = r._deliver(payload, event_id=mid)
    # Only what this email caused: an earlier turn's reply can reach the stub
    # broker late, from the reply plugin's own thread.
    mine = [
        req
        for req in r.broker.since(broker_mark)
        if mid in json.dumps(req.get("payload") or {}, default=str)
    ]
    return {
        "message_id": mid,
        "router_verdict": verdict if isinstance(verdict, (dict, type(None))) else str(verdict),
        "broker": mine,
    }


def run_training(r: Any, a: str, b: str, a_mark: str, b_mark: str) -> dict[str, Any]:
    """Drive the training turns. Returns evidence for the parent's file."""
    rec = "training_record"
    out: dict[str, Any] = {}

    r.turn("t-a-starts", [(rec, {"action": "status"})], sender=a, body=f"start training {a_mark}")
    r.turn(
        "t-a-lesson-1",
        [
            (rec, {"action": "complete_lesson", "lesson_id": "l03"}),
            (rec, {"action": "complete_lesson", "lesson_id": "l01"}),
        ],
        sender=a,
        body=f"The Operator is a worker SMD runs for one business. {a_mark}",
    )
    r.turn(
        "t-a-lessons-2-to-8",
        [(rec, {"action": "complete_lesson", "lesson_id": f"l0{n}"}) for n in range(2, 9)],
        sender=a,
        body=f"Here are my answers to the exercises. {a_mark}",
    )
    r.turn(
        "t-a-draft-with-misses",
        [("tone_check", {}), (rec, {"action": "complete_lesson", "lesson_id": "l09"})],
        sender=a,
        body=f"Hi, we build better systems in two weeks for $500! {a_mark}",
    )
    # No marker in this one email: the marker is a word in capitals, which the
    # tone rules count as a miss, and this draft is the clean one.
    r.turn(
        "t-a-clean-draft",
        [("tone_check", {})],
        sender=a,
        body="Where are you trying to take the business, and what is in the way today?",
    )
    for n, answer in enumerate(A_ANSWERS, start=1):
        calls = [(rec, {"action": "objection_answered", "scenario": "owner", "number": n})]
        if n == 1:
            calls.insert(
                0, (rec, {"action": "objection_answered", "scenario": "owner", "number": 3})
            )
        r.turn(f"t-a-objection-{n}", calls, sender=a, body=f"{answer} {a_mark}")
    r.turn(
        "t-a-lesson-10",
        [(rec, {"action": "complete_lesson", "lesson_id": "l10"})],
        sender=a,
        body=f"That was the whole pitch. {a_mark}",
    )
    r.turn("t-a-asks-quiz", [(rec, {"action": "quiz"})], sender=a, body=f"quiz please {a_mark}")
    r.turn(
        "t-a-quiz-answers",
        [(rec, {"action": "grade_quiz"}), (rec, {"action": "status"})],
        sender=a,
        body=f"{A_QUIZ}\nThanks. {a_mark}",
    )

    r.turn("t-b-starts", [(rec, {"action": "status"})], sender=b, body=f"start training {b_mark}")
    r.turn(
        "t-b-lesson-1",
        [(rec, {"action": "complete_lesson", "lesson_id": "l01"})],
        sender=b,
        body=f"It is a managed worker for one business. {b_mark}",
    )
    r.turn(
        "t-b-quiz-answers",
        [(rec, {"action": "grade_quiz"}), (rec, {"action": "status"})],
        sender=b,
        body=f"{B_QUIZ}\n{b_mark}",
    )

    out["redeem"] = _redeem(r, C_MARK)

    cron_turn(
        r,
        "t-scheduled-training-call",
        skill="rep-training",
        script_output="{}",
        calls=[(rec, {"action": "status"}), ("tone_check", {})],
    )

    pre = run_pre_run(r.profile, "rep-training", volume=r.volume)
    out["report_pre_run"] = pre
    envelope = r.profile / ".smd" / "pre_run" / "rep-training.dispatch.json"
    out["report_envelope"] = (
        json.loads(envelope.read_text(encoding="utf-8")) if envelope.exists() else None
    )
    out["report_turn"] = cron_turn(
        r,
        "t-weekly-report",
        skill="rep-training",
        script_output=json.dumps(pre["verdict"]),
        calls=[],
    )
    return out


__all__ = ["A_QUIZ", "B_QUIZ", "C", "C_MARK", "author_training", "run_training"]
