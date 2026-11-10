"""hermes-smd-training: a representative's lessons, quiz, mock pitch and tone check (ss-console #3946).

Two tools, registered only on a guide seat that authors representative
training (:func:`shared.training.authored`: the ``rep-training`` skill enabled
and the ``rep-progress`` store walled per person and written only by code):

``training_record``  The representative's progress, kept by code. ``status``
    returns the next lesson and readiness; ``complete_lesson`` marks the next
    lesson done when its requirement is met; ``quiz`` returns the open-book quiz
    to send; ``grade_quiz`` grades the answer letters in the email that opened
    this turn; ``objection_answered`` records that the email that opened this
    turn answers the named objection of a mock pitch.

``tone_check``  Checks the representative's own words in the email that opened
    this turn against the tone rules the skill authors as data, records how
    many misses there were, and returns each miss with how to fix it.

The model supplies values (a lesson id, a scenario id, an objection number) and
never a person, a grade, a count or any text to store. Everything is decided in
:mod:`shared.training`. The person is the strict per-turn verified sender,
keyed by the session id Hermes hands the handler; there is no argument naming
one. Nothing the representative wrote is stored: the progress record holds ids
and counts only.

The handlers raise nothing to Hermes: a refusal is a sentence in the result.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shared import training
from shared.tool_registration import register_wrapped_tool

logger = logging.getLogger(__name__)

TOOL_RECORD = "training_record"
TOOL_TONE = "tone_check"

ACTIONS = ("status", "complete_lesson", "quiz", "grade_quiz", "objection_answered")

TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    TOOL_RECORD: (
        "The representative's training progress, kept by the seat. action status: the next "
        "lesson (its id, title, the library sections to read and its exercise) and readiness. "
        "action complete_lesson with lesson_id: marks the next lesson complete once its exercise "
        "is done. action quiz: the open-book quiz to send. action grade_quiz: grades the answer "
        "letters in the email you are answering (pass nothing else). action objection_answered "
        "with scenario and number: records that the email you are answering answers that "
        "objection of a mock pitch. The person is always whoever sent the email you are "
        "answering; you never name one.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "lesson_id": {"type": "string", "maxLength": 32},
                "scenario": {"type": "string", "maxLength": 32},
                "number": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    ),
    TOOL_TONE: (
        "Checks the representative's own words in the email you are answering against the "
        "seat's tone rules, records how many misses there were, and returns each miss with "
        "how to fix it. Takes no arguments: it reads that email itself.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
}


def _say(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _refused(sentence: str) -> str:
    return _say({"ok": False, "say": sentence})


def _lesson_view(lesson: training.Lesson, index: int, total: int) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": lesson.id,
        "number": index,
        "of": total,
        "title": lesson.title,
        "read": [{"document": s.document, "section": s.section} for s in lesson.read],
        "exercise": lesson.exercise,
    }
    if lesson.guide:
        view["training_page"] = lesson.guide
    return view


def _status(cur: training.Curriculum, progress: dict[str, Any]) -> dict[str, Any]:
    upcoming = training.next_lesson(cur, progress)
    ready, criteria = training.readiness(cur, progress)
    view: dict[str, Any] = {
        "ok": True,
        "lessons_complete": len(progress["lessons_complete"]),
        "lessons_total": len(cur.lessons),
        "next_lesson": (
            _lesson_view(upcoming, cur.lessons.index(upcoming) + 1, len(cur.lessons))
            if upcoming
            else None
        ),
        "ready": ready,
        "readiness": [{"id": c.id, "met": c.met, "value": c.value} for c in criteria],
    }
    return view


def _load() -> tuple[training.Curriculum | None, str]:
    try:
        return training.load_curriculum(), ""
    except training.TrainingError as exc:
        logger.warning("hermes-smd-training: curriculum unreadable (%s)", exc)
        return None, "The training material could not be opened just now; nothing was recorded."


def _progress_for(person: training.TurnPerson, cur: training.Curriculum) -> dict[str, Any]:
    progress = training.read_progress(person.address, cur)
    return progress if progress is not None else training.new_progress()


def record_handler(args: dict[str, Any], **kwargs: Any) -> str:
    args = args if isinstance(args, dict) else {}
    action = args.get("action")
    if action not in ACTIONS:
        return _refused("action is one of " + ", ".join(ACTIONS) + ".")
    person = training.turn_person(kwargs.get("session_id"))
    if person is None:
        return _refused(training.NO_PERSON)
    cur, problem = _load()
    if cur is None:
        return _refused(problem)
    progress = _progress_for(person, cur)
    changed = False
    out: dict[str, Any]
    if action == "status":
        out = _status(cur, progress)
        changed = training.read_progress(person.address, cur) is None
    elif action == "complete_lesson":
        changed, sentence = training.complete_lesson(
            cur, progress, str(args.get("lesson_id") or "")
        )
        out = {**_status(cur, progress), "ok": changed, "say": sentence}
    elif action == "quiz":
        out = {
            "ok": True,
            "open_book": True,
            "questions": [
                {"number": i, "ask": q.ask, "choices": [f"{k}. {v}" for k, v in q.choices]}
                for i, q in enumerate(cur.questions, start=1)
            ],
            "pass_correct": cur.pass_correct,
            "how_to_answer": "One line per question: the question number, then the letter.",
        }
    elif action == "grade_quiz":
        result = training.grade_quiz(cur, progress, person.reply_text)
        if result is None:
            return _refused(
                "No quiz answers were found in this email. Answers are one line per question: "
                "the number, then the letter."
            )
        changed = True
        out = {
            **_status(cur, progress),
            "quiz": {
                "correct": result.correct,
                "total": result.total,
                "passed": result.passed,
                "pass_correct": cur.pass_correct,
                "missed": [
                    {
                        "number": cur.questions.index(q) + 1,
                        "read": {"document": q.source.document, "section": q.source.section},
                    }
                    for q in result.missed
                ],
                "unanswered": [cur.questions.index(q) + 1 for q in result.unanswered],
            },
        }
    else:  # objection_answered
        changed, sentence = training.record_objection(
            cur, progress, str(args.get("scenario") or ""), args.get("number"), person.reply_text
        )
        out = {**_status(cur, progress), "ok": changed, "say": sentence}
    if changed:
        try:
            training.write_progress(person.address, progress)
        except training.TrainingError as exc:
            logger.warning("hermes-smd-training: progress not kept (%s)", type(exc).__name__)
            return _refused("The progress could not be recorded just now; nothing was kept.")
    return _say(out)


def tone_handler(args: dict[str, Any], **kwargs: Any) -> str:
    person = training.turn_person(kwargs.get("session_id"))
    if person is None:
        return _refused(training.NO_PERSON)
    if not person.reply_text.strip():
        return _refused("This email carries no words of the representative's own to check.")
    cur, problem = _load()
    if cur is None:
        return _refused(problem)
    try:
        rules = training.load_tone_rules()
    except training.TrainingError as exc:
        logger.warning("hermes-smd-training: tone rules unreadable (%s)", exc)
        return _refused("The tone rules could not be opened just now; nothing was recorded.")
    misses = training.tone_misses(person.reply_text, rules)
    progress = _progress_for(person, cur)
    total = training.record_tone(progress, misses)
    try:
        training.write_progress(person.address, progress)
    except training.TrainingError as exc:
        logger.warning("hermes-smd-training: tone check not kept (%s)", type(exc).__name__)
        return _refused("The tone check could not be recorded just now; nothing was kept.")
    return _say(
        {
            "ok": True,
            "misses": total,
            "rules_missed": [{"rule": m.rule, "times": m.count, "fix": m.fix} for m in misses],
        }
    )


_HANDLERS = {TOOL_RECORD: record_handler, TOOL_TONE: tone_handler}


def register(ctx: Any) -> None:
    """Register both tools on a seat that authors representative training; nothing elsewhere."""
    if not training.authored():
        logger.info("hermes-smd-training: representative training not authored; no tools")
        return
    for name, (description, schema) in TOOLS.items():
        register_wrapped_tool(
            ctx,
            name=name,
            toolset="training",
            schema=schema,
            handler=_HANDLERS[name],
            description=description,
            emoji="",
        )
    logger.info("hermes-smd-training registered %d tools", len(TOOLS))


__all__ = [
    "ACTIONS",
    "TOOLS",
    "TOOL_RECORD",
    "TOOL_TONE",
    "record_handler",
    "register",
    "tone_handler",
]
