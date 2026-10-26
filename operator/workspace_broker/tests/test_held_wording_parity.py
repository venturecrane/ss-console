"""The broker's held wording and the skills' held wording are the same words.

The broker refuses any held reply that is not ``held_wording``'s text, and the
model drafts from the skill. If the two drifted, the model would draft the
skill's sentence and the broker would refuse it on every held job. So every
wording in the registry, slots unfilled (``<number>``, ``<name> <why>``,
``<file name>``), must appear word for word in the skill's DELIVER held step
and in its reply-shapes reference; and each ``<why>`` phrase in the skill.

Adding a class: one entry in ``held_wording.WORDING`` and its sentence in the
skill's held step (plus the reply-shapes mirror ``tests/hold-class-wording``
already requires).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import held_wording, hold_class

SKILLS = Path(__file__).resolve().parents[2] / "skills"
# The chronology has no held wording since 2026-10-24 (a held chronology is
# refused its reply), so only litigation is pinned here.
FILES = {
    "litigation": SKILLS / "litigation-status",
    "casefile": SKILLS / "case-file-manager",
}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _held_step(body: str) -> str:
    start = body.index("- **held**:")
    rest = body[start:]
    end = re.search(r"\n {3}- In every message|\n[45]\. \*\*Stop\.\*\*", rest)
    return _flat(rest[: end.start()] if end else rest)


@pytest.mark.parametrize(("lane", "cls", "report", "wording"), held_wording.templates())
def test_every_held_wording_is_in_its_skill_word_for_word(lane: str, cls: str, report: str, wording: str) -> None:
    skill = _held_step((FILES[lane] / "SKILL.md").read_text(encoding="utf-8"))
    shapes = _flat((FILES[lane] / "references" / "reply-shapes.md").read_text(encoding="utf-8"))
    assert _flat(wording) in skill, f"{lane}/{cls}/{report}: not in SKILL.md's held step"
    assert _flat(wording) in shapes, f"{lane}/{cls}/{report}: not in references/reply-shapes.md"


@pytest.mark.parametrize("why", sorted(held_wording.STAFF_WHY.values()))
def test_every_staff_why_is_in_the_litigation_skill(why: str) -> None:
    assert why in _held_step((FILES["litigation"] / "SKILL.md").read_text(encoding="utf-8"))


def test_every_class_resolves_to_wording_on_every_report() -> None:
    """A class with no entry falls back to smd_finishing; nothing raises."""
    reports = {"litigation": ("status", "deadlines", "reassign"), "casefile": ("full", "partial", "apply")}
    for lane, names in reports.items():
        for cls in hold_class.CLASSES:
            for report in names:
                assert held_wording._wordings(lane, cls, report)


@pytest.mark.parametrize("lane", sorted(FILES))
def test_each_skill_says_the_broker_enforces_it(lane: str) -> None:
    held = _held_step((FILES[lane] / "SKILL.md").read_text(encoding="utf-8"))
    assert "The broker enforces this" in held and "`held_wording`" in held


def _casefile_row(**over):
    return {"state": "held", "reason": "all_matters_held: x", "mode": "full", **over}


def test_a_casefile_hold_is_worded_by_what_is_true_of_the_job() -> None:
    """A review that filed nothing, one that filed File Statuses before it held,
    and an apply each get their own words; none says "nothing new" falsely."""
    full = held_wording.allowed("casefile_job", _casefile_row())
    filed = held_wording.allowed(
        "casefile_job",
        _casefile_row(delivery_json='{"files": [{"role": "file_status", "name": "x", "size": 1}]}'),
    )
    apply = held_wording.allowed("casefile_job", _casefile_row(mode="apply", reason="readback_mismatch: 1"))
    assert "nothing new has been put in the firm's files" in full[0]
    assert "nothing new" not in filed[0] and "some files have a new File Status" in filed[0]
    assert "approved changes" in apply[0] and "review" not in apply[0]


def test_a_held_casefile_reply_in_other_words_is_refused() -> None:
    row = _casefile_row()
    with pytest.raises(held_wording.HeldWordingRefused):
        held_wording.check("casefile_job", row, "Your review stopped, want me to run it again?", "")
    held_wording.check("casefile_job", row, held_wording.allowed("casefile_job", row)[0], "")
