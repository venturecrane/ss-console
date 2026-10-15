"""Parity F7: what the update review is shown, and a part it cannot finish.

* A pre-operative history delivered weeks before a surgery was twice held as
  "missing" from a re-filed copy of the surgical record: the review saw only
  delivered entries within a week. It now sees, whatever their date, the
  delivered entries that cite the delivered pages the new pages copy, and the
  delivered entries of the same provider.
* One part's review never said it was complete; the stage split it seven
  times and failed the whole job. Now a leaf reply with findings is read, and
  a leaf that still does not parse is held as "not reviewed", never a failure.
* The prompts name paired fields (goal and date, value and unit, side) and
  marks on a form ("marked as shown on the page", never a guessed letter).

Every value is invented; each test fails on the code before F7.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import update_testkit as K
from medchron import prompts
from medchron.stages import update_review as UR
from medchron.update import review_parts as RP, review_repair as RR
from test_update_finisher import N1, _record, _review, _run

COPY = (
    "12/08/2025\nSample Hospital | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Pre-operative history recorded a reducible right inguinal hernia for six months. (Exhibit 2 - p. 1)"
)
PREOP = (
    "10/30/2025\nSample Hospital | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Pre-operative history recorded a reducible right inguinal hernia for six months. (Exhibit 1 - p. 37)"
)
SAME_PROVIDER = (
    "06/01/2025\nSample Hospital | All Other Information\n\nAll Other Information\n"
    "Annual examination recorded no acute findings at the visit. (Exhibit 1 - p. 3)"
)
UNRELATED = (
    "05/01/2025\nOther Clinic | All Other Information\n\nAll Other Information\n"
    "Routine dental cleaning was recorded with no findings at the visit. (Exhibit 1 - p. 9)"
)


def test_the_review_sees_the_delivered_entry_a_copied_page_came_from_whatever_its_date() -> None:
    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + COPY + "\n\n## Exhibit List\n"
    )
    ents = RR.anchor_entries(md, {2}, K.HEADINGS)
    already = {2: {2: "Exhibit 1 p. 37"}}  # the copy's next page is delivered Exhibit 1 p. 37
    text = RP.context(ents, ents, [UNRELATED, PREOP, SAME_PROVIDER], already)
    assert "=== D2 (DELIVERED) ===" in text  # 39 days earlier, found by page identity
    assert "=== D3 (DELIVERED) ===" in text  # months earlier, the same provider
    assert "=== D1 (DELIVERED) ===" not in text  # neither


def test_a_leaf_reply_with_findings_and_no_completeness_line_is_read() -> None:
    reply = json.dumps({"findings": [{"severity": "MUST", "class": "1 wrong value", "anchor": "N1.c1"}]})
    with pytest.raises(RR.ReplyUnparsed):
        RR.parse_reply(reply)
    assert [f.anchor for f in RR.parse_reply(reply, lenient=True)] == ["N1.c1"]
    with pytest.raises(RR.ReplyUnparsed):  # no findings and no completeness line is never "clean"
        RR.parse_reply(json.dumps({"findings": []}), lenient=True)


class _Door:
    """Every review reply lacks the completeness line and has no findings."""

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def call(self, _stage: str, **kw: Any):
        self.seen.append(kw)
        return SimpleNamespace(text=json.dumps({"findings": []}), stop_reason="end_turn", usage=None)


def test_a_part_that_never_says_it_is_complete_is_held_not_reviewed_never_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, ReviewIncomplete failed the stage (exit 1) and the whole job."""
    door = _Door()
    monkeypatch.setattr(K, "ScriptedDoor", lambda *_a: door)
    rc, sr, _md = _run(tmp_path, monkeypatch, [N1], [], [])
    assert rc == 2  # held for SMD, not failed
    open_ = _record(sr)["open"]
    assert open_ and open_[0]["klass"] == UR.NOT_REVIEWED and open_[0]["outcome"].startswith("not reviewed")
    assert any(m.startswith("update_held:") and "did not finish" in m for m in sr.logged)
    assert all(kw.get("system") != "update-finish-system" for kw in door.seen)  # never sent to the finisher


def test_the_prompts_name_paired_fields_and_marks_on_a_form() -> None:
    cfg = K.cfg()
    review, finish = prompts.load("update-review-system", cfg), prompts.load("update-finish-system", cfg)
    assert "PAIRED FIELDS" in review and "a goal with its target date" in review
    assert "MARKS ON A FORM" in review and "marked as shown on the page" in review
    assert "marked as shown on the page" in finish


def test_a_finding_on_a_copied_delivered_visit_is_reviewed_with_that_visit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: page identity on disk puts the delivered pre-operative
    entry, 39 days before the copy's date, into the part the review reads."""
    ud = tmp_path / "matter" / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 2, "id": "n2"}]))
    (ud / "page_identity.json").write_text(json.dumps({"pages": {"n2": {"2": ["already", "exhibit:1", 37, "near"]}}}))
    orig = K.base_md
    monkeypatch.setattr(K, "base_md", lambda: orig((PREOP, K.ENTRY_PRE, K.ENTRY_POST)))
    _rc, sr, _md = _run(tmp_path, monkeypatch, [COPY], [_review()], [])
    first = "\n".join(b.get("text", "") for b in sr.doorway.review_calls[0]["messages"][0]["content"])
    assert "(Exhibit 1 - p. 37)" in first and "Exhibit 2 p. 2" not in first  # the copy page is not shown
