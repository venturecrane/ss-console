"""The library-change email, and the gaps report's replay (guide seat).

THE AUTHORED FILE. ``operator/skills/product-guide/changes.yaml`` is parsed by
the real loader: ids strictly increase, and the NEWEST entry's
``library_sha256`` equals the hash of the library as committed. A planted edit
to a copy of the library is shown to change the hash, so the pin can fail.
Every entry's rendered body, the subject and the skeleton go through the seat's
reply checks (``tests.test_training_report._held``: the relay's fabrication and
content gate, the output checklist, the identifier gate with an empty
provenance register), which are themselves shown to hold a planted line there.

THE REHEARSAL ($0, no model). ``shared.library_notice.run`` against a real
``customer.yaml``, the real enrolled list and a real walled progress store on a
temp volume, with two library versions and three people: the person with no
mark is sent the newest entry once and never twice; after a second entry they
are sent only the new one; a person enrolled after the change (their mark set
by the webhook router's own enrollment step) is sent nothing; a person whose
mark is unknown or unreadable is reset to the newest and sent nothing. Mark
before envelope, restored when the envelope cannot be written; ten a day; a dry
run writes nothing.

THE GAPS REPLAY. A not-covered product turn from a verified sender goes through
the desk plugin's real ``post_llm_call`` hook (``gap_report.note_turn``) on a
seat where the counter does not exist yet; the counter is created and the
weekly report rendered by ``training_report.run`` lists the topic. What the
counter stores is unchanged (keys and counts; ``test_rep_desk`` pins that).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from shared import library_notice, person_digest, prerendered_dispatch, record_store, training
from shared.customer_config import CustomerConfig
from shared.person_prefs import person_slug
from tests.test_rep_desk import desk  # noqa: F401 - the desk seat fixture, reused
from tests.test_training_report import _held

OVERLAY = Path(__file__).resolve().parents[1]
REPO_SKILLS = OVERLAY.parent / "skills"
REPO_GUIDE = REPO_SKILLS / "product-guide"
A = "rep-a@example.test"
B = "rep-b@example.test"
C = "rep-c@example.test"
ADMIN = "admin@example.test"
NOW = datetime(2026, 11, 16, 16, 23, tzinfo=timezone.utc)
LINE_1 = "We now say the first thing."
LINE_2 = "We now say the second thing."


# ---------------------------------------------------------------------------
# the authored file
# ---------------------------------------------------------------------------


def test_the_repo_entries_parse_and_ids_strictly_increase() -> None:
    entries = library_notice.load_changes(REPO_GUIDE)
    ids = [e.id for e in entries]
    assert ids == sorted(set(ids)) and ids[0] >= 1


def test_the_newest_entry_hash_is_the_library_as_committed() -> None:
    """If this fails, references/ changed: add an entry to changes.yaml saying
    what we now say, then run
    ``cd operator/hermes-smd-overlay && uv run python -m shared.library_notice rehash``."""
    newest = library_notice.load_changes(REPO_GUIDE)[-1]
    assert newest.library_sha256 == library_notice.library_sha256(REPO_GUIDE)


def test_a_planted_library_edit_changes_the_hash(tmp_path) -> None:
    copy = tmp_path / "product-guide"
    shutil.copytree(REPO_GUIDE, copy)
    before = library_notice.library_sha256(copy)
    assert before == library_notice.load_changes(copy)[-1].library_sha256
    index = copy / "references" / "INDEX.md"
    index.write_text(index.read_text(encoding="utf-8") + "\nA planted line.\n", encoding="utf-8")
    edited = library_notice.library_sha256(copy)
    assert edited != before
    (copy / "references" / "new-page.md").write_text("x", encoding="utf-8")
    assert library_notice.library_sha256(copy) not in {before, edited}
    # The regeneration command touches only the newest hash, and then agrees.
    old, new = library_notice.rehash(copy)
    assert old == before and new == library_notice.library_sha256(copy)
    assert library_notice.load_changes(copy)[-1].library_sha256 == new


@pytest.mark.parametrize(
    "entries",
    [
        [{"id": 2, "lines": ["x"]}, {"id": 1, "lines": ["y"]}],
        [{"id": 1, "lines": ["x"]}, {"id": 1, "lines": ["y"]}],
        [{"id": True, "lines": ["x"]}],
        [{"id": 0, "lines": ["x"]}],
        [{"id": 1, "lines": []}],
        [{"id": 1, "lines": [" "]}],
        [{"id": 1, "lines": ["x"], "library_sha256": "abc"}],
    ],
)
def test_a_malformed_file_is_refused(entries) -> None:
    for e in entries:
        e.setdefault("library_sha256", "a" * 64)
    with pytest.raises(library_notice.NoticeError):
        library_notice.parse_changes(yaml.safe_dump({"entries": entries}))


def test_every_rendered_entry_clears_the_reply_checks() -> None:
    assert _held("Your quiz is due Nov 12 at 9:30, and the program costs $500!")
    entries = library_notice.load_changes(REPO_GUIDE)
    held = {e.id: why for e in entries if (why := _held(library_notice.render([e])))}
    assert held == {}
    assert _held(library_notice.render(entries)) == []
    assert _held(library_notice.SUBJECT) == [] and _held(library_notice.SKELETON) == []


def test_the_constants_match_the_modules_they_copy() -> None:
    assert library_notice.CHUNK == prerendered_dispatch._MAX_DISPATCHES
    assert library_notice.STORE == training.PROGRESS_STORE
    assert library_notice.SKILL == REPO_GUIDE.name
    assert (REPO_GUIDE / "pre_run.py").is_file()


# ---------------------------------------------------------------------------
# the rehearsal
# ---------------------------------------------------------------------------


def _skill(tmp_path: Path, *lines_by_id: str) -> Path:
    """A product-guide skill dir with one entry per line given (ids 1, 2...)."""
    root = tmp_path / "skills"
    skill = root / "product-guide"
    (skill / "references").mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("# product-guide\n", encoding="utf-8")
    (skill / "references" / "INDEX.md").write_text("v" + str(len(lines_by_id)), encoding="utf-8")
    digest = library_notice.library_sha256(skill)
    doc = {
        "entries": [
            {"id": i, "lines": [line], "library_sha256": digest}
            for i, line in enumerate(lines_by_id, 1)
        ]
    }
    (skill / "changes.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return skill


def _enroll(path: Path, *people: str) -> None:
    entries = [
        {"address": a, "label": "t", "code_id": f"c_{i}", "enrolled_at": "x"}
        for i, a in enumerate(people)
    ]
    path.write_text(json.dumps({"schema": 1, "enrolled": entries}), encoding="utf-8")


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    volume = tmp_path / "opt-data"
    volume.mkdir()
    monkeypatch.setattr(record_store, "VOLUME_ROOT", volume)
    home = volume / "profiles" / "guide"
    home.mkdir(parents=True)
    doc = dedent(
        f"""
        customer_id: guide-test
        seat: {{kind: internal, product: operator, role: guide}}
        sender_enrollment: {{enabled: true}}
        scope:
          inbound_allow_from: [{ADMIN}]
          admins: [{ADMIN}]
        record_stores:
          - {{name: rep-progress, path: {volume}/rep/progress, partition: sender, managed: true}}
        personas:
          - slug: guide
            status: active
            name: Guide
            skills:
              - name: product-guide
                version: pending
                enabled: true
                initiation: {{manual: true, scheduled: true, webhook: true}}
                settings: {{recipients: roster}}
              - name: rep-training
                version: pending
                enabled: true
                initiation: {{manual: true, scheduled: true, webhook: true}}
        """
    )
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(doc, encoding="utf-8")
    enrolled = tmp_path / "enrolled.json"
    _enroll(enrolled, A)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml_path))
    monkeypatch.setenv("SMD_SENDER_ENROLLMENT_ENROLLED", str(enrolled))
    monkeypatch.setenv("HERMES_HOME", str(home))
    return {
        "volume": volume,
        "home": home,
        "enrolled": enrolled,
        "yaml": yaml_path,
        "tmp": tmp_path,
    }


def _cfg() -> CustomerConfig:
    return CustomerConfig.from_volume()


def _envelope(seat: dict) -> dict | None:
    path = seat["home"] / ".smd" / "pre_run" / "product-guide.dispatch.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()  # as the dispatcher consumes it
    return payload


def _mark_path(seat: dict, person: str) -> Path:
    return seat["volume"] / "rep" / "progress" / person_slug(person) / library_notice.MARK_NAME


def _bodies(envelope: dict | None) -> dict[str, str]:
    if envelope is None:
        return {}
    out = {}
    for d in envelope["dispatches"]:
        [person] = d["recipients"]
        assert d["cc"] == [] and d["subject"] == library_notice.SUBJECT
        out[person] = d["full_body"]
    return out


def test_two_versions_three_people_each_sent_once_and_never_twice(seat) -> None:
    v1 = _skill(seat["tmp"] / "v1", LINE_1)
    # A: on the roster, no mark. The admin is on the roster too.
    verdict = library_notice.run(_cfg(), NOW, skill_dir=v1)
    assert verdict["status"] == "notice_ready" and verdict["wakeAgent"] is True
    assert verdict["chunk"] == 2 and verdict["newest"] == 1 and verdict["people"] == 2
    bodies = _bodies(_envelope(seat))
    assert set(bodies) == {A, ADMIN}
    assert f"- {LINE_1}" in bodies[A]
    assert json.loads(_mark_path(seat, A).read_text()) == {"seen": 1}

    # Again: nothing new for anyone, no wake, no envelope.
    verdict = library_notice.run(_cfg(), NOW, skill_dir=v1)
    assert verdict["status"] == "nothing_new" and verdict["wakeAgent"] is False
    assert _envelope(seat) is None

    # The library changes. B enrols now: the router's enrollment step sets
    # their mark to the newest. C enrols with an unknown mark.
    v2 = _skill(seat["tmp"] / "v2", LINE_1, LINE_2)
    _enroll(seat["enrolled"], A, B, C)
    assert library_notice.write_mark(B, 2, _cfg())
    assert library_notice.write_mark(C, 99, _cfg())
    verdict = library_notice.run(_cfg(), NOW, skill_dir=v2)
    assert verdict["status"] == "notice_ready"
    assert verdict["chunk"] == 2 and verdict["reset"] == 1 and verdict["newest"] == 2
    bodies = _bodies(_envelope(seat))
    assert set(bodies) == {A, ADMIN}
    assert f"- {LINE_2}" in bodies[A] and LINE_1 not in bodies[A]
    assert json.loads(_mark_path(seat, C).read_text()) == {"seen": 2}

    # And never twice.
    assert library_notice.run(_cfg(), NOW, skill_dir=v2)["status"] == "nothing_new"
    assert _envelope(seat) is None


def test_a_person_never_sent_one_gets_only_the_newest(seat) -> None:
    v2 = _skill(seat["tmp"] / "v2", LINE_1, LINE_2)
    library_notice.run(_cfg(), NOW, skill_dir=v2)
    body = _bodies(_envelope(seat))[A]
    assert LINE_2 in body and LINE_1 not in body


def test_an_unreadable_mark_is_reset_and_sent_nothing(seat) -> None:
    v1 = _skill(seat["tmp"] / "v1", LINE_1)
    _enroll(seat["enrolled"], B)
    record_store.code_write("rep-progress", B, library_notice.MARK_NAME, "not json", config=_cfg())
    library_notice.run(_cfg(), NOW, skill_dir=v1)
    assert B not in _bodies(_envelope(seat))
    assert json.loads(_mark_path(seat, B).read_text()) == {"seen": 1}


def test_enrollment_sets_the_mark_to_the_newest(seat, monkeypatch) -> None:
    from tests.conftest import load_plugin

    v2 = _skill(seat["tmp"] / "v2", LINE_1, LINE_2)
    monkeypatch.setattr(training, "_IMAGE_SKILL_ROOTS", (str(v2.parent),))
    _enroll(seat["enrolled"], A, B)
    router = load_plugin("hermes-smd-webhook-router")
    assert router._start_training(_cfg(), B) is True
    assert json.loads(_mark_path(seat, B).read_text()) == {"seen": 2}
    assert library_notice.run(_cfg(), NOW, skill_dir=v2)["chunk"] == 2  # A and the admin
    assert B not in _bodies(_envelope(seat))


def test_the_marks_are_restored_when_the_envelope_cannot_be_written(seat, monkeypatch) -> None:
    v1 = _skill(seat["tmp"] / "v1", LINE_1)
    assert library_notice.write_mark(ADMIN, None, _cfg())
    monkeypatch.setattr(library_notice, "write_envelope", lambda payload: False)
    verdict = library_notice.run(_cfg(), NOW, skill_dir=v1)
    assert verdict["status"] == "envelope_write_failed" and verdict["wakeAgent"] is True
    assert not _mark_path(seat, A).exists() and not _mark_path(seat, ADMIN).exists()


def test_ten_a_day_and_the_rest_the_next_day(seat) -> None:
    v1 = _skill(seat["tmp"] / "v1", LINE_1)
    people = [f"rep-{i:02d}@example.test" for i in range(12)]
    _enroll(seat["enrolled"], *people)
    first = library_notice.run(_cfg(), NOW, skill_dir=v1)
    assert first["chunk"] == 10 and first["deferred"] == 3  # 12 reps and the admin
    assert len(_envelope(seat)["dispatches"]) == 10
    second = library_notice.run(_cfg(), NOW, skill_dir=v1)
    assert second["chunk"] == 3 and second["deferred"] == 0


def test_a_dry_run_reports_counts_and_writes_nothing(seat) -> None:
    v1 = _skill(seat["tmp"] / "v1", LINE_1)
    verdict = library_notice.run(_cfg(), NOW, dry=True, skill_dir=v1)
    assert verdict == {
        "wakeAgent": False,
        "dry_run": True,
        "status": "notice_ready",
        "people": 2,
        "due": 2,
        "chunk": 2,
        "deferred": 0,
        "reset": 0,
        "unreadable": 0,
        "newest": 1,
    }
    assert _envelope(seat) is None
    assert not (seat["volume"] / "rep" / "progress").exists()


def test_no_walled_store_means_nothing(seat) -> None:
    v1 = _skill(seat["tmp"] / "v1", LINE_1)
    text = seat["yaml"].read_text(encoding="utf-8").replace(", managed: true", "")
    seat["yaml"].write_text(text, encoding="utf-8")
    assert library_notice.run(_cfg(), NOW, skill_dir=v1) == {
        "wakeAgent": False,
        "status": "store_unauthored",
        "people": 0,
        "due": 0,
        "chunk": 0,
        "deferred": 0,
        "reset": 0,
        "unreadable": 0,
        "newest": 0,
    }
    assert library_notice.mark_newest(_cfg(), A) is False


def test_an_unreadable_changes_file_wakes_with_nothing_to_send(seat) -> None:
    broken = seat["tmp"] / "broken"
    broken.mkdir()
    (broken / "changes.yaml").write_text("entries: [", encoding="utf-8")
    assert library_notice.run(_cfg(), NOW, skill_dir=broken) == {
        "wakeAgent": True,
        "status": "changes_unreadable",
    }
    assert _envelope(seat) is None


def test_the_pre_run_script_dry_run_prints_counts_only(seat) -> None:
    """The staged script, run as the scheduler runs it, in a fresh process,
    with the skill where the seat keeps it (the persona home's skills). The
    fresh process's volume root is the seat's own (a temp store path is
    refused there), so this seat authors no store: the point is one verdict
    line, counts only, nothing written."""
    shutil.copytree(REPO_GUIDE, seat["home"] / "skills" / "product-guide")
    text = seat["yaml"].read_text(encoding="utf-8")
    seat["yaml"].write_text(text.split("record_stores:")[0] + "personas: []\n", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(OVERLAY),
        "HERMES_HOME": str(seat["home"]),
        "SMD_CUSTOMER_YAML_PATH": str(seat["yaml"]),
        "SMD_SENDER_ENROLLMENT_ENROLLED": str(seat["enrolled"]),
        "SMD_LIBRARY_NOTICE_DRY": "1",
    }
    out = subprocess.run(
        [sys.executable, str(REPO_GUIDE / "pre_run.py")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    verdict = json.loads(out)
    assert verdict["wakeAgent"] is False and verdict.get("dry_run") is True
    assert verdict["status"] == "store_unauthored" and "newest" in verdict
    assert A not in out and ADMIN not in out
    assert _envelope(seat) is None


def test_the_roster_is_the_digests(seat) -> None:
    assert person_digest.people(_cfg()) == sorted([A, ADMIN], key=lambda a: person_slug(a))


# ---------------------------------------------------------------------------
# the gaps report replay
# ---------------------------------------------------------------------------


def test_a_not_covered_turn_reaches_the_rendered_weekly_report(
    desk,  # noqa: F811 - the imported desk fixture, requested by name
    tmp_path,
    monkeypatch,
) -> None:
    """A product question the library does not cover, from a verified sender,
    through the desk plugin's real hook, on a seat where the counter has never
    been written; then the weekly report, rendered by its real ``run``."""
    from shared import gap_report, training_report
    from tests import test_rep_desk as rd
    from tests import test_training_report as tr

    db = tmp_path / "gap_counts.db"
    assert not db.exists()
    rd._email_turn(desk, rd.A, rd.SA, "<a-1@example.test>", "Does it integrate with Clio?")
    desk["hooks"]["post_llm_call"][0](
        session_id=rd.SA,
        user_message=rd._prompt("A question", "Does it integrate with Clio?"),
        conversation_history=rd._history([("read_file", rd._INDEX)], rd._NOT),
        assistant_response=rd._NOT,
    )
    assert db.exists()
    assert gap_report.weekly_gap_counts() == {"integrations": 1}

    tr._seat(tmp_path, monkeypatch)
    tr._enroll(tmp_path, monkeypatch, (tr.A, None))
    training_report.run(clock=lambda: datetime.now(timezone.utc))
    envelope = tr._envelope(tmp_path)
    assert envelope is not None
    body = envelope["dispatches"][0]["full_body"]
    assert f"{training_report.GAP_HEADING}\n- Integrations: 1" in body
    assert _held(body) == []
