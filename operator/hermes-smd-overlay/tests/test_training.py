"""Representative training on a guide seat (ss-console #3946): logic in code.

Drives the REAL pieces on the seat's path: a ``customer.yaml`` read by
``CustomerConfig``, the broker's enrolled list read by
``shared.sender_enrollment``, the webhook router's origin derivation and the
inbound plugin's real ``pre_llm_call`` bind on an agent thread (so the tools
read the representative's own words from the verified email), the record
store's managed partition, the training plugin's real handlers called the way
Hermes' ``registry.dispatch`` calls them, and the skill's real data files under
``operator/skills``.

Each test fails before this change: there was no ``shared.training``, no
``hermes-smd-training`` plugin, no ``rep-training`` skill, and the welcome had
no learning path or privacy wording.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from shared import inbound, inbound_message, provenance, record_store, training
from shared.person_prefs import person_slug
from tests.conftest import FakePluginContext, load_plugin
from tests.test_turn_origin_tool_thread import _hermes_render, _on_thread, _unpark

REPO_SKILLS = Path(__file__).resolve().parents[2] / "skills"
TRAINING_DIR = REPO_SKILLS / "rep-training"
PRACTICE_DIR = REPO_SKILLS / "pitch-practice"
LIBRARY = REPO_SKILLS / "product-guide" / "references"

A = "rep-a@example.test"
B = "rep-b@example.test"
ADMIN = "admin@example.test"
CRON = "cron_abc123def456_20261110_090000"
A_MARK = "CANARY-A-7f3k9q"
B_MARK = "CANARY-B-2m8x4w"
#: Every answer right except question 9.
A_QUIZ = "1 B\n2. C\nQ3: A\n4) D\n5 A\n6 C\n7 B\n8 C\n9 B\n10 A"


def _seat_doc(
    volume: Path, *, role: str = "guide", skill: bool = True, managed: bool = True
) -> dict:
    progress: dict[str, Any] = {
        "name": "rep-progress",
        "path": f"{volume}/rep/progress",
        "partition": "sender",
    }
    if managed:
        progress["managed"] = True
    return {
        "customer_id": "guide-test",
        "seat": {"kind": "internal", "product": "operator", "role": role},
        "sender_enrollment": {"enabled": True},
        "scope": {"inbound_allow_from": [ADMIN], "admins": [ADMIN]},
        "personas": [
            {
                "slug": "guide",
                "skills": [
                    {"name": "product-guide", "enabled": True},
                    {"name": "rep-training", "enabled": skill},
                ],
            }
        ],
        "record_stores": [
            {"name": "rep-notes", "path": f"{volume}/rep/notes", "partition": "sender"},
            progress,
        ],
    }


def _enroll(path: Path, *people: tuple[str, str | None]) -> None:
    entries = [
        {
            "address": a,
            "label": "t",
            "code_id": "c_1",
            "enrolled_at": "x",
            **({"name": n} if n else {}),
        }
        for a, n in people
    ]
    path.write_text(json.dumps({"schema": 1, "enrolled": entries}), encoding="utf-8")


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ctx: FakePluginContext):
    volume = tmp_path / "opt-data"
    volume.mkdir()
    monkeypatch.setattr(record_store, "VOLUME_ROOT", volume)
    # The seat reads the skill from /app/skills; a test reads the repo's copy.
    monkeypatch.setattr(training, "_IMAGE_SKILL_ROOTS", (str(REPO_SKILLS),))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(yaml.safe_dump(_seat_doc(volume)), encoding="utf-8")
    enrolled = tmp_path / "enrolled.json"
    _enroll(enrolled, (A, "Avery Example"), (B, None))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml_path))
    monkeypatch.setenv("SMD_SENDER_ENROLLMENT_ENROLLED", str(enrolled))
    provenance._reset_for_tests()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()
    plugin = load_plugin("hermes-smd-training")
    plugin.register(fake_ctx)
    state = {
        "plugin": plugin,
        "tools": fake_ctx.tools,
        "inbound": load_plugin("hermes-smd-inbound"),
        "router": load_plugin("hermes-smd-webhook-router"),
        "volume": volume,
        "yaml": yaml_path,
        "enrolled": enrolled,
        "progress": volume / "rep" / "progress",
        "notes": volume / "rep" / "notes",
        "n": 0,
    }
    yield state
    _unpark()
    provenance._reset_for_tests()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()


def _email(seat: dict, sender: str, body: str) -> str:
    """One verified email bound to a fresh session, with the provider's
    quoted-history-free text (``extracted_text``) as AgentMail sends it.
    Returns the session id."""
    from bootstrap.translate import _INBOUND_EMAIL_PROMPT

    seat["n"] += 1
    session = f"20261110_1601{seat['n']:02d}_sess{seat['n']:04d}"
    mid = f"<m-{seat['n']}@example.test>"
    payload = {
        "source": "agentmail",
        "event_type": "message.received",
        "message": {
            "inbox_id": "guide@agentmail.to",
            "message_id": mid,
            "from": sender,
            "subject": "training",
            "text": body,
            "extracted_text": body,
        },
    }
    dto = inbound_message.normalize_inbound("agentmail", payload)
    origin = seat["router"]._origin_from_dto(dto, content=body)
    inbound.SESSION_INBOUND_ORIGIN.record("", origin)
    prompt = _hermes_render(_INBOUND_EMAIL_PROMPT, payload)

    def pre_llm_call() -> None:
        provenance.note_session(session)
        seat["inbound"].on_pre_llm_call(
            session_id=session,
            task_id=f"task-{session}",
            turn_id="turn-1",
            user_message=prompt,
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="webhook",
            parent_session_id="",
            sender_id="webhook:agentmail",
        )

    _on_thread(pre_llm_call)
    assert inbound.SESSION_INBOUND_ORIGIN.bound_this_turn(session).reply_text == body
    return session


def _call(seat: dict, tool: str, args: dict, session: str | None) -> dict[str, Any]:
    handler = seat["tools"][tool]["handler"]
    kwargs: dict[str, Any] = {"task_id": "task-x", "user_task": None}
    if session is not None:
        kwargs["session_id"] = session
    return json.loads(_on_thread(handler, args, **kwargs))


def _record(seat: dict, address: str) -> dict:
    path = seat["progress"] / person_slug(address) / training.PROGRESS_RECORD
    return json.loads(path.read_text(encoding="utf-8"))


def _cur() -> training.Curriculum:
    return training.load_curriculum(TRAINING_DIR, PRACTICE_DIR)


# ---------------------------------------------------------------------------
# where it is switched on
# ---------------------------------------------------------------------------


def test_registers_both_tools_only_where_training_is_authored(seat, tmp_path, monkeypatch) -> None:
    assert set(seat["tools"]) == {"training_record", "tone_check"}
    for variant in (
        {"role": "marketing"},
        {"skill": False},
        {"managed": False},
    ):
        path = tmp_path / f"variant-{len(variant)}-{next(iter(variant))}.yaml"
        path.write_text(yaml.safe_dump(_seat_doc(seat["volume"], **variant)), encoding="utf-8")
        monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
        ctx = FakePluginContext()
        load_plugin("hermes-smd-training").register(ctx)
        assert ctx.tools == {}, variant


# ---------------------------------------------------------------------------
# the skill's data
# ---------------------------------------------------------------------------


def _sections(document: str) -> list[str]:
    text = (LIBRARY / document).read_text(encoding="utf-8")
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def test_every_lesson_and_question_cites_a_library_section_that_exists() -> None:
    cur = _cur()
    missing = []
    for lesson in cur.lessons:
        for s in lesson.read:
            if not (LIBRARY / s.document).exists() or s.section not in _sections(s.document):
                missing.append(f"{lesson.id}: {s.document} {s.section!r}")
        if lesson.guide:
            assert (TRAINING_DIR / "references" / lesson.guide).is_file(), lesson.id
    for q in cur.questions:
        if s := q.source:
            if not (LIBRARY / s.document).exists() or s.section not in _sections(s.document):
                missing.append(f"{q.id}: {s.document} {s.section!r}")
    assert missing == []
    assert len(cur.lessons) == 10 and len(cur.questions) == 10 and cur.pass_correct == 8
    # The answer key is spread across the letters, so a run of one letter fails.
    assert {q.answer for q in cur.questions} == set(training.LETTERS)
    # The industry example is not a buyer readiness counts.
    assert "law-practice" not in cur.scenarios and cur.scenarios["owner"] == 6


def test_the_progress_store_named_in_the_curriculum_is_the_one_code_writes() -> None:
    doc = yaml.safe_load((TRAINING_DIR / "references" / "curriculum.yaml").read_text())
    assert doc["progress_store"] == training.PROGRESS_STORE


# ---------------------------------------------------------------------------
# the rules, in code
# ---------------------------------------------------------------------------


KEY = "BCADACBCDA"
ALL = {i + 1: x for i, x in enumerate(KEY)}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (A_QUIZ, {**ALL, 9: "B"}),
        # Many numbered answers on one line (each graded as only {1: B} before).
        (" ".join(f"{i}-{x}" for i, x in ALL.items()), ALL),
        (", ".join(f"{i}. {x}" for i, x in ALL.items()), ALL),
        ("Q1: B Q2: C", {1: "B", 2: "C"}),
        ("1 B 2 C 3 A", {1: "B", 2: "C", 3: "A"}),
        # Letters alone, with or without a sign-off around them.
        ("b, c, a, d, a, c, b, c, d, a", ALL),
        ("b, c, a, d, a, c, b, c, d, a\nThanks, Sam", ALL),
        ("Hi,\nB C A D A\nC B C D A\n\nThanks, Sam", ALL),
        ("B C A", {}),  # letters alone, but not one per question
        # The last answer for a number wins.
        ("1 B\n1 C\n2 A", {1: "C", 2: "A"}),
        # Prose: the word "a" is never answer A, and no number is guessed.
        ("2 a lot of thought went into this one, C", {}),
        ("I think 1 is B and 2 is C", {}),
        ("1 Because it is refused", {}),
        ("Question 11 - B and 0) C", {}),  # numbers outside 1..10
    ],
)
def test_quiz_answers_are_parsed_by_code(text: str, expected: dict) -> None:
    assert training.parse_answers(text, 10) == expected


def test_a_quiz_with_answers_missing_is_not_graded_and_names_what_is_missing() -> None:
    cur = _cur()
    p = training.new_progress()
    result = training.grade_quiz(cur, p, "1-B 2-C 3-A\nThanks, Sam")
    assert result is not None and not result.complete
    assert result.missing == (4, 5, 6, 7, 8, 9, 10)
    assert p["quiz"] == training.new_progress()["quiz"], "nothing is recorded"
    assert training.missing_sentence(result.missing).startswith(
        "This email does not answer questions 4, 5, 6, 7, 8, 9, 10, so the quiz was not graded"
    )


def test_every_attempt_is_counted_and_readiness_uses_the_best() -> None:
    cur = _cur()
    p = training.new_progress()
    for letters in ("A" * 10, KEY[:8] + "BB", "A" * 10):
        training.grade_quiz(cur, p, ", ".join(letters))
    assert p["quiz"] == {"attempts": 3, "best_correct": 8, "last_correct": 3}
    quiz = next(c for c in training.readiness(cur, p)[1] if c.id == "quiz")
    assert quiz.met and quiz.value == "best quiz result 8 of 10 correct, 8 needed, 3 attempts"


def test_lessons_go_in_order_and_requirements_are_code_checked() -> None:
    cur = _cur()
    p = training.new_progress()
    assert training.complete_lesson(cur, p, "l02")[0] is False
    for n in range(1, 9):
        assert training.complete_lesson(cur, p, f"l0{n}") == (True, f"Lesson l0{n} is complete.")
    changed, why = training.complete_lesson(cur, p, "l09")
    assert not changed and "tone check" in why
    training.record_tone(p, [])
    assert training.complete_lesson(cur, p, "l09")[0] is True
    changed, why = training.complete_lesson(cur, p, "l10")
    assert not changed and "mock pitch" in why
    for n in range(1, 7):
        assert training.record_objection(cur, p, "owner", n, "a real answer in several words")[0]
    assert training.complete_lesson(cur, p, "l10")[0] is True


def test_an_objection_counts_only_when_answered_in_order_with_an_answer() -> None:
    cur = _cur()
    p = training.new_progress()
    assert (
        training.record_objection(cur, p, "owner", 2, "a real answer in several words")[0] is False
    )
    assert training.record_objection(cur, p, "owner", 1, "ok")[0] is False
    assert (
        training.record_objection(cur, p, "law-practice", 1, "a real answer in several words")[0]
        is False
    )
    assert (
        training.record_objection(cur, p, "owner", 1, "a real answer in several words")[0] is True
    )
    assert (
        training.record_objection(cur, p, "owner", 1, "a real answer in several words")[0] is False
    )
    assert p["mock_pitch"] == {"owner": [1]}


def test_readiness_is_the_four_code_checked_facts_and_nothing_else() -> None:
    cur = _cur()
    p = training.new_progress()
    p["lessons_complete"] = [x.id for x in cur.lessons]
    training.grade_quiz(cur, p, A_QUIZ)
    for n in range(1, 7):
        training.record_objection(cur, p, "owner", n, "a real answer in several words")
    training.record_tone(p, [])
    assert training.readiness(cur, p)[0] is True
    # A rubric score or any other key is no input, and is dropped on read.
    scored = {**p, "rubric": {"accuracy": 1}, "coach_notes": f"weak answer {A_MARK}"}
    assert training.readiness(cur, training.clean_progress(scored, cur))[0] is True
    assert "rubric" not in training.dumps(scored) and A_MARK not in training.dumps(scored)
    # Each fact alone breaks it.
    for broken in (
        {"lessons_complete": p["lessons_complete"][:-1]},
        {"quiz": {**p["quiz"], "best_correct": 7}},
        {"mock_pitch": {"owner": [1, 2, 3, 4, 5]}},
        {"tone": {**p["tone"], "last_misses": 1}},
    ):
        assert training.readiness(cur, {**p, **broken})[0] is False, broken


def test_a_progress_record_can_never_carry_words() -> None:
    planted = {
        "lessons_complete": ["l01", f"note {A_MARK}", "l99"],
        "quiz": {"attempts": "three", "best_correct": 9, "last_correct": -1},
        "mock_pitch": {"owner": [1, "two", 99], f"x {A_MARK}": [1]},
        "tone": {"checks": 1, "last_misses": 0, "total_misses": 0, "quote": A_MARK},
        "notes": A_MARK,
    }
    cleaned = training.dumps(training.clean_progress(planted, _cur()))
    assert A_MARK not in cleaned and "two" not in cleaned and "three" not in cleaned
    assert json.loads(cleaned)["lessons_complete"] == ["l01"]
    assert json.loads(cleaned)["mock_pitch"] == {"owner": [1]}


def test_every_tone_rule_fires_on_a_planted_draft_and_a_clean_draft_has_none() -> None:
    rules = training.load_tone_rules(TRAINING_DIR)
    planted = {
        "no-amount": "It costs $500 a month.",
        "no-timeframe": "We finish in two weeks.",
        "no-ai-employee": "Meet your new AI employee.",
        "no-ai-powered": "An AI-powered firm.",
        "solution-not-systems": "We build better systems.",
        "collaborative-not-diagnostic": "First we audit your operations.",
        "no-claim-to-know": "I know what your business needs.",
        "no-replace-people": "It will replace your staff.",
        "no-guarantee": "We guarantee results.",
        "no-long-dash": "Fast — and safe.",
        "no-exclamation": "Great news!",
        "no-shouting": "This is TRULY AMAZING.",
    }
    assert set(planted) == {r.id for r in rules}, "every authored rule has a planted case"
    for rule_id, text in planted.items():
        assert rule_id in {m.rule for m in training.tone_misses(text, rules)}, rule_id
    clean = (
        "Where are you trying to take the business, and what gets in the way today? "
        "We work alongside you and figure out the right solution together, twice a day if need be."
    )
    assert training.tone_misses(clean, rules) == []


@pytest.mark.parametrize(
    ("text", "shouts"),
    [
        ("We follow HIPAA and OSHA rules, and SMD runs it.", False),
        ("Ask about the CRM or the ERP integration.", False),
        ("Our team keeps a FAQ.\nOSHA training is separate.", False),
        ("This is a GAME CHANGER for you.", True),
        ("PLEASE READ THIS before the call.", True),
    ],
)
def test_shouting_is_a_run_of_capitals_never_one_acronym(text: str, shouts: bool) -> None:
    rules = training.load_tone_rules(TRAINING_DIR)
    assert ("no-shouting" in {m.rule for m in training.tone_misses(text, rules)}) is shouts


# ---------------------------------------------------------------------------
# the tools, through the real handlers and the verified email
# ---------------------------------------------------------------------------


def test_a_whole_path_to_ready_is_recorded_by_code(seat) -> None:
    s = _email(seat, A, f"start training {A_MARK}")
    status = _call(seat, "training_record", {"action": "status"}, s)
    assert status["next_lesson"]["id"] == "l01" and status["ready"] is False
    for n in range(1, 9):
        s = _email(seat, A, f"my answer to the exercise {A_MARK}")
        assert _call(
            seat, "training_record", {"action": "complete_lesson", "lesson_id": f"l0{n}"}, s
        )["ok"]
    s = _email(seat, A, f"We build better systems in two weeks! {A_MARK}")
    tone = _call(seat, "tone_check", {}, s)
    assert tone["misses"] >= 3 and {"solution-not-systems", "no-timeframe"} <= {
        m["rule"] for m in tone["rules_missed"]
    }
    assert _call(seat, "training_record", {"action": "complete_lesson", "lesson_id": "l09"}, s)[
        "ok"
    ]
    s = _email(seat, A, "Where are you trying to take the business?")
    assert _call(seat, "tone_check", {}, s)["misses"] == 0
    for n in range(1, 7):
        s = _email(seat, A, f"an answer to the buyer with substance {A_MARK}")
        out = _call(
            seat,
            "training_record",
            {"action": "objection_answered", "scenario": "owner", "number": n},
            s,
        )
        assert out["ok"], out
    assert _call(seat, "training_record", {"action": "complete_lesson", "lesson_id": "l10"}, s)[
        "ok"
    ]
    s = _email(seat, A, "quiz please")
    quiz = _call(seat, "training_record", {"action": "quiz"}, s)
    # The key never reaches the model: each question is its words and choices only.
    assert len(quiz["questions"]) == 10
    assert all(set(q) == {"number", "ask", "choices"} for q in quiz["questions"])
    s = _email(seat, A, f"{A_QUIZ}\nthanks {A_MARK}")
    graded = _call(seat, "training_record", {"action": "grade_quiz"}, s)
    assert graded["quiz"]["correct"] == 9 and graded["quiz"]["passed"] is True
    assert [m["number"] for m in graded["quiz"]["missed"]] == [9]
    assert graded["ready"] is True
    record = _record(seat, A)
    assert A_MARK not in json.dumps(record)
    assert record["quiz"] == {"attempts": 1, "best_correct": 9, "last_correct": 9}


def test_the_grade_comes_from_the_email_never_from_the_model(seat) -> None:
    s = _email(seat, B, "I am not sure yet, can we talk about it?")
    out = _call(seat, "training_record", {"action": "grade_quiz", "number": 10}, s)
    assert out["ok"] is False and "No quiz answers" in out["say"]
    assert not (seat["progress"] / person_slug(B)).exists()
    s = _email(seat, B, "1-B 2-C\nThanks, Sam")
    out = _call(seat, "training_record", {"action": "grade_quiz"}, s)
    assert out == {"ok": False, "say": training.missing_sentence((3, 4, 5, 6, 7, 8, 9, 10))}
    assert not (seat["progress"] / person_slug(B)).exists()


def test_each_person_sees_only_their_own_progress(seat) -> None:
    sa = _email(seat, A, f"start {A_MARK}")
    _call(seat, "training_record", {"action": "complete_lesson", "lesson_id": "l01"}, sa)
    sb = _email(seat, B, f"start {B_MARK}")
    status_b = _call(seat, "training_record", {"action": "status"}, sb)
    assert status_b["lessons_complete"] == 0
    assert _record(seat, A)["lessons_complete"] == ["l01"]
    assert _record(seat, B)["lessons_complete"] == []


@pytest.mark.parametrize("tool", ["training_record", "tone_check"])
def test_a_turn_with_no_verified_sender_is_refused(seat, tool: str) -> None:
    _on_thread(provenance.note_session, CRON)
    args = {"action": "status"} if tool == "training_record" else {}
    for session in (CRON, None, "20261110_999999_nobody00"):
        out = _call(seat, tool, args, session)
        assert out == {"ok": False, "say": training.NO_PERSON}
    assert not seat["progress"].exists() or not any(seat["progress"].iterdir())


def test_a_person_removed_is_refused(seat) -> None:
    s = _email(seat, B, "start")
    _enroll(seat["enrolled"], (A, None))
    assert _call(seat, "training_record", {"action": "status"}, s)["say"] == training.NO_PERSON


# ---------------------------------------------------------------------------
# the welcome and the progress record at enrollment
# ---------------------------------------------------------------------------


def test_the_training_welcome_is_the_guides_own_introduction() -> None:
    """The Captain's text (2026-11-10): first person, the start-training path,
    no privacy paragraph, nothing about codes, email or the library."""
    from shared import sender_enrollment as se

    text = se.welcome_text("Avery Example", training=True)
    assert text.startswith("Hi Avery,\n\nI'm the SMD Services guide.")
    assert '"start training"' in text and not hasattr(se, "PRIVACY_NOTICE")
    assert "SMD sees your progress" not in text
    # Every other seat's welcome is exactly what it was.
    plain = se.welcome_text("Avery Example")
    assert "training" not in plain
    assert plain == se._WELCOME_BODY.format(greeting="Hello Avery Example,")


def test_enrollment_creates_the_progress_record_by_code(seat) -> None:
    from shared.customer_config import CustomerConfig

    c = "rep-c@example.test"
    _enroll(seat["enrolled"], (A, None), (B, None), (c, "Casey Example"))
    cfg = CustomerConfig.from_volume(str(seat["yaml"]))
    assert seat["router"]._start_training(cfg, c) is True
    assert _record(seat, c) == training.new_progress()
    # A second enrollment keeps what is there.
    p = _record(seat, c)
    p["lessons_complete"] = ["l01"]
    training.write_progress(c, p, config=cfg)
    assert seat["router"]._start_training(cfg, c) is True
    assert _record(seat, c)["lessons_complete"] == ["l01"]


def test_a_seat_without_training_gets_the_old_welcome_and_writes_nothing(seat, tmp_path) -> None:
    from shared.customer_config import CustomerConfig

    path = tmp_path / "no-training.yaml"
    path.write_text(yaml.safe_dump(_seat_doc(seat["volume"], skill=False)), encoding="utf-8")
    cfg = CustomerConfig.from_volume(str(path))
    assert seat["router"]._start_training(cfg, A) is False
    assert not seat["progress"].exists()
