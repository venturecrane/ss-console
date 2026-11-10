"""The weekly progress report, and the reply checks over every training text (ss-console #3946).

THE REPORT is rendered by code (``shared.training_report``) from the progress
store only, and written as a pre-rendered envelope for the platform to send to
the seat's administrators. These tests run its real ``run`` against a real
``customer.yaml``, the real enrolled list and real record stores on a temp
volume, with a note and a lead carrying a marker in the same person's walled
stores: the marker can never reach the report.

THE REPLY CHECKS. Every line a representative or an administrator can be shown
from this slice (each lesson title and exercise, each quiz question and choice,
each tone fix, each drill, the buyer types, the discovery page, the prospect-fit
library page, every sentence the tools return, the welcome, and a rendered
report with realistic values) is run through the checks the seat runs on a
reply: the fabrication and content gate (``hermes-smd-reply/relay.gate_body``),
the output checklist on the staff-send surface, and the identifier gate with an
EMPTY provenance register (so a date, a clock time or an amount the text
carries is unverified). A planted line with a date, an amount, a clock time and
an exclamation mark is shown held by the same checks, so a pass means something.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from shared import identifier_filter, output_checklist, record_store, training, training_report
from shared.person_prefs import person_slug
from tests.conftest import load_plugin

REPO_SKILLS = Path(__file__).resolve().parents[2] / "skills"
TRAINING_DIR = REPO_SKILLS / "rep-training"
PRACTICE_DIR = REPO_SKILLS / "pitch-practice"
LIBRARY = REPO_SKILLS / "product-guide" / "references"

A = "rep-a@example.test"
B = "rep-b@example.test"
ADMIN = "admin@example.test"
A_MARK = "CANARY-A-7f3k9q"


def _seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, admins: list[str] | None = None) -> Path:
    volume = tmp_path / "opt-data"
    volume.mkdir(exist_ok=True)
    monkeypatch.setattr(record_store, "VOLUME_ROOT", volume)
    monkeypatch.setattr(training, "_IMAGE_SKILL_ROOTS", (str(REPO_SKILLS),))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    doc = {
        "customer_id": "guide-test",
        "seat": {"kind": "internal", "product": "operator", "role": "guide"},
        "sender_enrollment": {"enabled": True},
        "scope": {"inbound_allow_from": [ADMIN], "admins": [ADMIN] if admins is None else admins},
        "personas": [{"slug": "guide", "skills": [{"name": "rep-training", "enabled": True}]}],
        "record_stores": [
            {"name": "rep-notes", "path": f"{volume}/rep/notes", "partition": "sender"},
            {"name": "rep-leads", "path": f"{volume}/rep/leads", "partition": "sender"},
            {"name": "rep-progress", "path": f"{volume}/rep/progress", "partition": "sender", "managed": True},
        ],
    }
    path = tmp_path / "customer.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    return volume


def _enroll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *people: tuple[str, str | None]) -> None:
    path = tmp_path / "enrolled.json"
    entries = [
        {"address": a, "label": "t", "code_id": "c_1", "enrolled_at": "x", **({"name": n} if n else {})}
        for a, n in people
    ]
    path.write_text(json.dumps({"schema": 1, "enrolled": entries}), encoding="utf-8")
    monkeypatch.setenv("SMD_SENDER_ENROLLMENT_ENROLLED", str(path))


def _cur() -> training.Curriculum:
    return training.load_curriculum(TRAINING_DIR, PRACTICE_DIR)


def _ready_progress(cur: training.Curriculum) -> dict[str, Any]:
    p = training.new_progress()
    p["lessons_complete"] = [x.id for x in cur.lessons]
    training.grade_quiz(cur, p, "\n".join(f"{i} {q.answer}" for i, q in enumerate(cur.questions, 1)))
    for n in range(1, cur.scenarios["owner"] + 1):
        training.record_objection(cur, p, "owner", n, "a real answer in several words")
    training.record_tone(p, [])
    return p


def _envelope(tmp_path: Path) -> dict | None:
    path = tmp_path / "home" / ".smd" / "pre_run" / "rep-training.dispatch.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def test_the_report_is_rendered_from_progress_and_no_note_or_lead_reaches_it(tmp_path, monkeypatch) -> None:
    _seat(tmp_path, monkeypatch)
    _enroll(tmp_path, monkeypatch, (A, "Avery Example"), (B, None))
    cur = _cur()
    for store in ("rep-notes", "rep-leads"):
        record_store.code_write(store, A, "acme.md", f"Acme, met the CFO. {A_MARK}")
    training.write_progress(A, _ready_progress(cur))
    verdict = training_report.run(clock=lambda: datetime(2026, 11, 16, 15, tzinfo=timezone.utc))
    assert verdict == {
        "wakeAgent": True,
        "dispatch_expected": True,
        "status": "report_ready",
        "people": 2,
        "ready": 1,
        "dispatch_count": 1,
    }
    envelope = _envelope(tmp_path)
    assert envelope is not None and envelope["skill"] == "rep-training"
    [dispatch] = envelope["dispatches"]
    assert dispatch["recipients"] == [ADMIN]
    body = dispatch["full_body"]
    assert "Avery Example (rep-a@example.test)" in body and "Readiness: ready" in body
    assert "rep-b@example.test\n- No progress recorded yet." in body
    assert A_MARK not in json.dumps(envelope) and "Acme" not in json.dumps(envelope)


def test_the_report_module_opens_no_store_but_the_progress_store() -> None:
    """Structural: the report reaches records only through training.read_progress,
    which names PROGRESS_STORE. No other store name, and no record-store call,
    appears in the report module."""
    import ast

    src = (Path(training_report.__file__)).read_text(encoding="utf-8")
    names = {
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(ast.parse(src))
        if isinstance(n, (ast.Name, ast.Attribute))
    }
    imported = {
        a.name
        for n in ast.walk(ast.parse(src))
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert "record_store" not in names | imported
    assert not {"code_read", "code_list", "code_write"} & names
    assert re.findall(r"training\.(read_progress|load_curriculum|readiness)", src)
    read = Path(training.__file__).read_text(encoding="utf-8")
    calls = re.findall(r"record_store\.code_(?:read|list|write)\(\s*([A-Z_a-z]+)", read)
    assert calls and set(calls) == {"PROGRESS_STORE"}, calls


def test_nothing_is_written_or_woken_when_nobody_is_enrolled_or_no_administrator(tmp_path, monkeypatch) -> None:
    _seat(tmp_path, monkeypatch)
    _enroll(tmp_path, monkeypatch)
    assert training_report.run() == {"wakeAgent": False, "status": "nobody_enrolled", "people": 0}
    assert _envelope(tmp_path) is None
    _seat(tmp_path, monkeypatch, admins=[])
    _enroll(tmp_path, monkeypatch, (A, None))
    assert training_report.run() == {"wakeAgent": False, "status": "no_administrator"}
    assert _envelope(tmp_path) is None


def test_the_pre_run_prints_one_verdict_and_wakes_nothing_on_a_crash(tmp_path, monkeypatch, capsys) -> None:
    _seat(tmp_path, monkeypatch)
    _enroll(tmp_path, monkeypatch, (A, None))
    monkeypatch.setattr(training, "_IMAGE_SKILL_ROOTS", (str(tmp_path / "nowhere"),))
    assert training_report.main() == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"status": "report_crashed", "wakeAgent": False}


# ---------------------------------------------------------------------------
# the reply checks over every training text
# ---------------------------------------------------------------------------


def _relay() -> Any:
    load_plugin("hermes-smd-reply")
    return sys.modules["plugin_hermes_smd_reply.relay"]


def _held(text: str) -> list[str]:
    """Why the seat's reply checks would hold ``text``; empty means it goes."""
    why: list[str] = []
    gate = _relay().gate_body(text, vertical=None, cohort=None, internal_recipient=True)
    if not gate.allowed:
        why.append(gate.reason)
    found = output_checklist.check(text, output_checklist.STAFF_SEND, output_checklist.PROFILE_PARALEGAL)
    why += [v.rule for v in output_checklist.refusing(found)]
    unverified = identifier_filter.unverified_identifiers(text, identifier_filter.ProvenanceRegister())
    why += [f"identifier:{h.kind.value}" for h in unverified if h.kind != identifier_filter.IdKind.NAME]
    return why


