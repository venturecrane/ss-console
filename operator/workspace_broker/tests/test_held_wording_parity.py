"""The broker's held wording and the skills' held wording are the same words,
and every DELIVER section works with the switch on or off.

The broker refuses any held reply that is not ``fixed_wording``'s text, and the
model drafts from the skill. If the two drifted, the model would draft the
skill's sentence and the broker would refuse it on every held job. So every
wording in the registry, slots unfilled (``<number>``, ``<name> <why>``,
``<file name>``), must appear word for word in the skill's DELIVER held step
and in its reply-shapes reference; and each ``<why>`` phrase in the skill.

Adding a class: one entry in ``fixed_wording.WORDING`` and its sentence in the
skill's held step (plus the reply-shapes mirror ``tests/hold-class-wording``
already requires).
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import fixed_wording, hold_class

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


@pytest.mark.parametrize(("lane", "cls", "report", "wording"), fixed_wording.templates())
def test_every_held_wording_is_in_its_skill_word_for_word(lane: str, cls: str, report: str, wording: str) -> None:
    skill = _held_step((FILES[lane] / "SKILL.md").read_text(encoding="utf-8"))
    shapes = _flat((FILES[lane] / "references" / "reply-shapes.md").read_text(encoding="utf-8"))
    assert _flat(wording) in skill, f"{lane}/{cls}/{report}: not in SKILL.md's held step"
    assert _flat(wording) in shapes, f"{lane}/{cls}/{report}: not in references/reply-shapes.md"


@pytest.mark.parametrize("why", sorted(fixed_wording.STAFF_WHY.values()))
def test_every_staff_why_is_in_the_litigation_skill(why: str) -> None:
    assert why in _held_step((FILES["litigation"] / "SKILL.md").read_text(encoding="utf-8"))


def test_every_class_resolves_to_wording_on_every_report() -> None:
    """A class with no entry falls back to smd_finishing; nothing raises."""
    reports = {"litigation": ("status", "deadlines", "reassign"), "casefile": ("full", "partial", "apply")}
    for lane, names in reports.items():
        for cls in hold_class.CLASSES:
            for report in names:
                assert fixed_wording._wordings(lane, cls, report)


@pytest.mark.parametrize("lane", sorted(FILES))
def test_each_skill_says_the_broker_enforces_it(lane: str) -> None:
    held = _held_step((FILES[lane] / "SKILL.md").read_text(encoding="utf-8"))
    assert "The broker enforces this" in held and "`held_wording`" in held


def _casefile_row(**over):
    return {"state": "held", "reason": "all_matters_held: x", "mode": "full", **over}


def test_a_casefile_hold_is_worded_by_what_is_true_of_the_job() -> None:
    """A review that filed nothing, one that filed File Statuses before it held,
    and an apply each get their own words; none says "nothing new" falsely."""
    full = fixed_wording.allowed("casefile_job", _casefile_row())
    filed = fixed_wording.allowed(
        "casefile_job",
        _casefile_row(delivery_json='{"files": [{"role": "file_status", "name": "x", "size": 1}]}'),
    )
    apply = fixed_wording.allowed("casefile_job", _casefile_row(mode="apply", reason="readback_mismatch: 1"))
    assert "nothing new has been put in the firm's files" in full[0]
    assert "nothing new" not in filed[0] and "some files have a new File Status" in filed[0]
    assert "approved changes" in apply[0] and "review" not in apply[0]


def test_a_held_casefile_reply_in_other_words_is_refused() -> None:
    row = _casefile_row()
    with pytest.raises(fixed_wording.HeldWordingRefused):
        fixed_wording.check("casefile_job", row, "Your review stopped, want me to run it again?", "")
    fixed_wording.check("casefile_job", row, fixed_wording.allowed("casefile_job", row)[0], "")


# -- every DELIVER section works in both modes (ADR 0104) --------------------------
#
# The skills artifact and a seat's switch move apart, so a section must read right
# with the switch either way. When the broker words the reply, the model sends those
# words (the first branch). When it does not, the model composes by the rules it has
# always had (the second), so a seat in observe behaves as it does today. The block
# names no rendered sentence, on purpose: the model reads the skill, and a sentence
# printed here could change what it writes on a seat that was handed none.

BROKER_WORDED_BLOCK = (
    "**When the broker words this reply.** If `reply_bind` returned `fixed_wording`, or the status read "
    "returned `reply_text` or `reply_texts`, the reply's whole body is that text, word for word (one of "
    "them, when there are several): no greeting, no sign-off, no sentence of your own, no changed word. "
    "The broker enforces this: the send refuses any other words, sends nothing, keeps the one reply "
    "unspent and quotes the text to send. Who hears, when to send nothing and when to stop stay as this "
    "section says; only its composing rules give way. If none of those keys came back, the broker has not "
    "worded this reply: compose it exactly as the rest of this section says."
    "\n\n"
)
DELIVER_SKILLS = {
    "litigation": SKILLS / "litigation-status",
    "casefile": SKILLS / "case-file-manager",
    "medchron": SKILLS / "medical-chronology-maintainer",
    "negotiation": SKILLS / "negotiation-watch",
    "note": SKILLS / "smd-note",
}
#: Lanes whose DELIVER section does not carry the block yet. The drafting and demand
#: skills belong to the document-library work and join when it finishes; until then
#: no seat may run fixed_wording enforce for these lanes. Each must still have a
#: DELIVER section, and must not carry the block half-added.
PENDING_SKILLS = {
    "demand": SKILLS / "demand-letter-drafter",
    "drafting": SKILLS / "document-drafter",
}
#: sha256 of each DELIVER section with the block taken out: main's composing rules
#: as they stood when the block went in. A failure here means a composing rule
#: changed. That is a deliberate edit to main's rules: if it is one, re-pin the hash
#: in the same PR and say so; if it is not, put the words back.
COMPOSE_BRANCH_SHA256 = {
    "litigation": "77e75cb0372115bd391c63e4e42984a57c2f520fb18beaea790a9b46aa7622eb",
    "casefile": "394180288917b54a773207e6bcec49ccc08de22ec3cac6d80f35eb8713f62712",
    "medchron": "3048e3b4e03af59439ec820de46eeca2e04c2f3b8c335e8eec861e50f431c27d",
    "negotiation": "fcd27092664680450b0e6a76b3ac4ee238a88d8153194050a2eab98c0e64c72f",
    "note": "32dfa6398ec8b9711726699d8fba69e8c47e34dd61cb33f13dc05622e916f764",
}


def _deliver_raw(lane: str) -> str:
    skill = DELIVER_SKILLS.get(lane) or PENDING_SKILLS[lane]
    body = (skill / "SKILL.md").read_text(encoding="utf-8")
    start = body.index("## DELIVER")
    end = body.find("\n## ", start + 1)
    return body[start : end if end != -1 else len(body)]


def test_every_rendered_lane_has_a_skill() -> None:
    assert set(fixed_wording.KIND_LANE.values()) == set(DELIVER_SKILLS) | set(PENDING_SKILLS)
    assert set(DELIVER_SKILLS) == set(COMPOSE_BRANCH_SHA256)
    assert not set(DELIVER_SKILLS) & set(PENDING_SKILLS)


@pytest.mark.parametrize("lane", sorted(PENDING_SKILLS))
def test_a_pending_lane_has_a_deliver_section_and_no_block_yet(lane: str) -> None:
    section = _deliver_raw(lane)
    assert section.startswith("## DELIVER")
    assert "When the broker words this reply" not in section


@pytest.mark.parametrize("lane", sorted(DELIVER_SKILLS))
def test_the_worded_branch_comes_first_in_every_deliver_section(lane: str) -> None:
    """Before any composing rule, once, and naming every key the broker sends."""
    section = _deliver_raw(lane)
    heading, _, rest = section.partition("\n\n")
    assert heading.startswith("## DELIVER")
    assert rest.startswith(BROKER_WORDED_BLOCK) and section.count(BROKER_WORDED_BLOCK) == 1
    for key in ("`fixed_wording`", "`reply_text`", "`reply_texts`"):
        assert key in BROKER_WORDED_BLOCK


@pytest.mark.parametrize("lane", sorted(DELIVER_SKILLS))
def test_the_compose_branch_is_mains_text_unchanged(lane: str) -> None:
    compose = _deliver_raw(lane).replace(BROKER_WORDED_BLOCK, "", 1)
    assert hashlib.sha256(compose.encode()).hexdigest() == COMPOSE_BRANCH_SHA256[lane], lane


def test_no_delivered_sentence_carries_a_slot_the_model_could_fill() -> None:
    """A delivered sentence has fields code fills, never a ``<slot>``."""
    for lane, key, sentence in fixed_wording.delivered_templates():
        assert "<" not in sentence and "\u2014" not in sentence, f"{lane}/{key}"
