"""Representative training on a guide seat: lessons, a graded quiz, readiness (ss-console #3946).

WHAT THIS IS. A guide seat (ss ADR 0105) that authors the ``rep-training`` skill
teaches a new sales representative the product: guided lessons in order, an
open-book quiz, a mock pitch against the authored buyer scenarios, and a tone
check of their own draft wording. SMD's administrators receive each
representative's progress and readiness in a weekly report
(:mod:`shared.training_report`).

LOGIC IN CODE, THE MODEL SUPPLIES VALUES. The model names a lesson id, a scenario
id, an objection number; everything else is decided here:

* lesson order (the next lesson is the first one not complete; a lesson cannot
  be skipped), and what completing one needs (``requires``: nothing, a tone
  check on file, or a whole mock pitch answered);
* the quiz grade: the answer letters are parsed from the representative's own
  words in the email that opened this turn (``InboundOrigin.reply_text``, the
  provider's quoted-history-free text), never from anything the model passes,
  and graded against the key in the skill's data;
* an objection counts as answered only when the email that opened the turn
  carries an answer of some substance, in order;
* tone misses are counted by deterministic rules authored as seat data
  (``rep-training/references/tone-rules.yaml``), never judged by the model;
* readiness is computed from those four code-checked facts only. The model's
  rubric feedback in a practice is coaching for the representative and is no
  input to readiness.

WHAT IS STORED. One progress record per person, ``progress.json`` in the
authored progress store (:data:`PROGRESS_STORE`, a record store walled per
person, ``partition: sender``, that only code writes, ``managed: true``). It
holds criterion ids and counts only: lesson ids completed, quiz counts, the
objection numbers answered per scenario, tone check counts. Never a quote of
anything the representative wrote. A notes or leads store is never read here.

WHO. Every call resolves its person from the strict per-turn verified sender
(``SESSION_INBOUND_ORIGIN.bound_this_turn``), keyed by the session id Hermes
hands the tool handler, exactly as the partitioned record store does: a turn
with no verified sender (a scheduled turn, a chat turn) or a sender not on the
seat's roster now gets :data:`NO_PERSON`. There is no argument naming a person.

WHERE IT IS SWITCHED ON. :func:`authored`: a guide seat whose persona enables
the ``rep-training`` skill and which authors the progress store walled per
person and managed. Every other seat registers nothing and changes nothing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shared import pre_run_handoff, seat_layout

logger = logging.getLogger(__name__)

SKILL = "rep-training"
PRACTICE_SKILL = "pitch-practice"
#: The record store the progress lives in. Authored on the seat as
#: ``partition: sender`` and ``managed: true``; :func:`authored` refuses
#: anything else.
PROGRESS_STORE = "rep-progress"
PROGRESS_RECORD = "progress.json"
SCHEMA = 1
ROLE = "guide"

#: The skill's data, relative to the skill directory.
CURRICULUM_REL = Path("references") / "curriculum.yaml"
TONE_RULES_REL = Path("references") / "tone-rules.yaml"
SCENARIOS_REL = Path("references") / "scenarios.yaml"

#: The volume's skill tree, and the image's read-only one. A persona's own copy
#: (``skills/`` under the process's home, and under each profile home beneath
#: it) is tried first, as every other skill reader on the seat does.
_IMAGE_SKILL_ROOTS: tuple[str, ...] = (seat_layout.SKILLS, "/app/skills")

#: An answer to an objection must carry at least this many words to count.
MIN_ANSWER_WORDS = 4

#: The one sentence every refusal for "no person" returns: same words for every
#: cause, so it says nothing about anyone's progress.
NO_PERSON = (
    "Training progress is kept for the person who emailed this guide, and this turn has no "
    "verified sender listed on this seat, so nothing was read or recorded."
)

REQUIRES = ("none", "tone_check", "mock_pitch")
LETTERS = ("A", "B", "C", "D")


class TrainingError(RuntimeError):
    """The training data cannot be read, or a progress record cannot be kept."""


# ---------------------------------------------------------------------------
# Where switched on
# ---------------------------------------------------------------------------


def _skill_enabled(raw: dict[str, Any], skill: str) -> bool:
    for persona in raw.get("personas") or []:
        if not isinstance(persona, dict):
            continue
        for entry in persona.get("skills") or []:
            if (
                isinstance(entry, dict)
                and entry.get("name") == skill
                and entry.get("enabled") is True
            ):
                return True
    return False


def authored(config: Any = None) -> bool:
    """True only on a guide seat that enables ``rep-training`` and authors the
    progress store walled per person and written only by code. Anything that
    cannot be read is False (nothing registers, nothing changes)."""
    try:
        from shared import record_store
        from shared.customer_config import CustomerConfig

        cfg = config if config is not None else CustomerConfig.from_volume()
        raw = cfg.raw
        seat = raw.get("seat") if isinstance(raw, dict) else None
        if not isinstance(seat, dict) or seat.get("role") != ROLE:
            return False
        if not _skill_enabled(raw, SKILL):
            return False
        policy = record_store.authored_policies(cfg).get(PROGRESS_STORE)
        return bool(policy is not None and policy.partitioned and policy.managed)
    except Exception:  # noqa: BLE001 - could not tell is not switched on
        logger.debug("training: authored check failed", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# The skill's data
# ---------------------------------------------------------------------------


def skill_dir(skill: str = SKILL, *, home: str | None = None) -> Path:
    """The directory ``skill`` is read from on this seat. Raises TrainingError
    when no copy is found."""
    # The process's home, resolved the one way every pre-run reader resolves
    # it (``shared.pre_run_handoff``): ``<home>/.smd/pre_run``.
    base = Path(home) if home else pre_run_handoff.handoff_dir().parents[1]
    candidates = [base / "skills" / skill]
    profiles = base / "profiles"
    if profiles.is_dir():
        candidates.extend(sorted(p / "skills" / skill for p in profiles.iterdir() if p.is_dir()))
    candidates.extend(Path(root) / skill for root in _IMAGE_SKILL_ROOTS)
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file():
            return candidate
    raise TrainingError(f"the {skill} skill's files are not on this seat")


@dataclass(frozen=True)
class Source:
    document: str
    section: str


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    read: tuple[Source, ...]
    exercise: str
    requires: str = "none"
    #: A page of the training skill's own ``references/`` this lesson reads too.
    guide: str = ""


@dataclass(frozen=True)
class Question:
    id: str
    ask: str
    choices: tuple[tuple[str, str], ...]
    answer: str
    source: Source


@dataclass(frozen=True)
class Curriculum:
    lessons: tuple[Lesson, ...]
    questions: tuple[Question, ...]
    pass_correct: int
    #: scenario id -> objection count (the pitch-practice buyer scenarios).
    scenarios: dict[str, int] = field(default_factory=dict)

    def lesson(self, lesson_id: str) -> Lesson | None:
        return next((x for x in self.lessons if x.id == lesson_id), None)


_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _source(raw: Any, where: str) -> Source:
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("document"), str)
        or not isinstance(raw.get("section"), str)
    ):
        raise TrainingError(f"{where}: a source names a document and a section")
    return Source(raw["document"], raw["section"])


def _lessons(raw: Any) -> tuple[Lesson, ...]:
    if not isinstance(raw, list) or not raw:
        raise TrainingError("curriculum: no lessons")
    out: list[Lesson] = []
    for i, entry in enumerate(raw):
        where = f"lessons[{i}]"
        if not isinstance(entry, dict):
            raise TrainingError(f"{where}: not a mapping")
        lid, title, exercise = entry.get("id"), entry.get("title"), entry.get("exercise")
        requires = entry.get("requires", "none")
        if not isinstance(lid, str) or not _ID_RE.match(lid):
            raise TrainingError(f"{where}: bad id")
        if not isinstance(title, str) or not isinstance(exercise, str) or requires not in REQUIRES:
            raise TrainingError(f"{where}: title, exercise and requires are required")
        guide = entry.get("guide", "")
        if not isinstance(guide, str) or (guide and not re.match(r"^[a-z0-9-]+\.md$", guide)):
            raise TrainingError(f"{where}: guide names a page of this skill's references")
        reads = entry.get("read") or []
        if not isinstance(reads, list) or not reads:
            raise TrainingError(f"{where}: a lesson reads at least one library section")
        out.append(
            Lesson(
                lid,
                title.strip(),
                tuple(_source(r, where) for r in reads),
                " ".join(exercise.split()),
                requires,
                guide,
            )
        )
    if len({x.id for x in out}) != len(out):
        raise TrainingError("curriculum: duplicate lesson id")
    return tuple(out)


def _questions(raw: Any) -> tuple[tuple[Question, ...], int]:
    if not isinstance(raw, dict):
        raise TrainingError("curriculum: no quiz")
    items = raw.get("questions")
    if not isinstance(items, list) or not items:
        raise TrainingError("curriculum: the quiz has no questions")
    out: list[Question] = []
    for i, entry in enumerate(items):
        where = f"quiz.questions[{i}]"
        if not isinstance(entry, dict):
            raise TrainingError(f"{where}: not a mapping")
        choices = entry.get("choices")
        if not isinstance(choices, dict) or sorted(choices) != list(LETTERS):
            raise TrainingError(f"{where}: choices are exactly A, B, C and D")
        answer = entry.get("answer")
        if answer not in LETTERS:
            raise TrainingError(f"{where}: the answer is one of A, B, C, D")
        qid, ask = entry.get("id"), entry.get("ask")
        if not isinstance(qid, str) or not _ID_RE.match(qid) or not isinstance(ask, str):
            raise TrainingError(f"{where}: id and ask are required")
        out.append(
            Question(
                qid,
                " ".join(ask.split()),
                tuple((k, " ".join(str(choices[k]).split())) for k in LETTERS),
                answer,
                _source(entry.get("source"), where),
            )
        )
    pass_correct = raw.get("pass_correct")
    if (
        not isinstance(pass_correct, int)
        or isinstance(pass_correct, bool)
        or not 1 <= pass_correct <= len(out)
    ):
        raise TrainingError("curriculum: pass_correct is between 1 and the number of questions")
    return tuple(out), pass_correct


def _scenarios(path: Path) -> dict[str, int]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TrainingError(
            f"the buyer scenarios could not be read ({type(exc).__name__})"
        ) from exc
    out: dict[str, int] = {}
    for entry in doc.get("scenarios") or []:
        # The industry example (``example: true``) is an illustration of one
        # kind of business, not a buyer every representative meets; a mock
        # pitch that counts toward readiness is one of the general buyers.
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry.get("example") is not True
        ):
            objections = entry.get("objections")
            if isinstance(objections, list) and objections:
                out[entry["id"]] = len(objections)
    if not out:
        raise TrainingError("no buyer scenario has objections")
    return out


def load_curriculum(
    training_dir: Path | None = None, practice_dir: Path | None = None
) -> Curriculum:
    """The curriculum, the quiz and the scenario sizes, from the skills' data."""
    tdir = training_dir if training_dir is not None else skill_dir(SKILL)
    pdir = practice_dir if practice_dir is not None else skill_dir(PRACTICE_SKILL)
    try:
        doc = yaml.safe_load((tdir / CURRICULUM_REL).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TrainingError(f"the curriculum could not be read ({type(exc).__name__})") from exc
    if not isinstance(doc, dict):
        raise TrainingError("curriculum: not a mapping")
    questions, pass_correct = _questions(doc.get("quiz"))
    return Curriculum(
        lessons=_lessons(doc.get("lessons")),
        questions=questions,
        pass_correct=pass_correct,
        scenarios=_scenarios(pdir / SCENARIOS_REL),
    )


# ---------------------------------------------------------------------------
# Tone rules (seat data) and the check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToneRule:
    id: str
    pattern: re.Pattern[str]
    fix: str


@dataclass(frozen=True)
class ToneMiss:
    rule: str
    count: int
    fix: str


def load_tone_rules(training_dir: Path | None = None) -> tuple[ToneRule, ...]:
    """The authored tone rules. ``kind`` is ``literal`` (case-insensitive) or
    ``regex`` (case-insensitive unless ``case: sensitive``)."""
    tdir = training_dir if training_dir is not None else skill_dir(SKILL)
    try:
        doc = yaml.safe_load((tdir / TONE_RULES_REL).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TrainingError(f"the tone rules could not be read ({type(exc).__name__})") from exc
    out: list[ToneRule] = []
    for i, entry in enumerate(doc.get("rules") or []):
        if not isinstance(entry, dict):
            raise TrainingError(f"rules[{i}]: not a mapping")
        rid, kind, text, fix = (
            entry.get("id"),
            entry.get("kind"),
            entry.get("pattern"),
            entry.get("fix"),
        )
        if (
            not isinstance(rid, str)
            or not _ID_RE.match(rid)
            or not isinstance(text, str)
            or not text
        ):
            raise TrainingError(f"rules[{i}]: id and pattern are required")
        if not isinstance(fix, str) or not fix.strip():
            raise TrainingError(f"rules[{i}]: every rule says how to fix it")
        flags = 0 if entry.get("case") == "sensitive" else re.IGNORECASE
        if kind == "literal":
            pattern = re.compile(re.escape(text), flags)
        elif kind == "regex":
            try:
                pattern = re.compile(text, flags)
            except re.error as exc:
                raise TrainingError(f"rules[{i}]: the pattern does not compile") from exc
        else:
            raise TrainingError(f"rules[{i}]: kind is literal or regex")
        out.append(ToneRule(rid, pattern, " ".join(fix.split())))
    if not out:
        raise TrainingError("no tone rule is authored")
    if len({r.id for r in out}) != len(out):
        raise TrainingError("duplicate tone rule id")
    return tuple(out)


def tone_misses(text: str, rules: tuple[ToneRule, ...]) -> list[ToneMiss]:
    """Every rule ``text`` breaks, with how many times. Deterministic."""
    found: list[ToneMiss] = []
    for rule in rules:
        n = len(rule.pattern.findall(text or ""))
        if n:
            found.append(ToneMiss(rule.id, n, rule.fix))
    return found


# ---------------------------------------------------------------------------
# The progress record: ids and counts, never words
# ---------------------------------------------------------------------------


def new_progress() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "lessons_complete": [],
        "quiz": {"attempts": 0, "best_correct": 0, "last_correct": 0},
        "mock_pitch": {},
        "tone": {"checks": 0, "last_misses": 0, "total_misses": 0},
    }


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def clean_progress(raw: Any, cur: Curriculum | None = None) -> dict[str, Any]:
    """A progress record holding only the fields and value types this module
    writes. Anything else (an unknown key, a string where a count belongs) is
    dropped, so the record can never carry words, even if the file were edited."""
    out = new_progress()
    if not isinstance(raw, dict):
        return out
    known_lessons = {x.id for x in cur.lessons} if cur else None
    lessons = raw.get("lessons_complete")
    if isinstance(lessons, list):
        out["lessons_complete"] = [
            x
            for x in dict.fromkeys(lessons)
            if isinstance(x, str)
            and _ID_RE.match(x)
            and (known_lessons is None or x in known_lessons)
        ]
    quiz = raw.get("quiz")
    if isinstance(quiz, dict):
        out["quiz"] = {k: _count(quiz.get(k)) for k in ("attempts", "best_correct", "last_correct")}
    pitch = raw.get("mock_pitch")
    if isinstance(pitch, dict):
        for sid, numbers in pitch.items():
            if not (isinstance(sid, str) and _ID_RE.match(sid) and isinstance(numbers, list)):
                continue
            if cur is not None and sid not in cur.scenarios:
                continue
            total = cur.scenarios[sid] if cur is not None else 99
            kept = sorted({n for n in numbers if _count(n) and 1 <= n <= total})
            out["mock_pitch"][sid] = kept
    tone = raw.get("tone")
    if isinstance(tone, dict):
        out["tone"] = {k: _count(tone.get(k)) for k in ("checks", "last_misses", "total_misses")}
    return out


def dumps(progress: dict[str, Any]) -> str:
    return json.dumps(clean_progress(progress), sort_keys=True, separators=(",", ":"))


def read_progress(
    address: str, cur: Curriculum | None = None, *, config: Any = None
) -> dict[str, Any] | None:
    """``address``'s progress record, cleaned, or None when they have none."""
    from shared import record_store

    try:
        text = record_store.code_read(PROGRESS_STORE, address, PROGRESS_RECORD, config=config)
    except record_store.RecordStoreError:
        return None
    try:
        return clean_progress(json.loads(text), cur)
    except ValueError:
        return clean_progress(None, cur)


def write_progress(address: str, progress: dict[str, Any], *, config: Any = None) -> None:
    from shared import record_store

    try:
        record_store.code_write(
            PROGRESS_STORE, address, PROGRESS_RECORD, dumps(progress), overwrite=True, config=config
        )
    except record_store.RecordStoreError as exc:
        raise TrainingError(str(exc)) from exc


def start_progress(address: str, *, config: Any = None) -> bool:
    """Create ``address``'s progress record if they have none. True when one
    exists afterwards. Used at enrollment, so the record is there from the
    first day and the weekly report shows the person before they write."""
    from shared import record_store

    try:
        record_store.code_write(
            PROGRESS_STORE,
            address,
            PROGRESS_RECORD,
            dumps(new_progress()),
            overwrite=False,
            config=config,
        )
        return True
    except record_store.PartitionRefused:
        return False
    except record_store.RecordStoreError:
        # Already there (overwrite refused) is the ordinary second enrollment.
        return read_progress(address, config=config) is not None


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def next_lesson(cur: Curriculum, progress: dict[str, Any]) -> Lesson | None:
    done = set(progress["lessons_complete"])
    return next((x for x in cur.lessons if x.id not in done), None)


def best_scenario(cur: Curriculum, progress: dict[str, Any]) -> tuple[str, int, int]:
    """``(scenario id, answered, total)`` of the scenario furthest along, or
    ``("", 0, 0)`` when none is started."""
    started = [
        (sid, len(numbers), cur.scenarios[sid])
        for sid, numbers in sorted(progress["mock_pitch"].items())
        if numbers and cur.scenarios.get(sid)
    ]
    if not started:
        return ("", 0, 0)
    return max(started, key=lambda s: (s[1] == s[2], s[1] / s[2], s[1]))


def mock_pitch_done(cur: Curriculum, progress: dict[str, Any]) -> bool:
    sid, answered, total = best_scenario(cur, progress)
    return bool(sid) and answered == total


def requirement_met(cur: Curriculum, progress: dict[str, Any], requires: str) -> bool:
    if requires == "tone_check":
        return progress["tone"]["checks"] >= 1
    if requires == "mock_pitch":
        return mock_pitch_done(cur, progress)
    return True


_REQUIREMENT_WORDS = {
    "tone_check": "a tone check of a draft (the tone_check tool on an email that carries the draft)",
    "mock_pitch": "a whole mock pitch answered, every objection of one buyer scenario",
}


def complete_lesson(cur: Curriculum, progress: dict[str, Any], lesson_id: str) -> tuple[bool, str]:
    """Mark ``lesson_id`` complete when it is the next lesson and its
    requirement is met. Returns ``(changed, sentence)``."""
    lesson = cur.lesson(lesson_id) if isinstance(lesson_id, str) else None
    if lesson is None:
        return False, "There is no lesson with that id; use the id the status gives."
    if lesson.id in progress["lessons_complete"]:
        return False, f"Lesson {lesson.id} is already complete."
    upcoming = next_lesson(cur, progress)
    if upcoming is None or upcoming.id != lesson.id:
        return False, (
            f"Lessons are taken in order; the next lesson is {upcoming.id}, {upcoming.title}."
            if upcoming
            else "Every lesson is already complete."
        )
    if not requirement_met(cur, progress, lesson.requires):
        return (
            False,
            f"Lesson {lesson.id} is complete once there is {_REQUIREMENT_WORDS[lesson.requires]}.",
        )
    progress["lessons_complete"].append(lesson.id)
    return True, f"Lesson {lesson.id} is complete."


#: One numbered answer anywhere in the text: an optional "q"/"question", the
#: number, then the letter. With an explicit separator (``.`` ``)`` ``:`` ``-``)
#: the letter must stand alone (not the start of a word): "1-B", "2. c",
#: "Q3: A", "4) D". With only spaces between, the letter must also end the
#: answer (end of line, a comma or semicolon, a full stop, or the next number),
#: so the prose word "a" in "2 a lot of thought" is never answer A.
_NUMBERED = re.compile(
    r"(?<![A-Za-z0-9])(?:q(?:uestion)?\s*)?(\d{1,2})"
    r"(?:\s*[.):\-]\s*([A-Da-d])(?![A-Za-z0-9'])"
    r"|[ \t]+([A-Da-d])(?=[ \t]*(?:$|[,;.]|\d)))",
    re.IGNORECASE | re.MULTILINE,
)
#: A line holding only standalone letters A to D and separators.
_LETTERS_LINE = re.compile(r"^[\s,;./]*[A-Da-d](?:[\s,;./]+[A-Da-d])*[\s,;./]*$")


def _numbered(text: str, total: int) -> dict[int, str]:
    found: dict[int, str] = {}
    for m in _NUMBERED.finditer(text):
        n = int(m.group(1))
        letter = m.group(2) or m.group(3)
        if 1 <= n <= total and letter:
            found[n] = letter.upper()  # the last answer for a number wins
    return dict(sorted(found.items()))


def _letters_only(text: str, total: int) -> dict[int, str]:
    """The first run of consecutive letters-only lines carrying exactly
    ``total`` letters, in question order; any other line (a greeting, a
    sign-off) ends a run and is ignored."""
    run: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.strip() and _LETTERS_LINE.match(line):
            run.extend(re.findall(r"[A-Da-d]", line))
            continue
        if len(run) == total:
            return {i + 1: x.upper() for i, x in enumerate(run)}
        run = []
    return {}


def parse_answers(text: str, total: int) -> dict[int, str]:
    """Question number -> letter, parsed from the representative's own words.

    Two shapes. Numbered answers anywhere in the text, one per line or many on
    one line ("1-B 2-C", "1. B, 2. C", "Q3: A"); the last answer for a number
    wins. Or, when there are no numbered answers, the letters alone in question
    order ("b, c, a, d ...") on one line or consecutive lines, exactly
    ``total`` of them, whatever else the email says around them."""
    text = text or ""
    return _numbered(text, total) or _letters_only(text, total)


@dataclass(frozen=True)
class QuizResult:
    answered: int
    correct: int
    total: int
    passed: bool
    missed: tuple[Question, ...]
    #: Question numbers this email did not answer. A quiz is graded only when
    #: every question is answered; otherwise nothing is recorded.
    missing: tuple[int, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing


def grade_quiz(cur: Curriculum, progress: dict[str, Any], reply_text: str) -> QuizResult | None:
    """Grade the letters in ``reply_text`` and record the attempt. None when no
    answer could be parsed; an incomplete result (``missing``) when some
    questions were not answered. Neither records anything."""
    total = len(cur.questions)
    answers = parse_answers(reply_text, total)
    if not answers:
        return None
    missing = tuple(n for n in range(1, total + 1) if n not in answers)
    if missing:
        return QuizResult(len(answers), 0, total, False, (), missing)
    missed = tuple(q for i, q in enumerate(cur.questions, start=1) if answers[i] != q.answer)
    correct = total - len(missed)
    quiz = progress["quiz"]
    quiz["attempts"] += 1
    quiz["last_correct"] = correct
    quiz["best_correct"] = max(quiz["best_correct"], correct)
    return QuizResult(total, correct, total, correct >= cur.pass_correct, missed)


def missing_sentence(missing: tuple[int, ...]) -> str:
    """The fixed reply when a quiz email does not answer every question."""
    numbers = ", ".join(str(n) for n in missing)
    which = "question" if len(missing) == 1 else "questions"
    return (
        f"This email does not answer {which} {numbers}, so the quiz was not graded and "
        "nothing was recorded. Please send every answer, one per question: the number, "
        "then the letter."
    )


def record_objection(
    cur: Curriculum, progress: dict[str, Any], scenario: str, number: int, reply_text: str
) -> tuple[bool, str]:
    """Record that objection ``number`` of ``scenario`` was answered, when the
    email that opened this turn carries an answer and it is the next one."""
    total = cur.scenarios.get(scenario) if isinstance(scenario, str) else None
    if not total:
        return False, "There is no buyer scenario with that id."
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= total:
        return False, f"Scenario {scenario} has objections 1 to {total}."
    answered = progress["mock_pitch"].setdefault(scenario, [])
    if number in answered:
        return False, f"Objection {number} of scenario {scenario} is already recorded as answered."
    expected = len(answered) + 1
    if number != expected:
        return (
            False,
            f"Objections are answered in order; the next one for scenario {scenario} is {expected}.",
        )
    if len(re.findall(r"[A-Za-z]{2,}", reply_text or "")) < MIN_ANSWER_WORDS:
        return (
            False,
            "This email carries no answer to record; the answer is the representative's own reply.",
        )
    answered.append(number)
    answered.sort()
    return True, f"Objection {number} of {total} in scenario {scenario} is recorded as answered."


def record_tone(progress: dict[str, Any], misses: list[ToneMiss]) -> int:
    count = sum(m.count for m in misses)
    tone = progress["tone"]
    tone["checks"] += 1
    tone["last_misses"] = count
    tone["total_misses"] += count
    return count


@dataclass(frozen=True)
class Criterion:
    id: str
    met: bool
    value: str


def readiness(cur: Curriculum, progress: dict[str, Any]) -> tuple[bool, tuple[Criterion, ...]]:
    """Ready when all four code-checked facts hold. Nothing else is an input."""
    done = len([x for x in cur.lessons if x.id in progress["lessons_complete"]])
    quiz = progress["quiz"]
    sid, answered, total = best_scenario(cur, progress)
    tone = progress["tone"]
    criteria = (
        Criterion(
            "lessons", done == len(cur.lessons), f"{done} of {len(cur.lessons)} lessons complete"
        ),
        Criterion(
            "quiz",
            quiz["best_correct"] >= cur.pass_correct,
            (
                f"best quiz result {quiz['best_correct']} of {len(cur.questions)} correct, "
                f"{cur.pass_correct} needed, {quiz['attempts']} "
                f"{'attempt' if quiz['attempts'] == 1 else 'attempts'}"
                if quiz["attempts"]
                else "quiz not taken"
            ),
        ),
        Criterion(
            "mock_pitch",
            bool(sid) and answered == total,
            f"mock pitch {answered} of {total} objections answered"
            if sid
            else "mock pitch not started",
        ),
        Criterion(
            "tone",
            tone["checks"] >= 1 and tone["last_misses"] == 0,
            (
                f"last tone check {tone['last_misses']} misses"
                if tone["checks"]
                else "no tone check yet"
            ),
        ),
    )
    return all(c.met for c in criteria), criteria


# ---------------------------------------------------------------------------
# The person of this turn
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnPerson:
    address: str
    reply_text: str


def turn_person(session_id: Any) -> TurnPerson | None:
    """The verified sender of the email that opened this turn, when they are on
    the seat's roster now, with their own words. None otherwise (see module
    docstring, WHO)."""
    from shared import record_store
    from shared.cron_attribution import parse_cron_session
    from shared.inbound import SESSION_INBOUND_ORIGIN

    sid = session_id if isinstance(session_id, str) else ""
    if not sid or parse_cron_session(sid):
        return None
    origin = SESSION_INBOUND_ORIGIN.bound_this_turn(sid)
    address = ((origin.sender_address if origin is not None else "") or "").strip().lower()
    if not address or not record_store.listed_on_seat(address):
        return None
    return TurnPerson(address, getattr(origin, "reply_text", "") or "")


__all__ = [
    "Criterion",
    "Curriculum",
    "Lesson",
    "NO_PERSON",
    "PROGRESS_RECORD",
    "PROGRESS_STORE",
    "Question",
    "QuizResult",
    "SKILL",
    "ToneMiss",
    "ToneRule",
    "TrainingError",
    "TurnPerson",
    "authored",
    "best_scenario",
    "clean_progress",
    "complete_lesson",
    "grade_quiz",
    "load_curriculum",
    "load_tone_rules",
    "missing_sentence",
    "new_progress",
    "next_lesson",
    "parse_answers",
    "read_progress",
    "readiness",
    "record_objection",
    "record_tone",
    "skill_dir",
    "start_progress",
    "tone_misses",
    "turn_person",
    "write_progress",
]