def test_the_checks_hold_a_planted_line() -> None:
    planted = "Your quiz is due Nov 12 at 9:30, and the program costs $500!"
    assert _held(planted), "the checks must be able to hold a line"
    assert _held("Plain sentence about the lesson.") == []


def _md_lines(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("-0123456789. ").strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _authored_texts() -> list[tuple[str, str]]:
    cur = _cur()
    texts: list[tuple[str, str]] = []
    for lesson in cur.lessons:
        texts += [(f"{lesson.id} title", f"Lesson 1 of 10: {lesson.title}"), (f"{lesson.id} exercise", lesson.exercise)]
    for i, q in enumerate(cur.questions, 1):
        texts.append((f"{q.id} ask", f"{i}. {q.ask}"))
        texts += [(f"{q.id} {k}", f"{k}. {v}") for k, v in q.choices]
    for rule in training.load_tone_rules(TRAINING_DIR):
        texts.append((f"tone {rule.id}", rule.fix))
    drills = yaml.safe_load((PRACTICE_DIR / "references" / "drills.yaml").read_text())
    for theme in drills["themes"]:
        texts.append((f"drill theme {theme['id']}", f"Drill (theme: {theme['title']}, drill 1 of 3)"))
        for d in theme["drills"]:
            texts.append((f"drill {theme['id']} says", d["says"]))
            texts += [(f"drill {theme['id']} covers", c) for c in d["covers"]]
    scenarios = yaml.safe_load((PRACTICE_DIR / "references" / "scenarios.yaml").read_text())
    for s in scenarios["scenarios"]:
        texts += [(f"scenario {s['id']}", o["says"]) for o in s["objections"]]
    for path in (
        PRACTICE_DIR / "references" / "buyer-types.md",
        TRAINING_DIR / "references" / "discovery.md",
        LIBRARY / "who-smd-sells-to.md",
    ):
        texts += [(path.name, line) for line in _md_lines(path)]
    return texts


def test_every_authored_training_line_clears_the_reply_checks() -> None:
    held = {label: why for label, text in _authored_texts() if (why := _held(text))}
    assert held == {}


def test_every_sentence_the_tools_and_the_welcome_return_clears_the_reply_checks(tmp_path, monkeypatch) -> None:
    from shared import sender_enrollment as se

    plugin = load_plugin("hermes-smd-training")
    cur = _cur()
    p = training.new_progress()
    sentences = [training.NO_PERSON]
    sentences.append(training.complete_lesson(cur, p, "l02")[1])
    sentences.append(training.complete_lesson(cur, p, "l99")[1])
    for n in range(1, 9):
        sentences.append(training.complete_lesson(cur, p, f"l0{n}")[1])
    sentences.append(training.complete_lesson(cur, p, "l09")[1])
    sentences.append(training.record_objection(cur, p, "owner", 3, "x")[1])
    sentences.append(training.record_objection(cur, p, "owner", 1, "ok")[1])
    sentences.append(training.record_objection(cur, p, "nobody", 1, "x")[1])
    sentences.append(training.record_objection(cur, p, "owner", 1, "a real answer in several words")[1])
    sentences += [c.value for c in training.readiness(cur, p)[1]]
    sentences += [c.value for c in training.readiness(cur, _ready_progress(cur))[1]]
    status = plugin._status(cur, p)
    sentences += [json.dumps(status["next_lesson"]["read"])]
    sentences.append(se.welcome_text("Avery Example", training=True))
    for name, (description, _schema) in plugin.TOOLS.items():
        sentences.append(description)
    held = {s[:60]: why for s in sentences if (why := _held(s))}
    assert held == {}


@pytest.mark.parametrize("name", ["Avery Example", "Jo O'Neil-Smith", None])
def test_a_rendered_report_with_realistic_values_clears_the_reply_checks(name) -> None:
    cur = _cur()
    half = _ready_progress(cur)
    half["lessons_complete"] = half["lessons_complete"][:4]
    half["quiz"] = {"attempts": 3, "best_correct": 7, "last_correct": 6}
    half["tone"] = {"checks": 12, "last_misses": 3, "total_misses": 41}
    label = f"{name} (rep-a@example.test)" if name else "rep-a@example.test"
    body = training_report.render(
        cur, [(label, _ready_progress(cur)), ("rep-b@example.test", half), ("rep-c@example.test", None)]
    )
    assert _held(body) == [] and _held(training_report.SKELETON) == []
    assert "1 ready" in body and "3 people enrolled" in body


def test_the_partition_slug_is_the_one_the_purge_uses(tmp_path, monkeypatch) -> None:
    volume = _seat(tmp_path, monkeypatch)
    _enroll(tmp_path, monkeypatch, (A, None))
    training.start_progress(A)
    assert (volume / "rep" / "progress" / person_slug(A) / "progress.json").is_file()
