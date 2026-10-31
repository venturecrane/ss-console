"""The guide rides the ``operator_seat_facts`` help result (ss-console#3423).

OBSERVED (pilot-smokeball, image rel-1c955bc7): a rostered non-admin asked
"Should I prompt the operator the same way I prompt Claude ... will it know
what to pull". The turn called ``operator_seat_facts`` then ``create_draft``;
it never read ``operator-help/SKILL.md`` or ``references/guide.md``, and the
reply missed the guide's shape entirely. These tests pin the structural fix:
the help section itself carries the guide body, the tips, the ordered
``answer_shape`` and the compacted ``answer_rules``, so composing a guide-shaped
answer no longer depends on the model choosing to read a file.

Every check names the state it must FAIL on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import load_plugin
from tests.test_seat_facts import (
    _ADMIN,
    _STAFF,
    _FakeConfig,
    _FakeManifest,
    _help_raw,
    _help_roots,
    _skill_md,
    _write_jobs,
)

#: A sentence that lives ONLY in the Notes block of the fixture guide. If it
#: ever appears in the result, the Notes were shipped to the reader's composer.
_NOTES_SENTINEL = "NOTES-SENTINEL never quoted to the reader"
_BODY_SENTINEL = "Prompt it the same way you prompt Claude."

_FIXTURE_GUIDE = f"""# How to work with your Operator (SMD-authored guide)

## Notes for the Operator (never quoted, never mentioned to the reader)

- {_NOTES_SENTINEL}.
- Matter numbers in examples stay [number].

## The guide

{_BODY_SENTINEL} The more specific you are, the better the result.

### Two kinds of requests

1. **Work it has been trained on.** "On matter [number], draft the demand."
2. **One-off work.** "On matter [number], draft special interrogatories."
"""

_FIXTURE_TIPS = """tips:
  - id: vague_request
    when: vague_request
    text: >-
      Name the matter and what you want.
  - id: wants_changes
    when: wants_changes
    text: >-
      Reply in the same thread with what to change.
"""

#: The real guide, when this overlay is checked out inside ss-console.
_REAL_HELP = Path(__file__).resolve().parents[2] / "skills" / "operator-help"


@pytest.fixture
def sf():
    return load_plugin("hermes-smd-initiation").seat_facts


@pytest.fixture
def hg():
    return load_plugin("hermes-smd-initiation").help_guide


def _write_help_skill(root: Path, guide: str | None = _FIXTURE_GUIDE, tips: str | None = None):
    _skill_md(root, "operator-help", None)
    refs = root / "operator-help" / "references"
    refs.mkdir(parents=True, exist_ok=True)
    if guide is not None:
        (refs / "guide.md").write_text(guide, encoding="utf-8")
    (refs / "tips.yaml").write_text(tips if tips is not None else _FIXTURE_TIPS, encoding="utf-8")


def _bound_raw() -> dict:
    raw = _help_raw()
    raw["personas"][0]["skills"].append(
        {"name": "operator-help", "enabled": True, "initiation": {"manual": True}}
    )
    return raw


def _help(sf, tmp_path, *, raw=None, roots=None, sender=_STAFF):
    _write_jobs(tmp_path, [])
    if roots is None:
        roots = _help_roots(tmp_path)
        _write_help_skill(roots[1])
    cfg = _FakeConfig(raw if raw is not None else _bound_raw())
    return sf.build_facts(
        depth="help",
        config_loader=lambda: cfg,
        spec_manifest_module=_FakeManifest(),
        home=str(tmp_path),
        sender=sender,
        skill_roots_override=roots,
    )


def test_help_section_carries_the_guide_body_and_the_tips(sf, tmp_path):
    """FALSIFIER: drop guide_fields from _help_section and the guide is again a
    file the model must choose to read, which is the observed failure."""
    help_ = _help(sf, tmp_path)["help"]
    assert help_["read"] is True
    assert isinstance(help_["guide"], str)
    assert help_["guide"].startswith(_BODY_SENTINEL)
    assert "### Two kinds of requests" in help_["guide"]
    assert [t["id"] for t in help_["tips"]] == ["vague_request", "wants_changes"]
    assert help_["tips"][0] == {
        "id": "vague_request",
        "when": "vague_request",
        "text": "Name the matter and what you want.",
    }


def test_the_notes_block_never_reaches_the_result(sf, tmp_path):
    """The Notes are instructions to the model, never words for the reader; they
    reach it compacted as answer_rules. FALSIFIER: return the whole file."""
    facts = _help(sf, tmp_path)
    blob = json.dumps(facts)
    assert _NOTES_SENTINEL not in blob
    assert "Notes for the Operator" not in blob
    assert facts["help"]["answer_rules"], "the Notes must arrive as answer_rules"


def test_notes_placed_after_the_guide_heading_are_still_cut(hg):
    text = f"## The guide\n\nBody line.\n\n## Notes for the Operator\n\n- {_NOTES_SENTINEL}\n"
    assert hg.guide_body(text) == "Body line."


def test_answer_shape_is_present_and_ordered(sf, tmp_path):
    shape = _help(sf, tmp_path)["help"]["answer_shape"]
    parts = [p["part"] for p in shape]
    assert parts == [
        "direct_answer",
        "how_it_works",
        "trained_work",
        "one_off_example",
        "voice",
        "keep_going",
        "when_to_train",
        "close",
    ]
    trained = next(p["say"] for p in shape if p["part"] == "trained_work")
    assert "sender_may_ask true" in trained
    assert "name none" in trained
    for p in shape:
        assert "—" not in p["say"], p["part"]


def test_answer_rules_forbid_self_description_by_negation(sf, tmp_path):
    """The observed reply said "I'm not a general-purpose prompt assistant"."""
    rules = " ".join(_help(sf, tmp_path)["help"]["answer_rules"])
    assert "never by what you are not" in rules
    assert "[number]" in rules
    assert "Never promise a time" in rules
    assert "—" not in rules


def test_an_unreadable_guide_is_an_entry_and_the_requests_still_report(sf, tmp_path):
    """FALSIFIER: let the guide read raise out of _help_section and a missing
    guide blanks the requests too ("I could not read the guide" becomes
    "you may ask for nothing")."""
    roots = _help_roots(tmp_path)
    _write_help_skill(roots[1], guide=None)
    facts = _help(sf, tmp_path, roots=roots)
    help_ = facts["help"]
    assert help_["read"] is True
    assert help_["guide"] is None
    assert help_["tips"], "the tips still read"
    assert {r["skill"] for r in help_["requests"]} == {
        "demand-letter-drafter",
        "file-work-requests",
    }
    assert help_["answer_shape"], "the shape is authored here, never unreadable"
    reasons = [u["reason"] for u in facts["unreadable"] if u["section"] == "help"]
    assert any(r.startswith("operator-help guide unreadable") for r in reasons), reasons


def test_a_guide_with_no_body_heading_or_over_the_ceiling_is_unreadable(sf, hg, tmp_path):
    roots = _help_roots(tmp_path)
    _write_help_skill(roots[1], guide="# Title only\n\nNo body heading.\n")
    facts = _help(sf, tmp_path, roots=roots)
    assert facts["help"]["guide"] is None
    assert any("guide unreadable" in u["reason"] for u in facts["unreadable"])

    big = "## The guide\n\n" + ("word " * (hg.GUIDE_CEILING_CHARS // 4))
    with pytest.raises(ValueError, match="exceeds"):
        hg.guide_body(big)


def test_malformed_tips_are_an_entry_and_the_guide_still_reads(sf, tmp_path):
    roots = _help_roots(tmp_path)
    _write_help_skill(roots[1], tips="tips:\n  - id: x\n")
    facts = _help(sf, tmp_path, roots=roots)
    assert facts["help"]["guide"].startswith(_BODY_SENTINEL)
    assert facts["help"]["tips"] == []
    assert any("tips unreadable" in u["reason"] for u in facts["unreadable"])


def test_the_volume_copy_of_the_guide_wins(sf, tmp_path):
    roots = _help_roots(tmp_path)
    _write_help_skill(roots[1])
    _write_help_skill(roots[0], guide="## The guide\n\nVolume words.\n")
    assert _help(sf, tmp_path, roots=roots)["help"]["guide"] == "Volume words."


def test_a_seat_that_does_not_enable_operator_help_gets_no_guide_and_no_fault(sf, tmp_path):
    """The skill fence's rule: a skill the seat does not enable is not read.
    That is a fact about the seat, not a failed read. Nor does it get the answer
    shape or rules (ADR 0100): they are the skill's wording.
    FALSIFIER: fill them before the bound check and this seat gets both."""
    facts = _help(sf, tmp_path, raw=_help_raw())
    assert facts["help"]["guide"] is None
    assert facts["help"]["tips"] == []
    assert facts["help"]["answer_shape"] == []
    assert facts["help"]["answer_rules"] == []
    assert not any("operator-help" in u["reason"] for u in facts["unreadable"])


def test_other_depths_are_unchanged(sf, tmp_path):
    _write_jobs(tmp_path, [])
    cfg = _FakeConfig(_bound_raw())
    for depth in ("introduction", "walkthrough"):
        facts = sf.build_facts(
            depth=depth,
            config_loader=lambda: cfg,
            spec_manifest_module=_FakeManifest(),
            home=str(tmp_path),
            sender=_ADMIN,
        )
        assert "help" not in facts
        assert "answer_shape" not in json.dumps(facts)


@pytest.mark.skipif(not _REAL_HELP.is_dir(), reason="overlay checked out outside ss-console")
def test_the_shipped_guide_reads_whole_under_the_ceiling_without_its_notes(hg):
    """The real guide and tips, as a seat reads them. Pins the size so the help
    result stays small enough to ride every help turn."""
    roots = (_REAL_HELP.parent,)
    body = hg.read_guide(roots)
    assert body.startswith("Prompt it the same way you prompt Claude.")
    assert "Notes for the Operator" not in body
    assert "never quoted" not in body
    assert "### When to train it" in body
    assert len(body) <= hg.GUIDE_CEILING_CHARS
    assert len(body) < 4500, len(body)
    tips = hg.read_tips(roots)
    assert {t["id"] for t in tips} >= {"vague_request", "wants_specific_document"}
    assert len(json.dumps({"guide": body, "tips": tips})) < 8000


def test_tool_description_tells_the_model_to_compose_from_answer_shape():
    """The description is the one surface always in front of the model.
    FALSIFIER: drop the answer_shape sentence and a help turn may again answer
    from the requests list alone."""
    plugin = load_plugin("hermes-smd-initiation")
    desc = plugin._SEAT_FACTS_DESCRIPTION
    assert "answer_shape" in desc
    assert "guide" in desc
    assert "answer_rules" in desc
    assert "never by what you are not" in desc
    assert "should I prompt you the way I prompt Claude?" in desc
    depth_doc = plugin._SEAT_FACTS_SCHEMA["properties"]["depth"]["description"]
    assert "answer_shape" in depth_doc


def test_the_shipped_help_text_passes_the_send_checker_even_unquoted(hg):
    """OBSERVED (pilot-smokeball, 2026-10-24): the guide said matter numbers stay
    ``XXXXXX``; the model copied an example out of its quotes, the send checker
    refused the draft as shouting (``caps_emphasis``), and the model deleted it
    and redrafted. Every placeholder a reader may be shown must survive the
    checker with its quotes stripped. FAILS if any guide line, tip or skill
    ``help.example`` carries a four-capital run the checker does not know."""
    import yaml

    from shared import output_checklist as oc

    roots = (_REAL_HELP.parent,)
    texts = [hg.read_guide(roots)] + [t["text"] for t in hg.read_tips(roots)]
    for skill_md in sorted(_REAL_HELP.parent.glob("*/SKILL.md")):
        front = skill_md.read_text(encoding="utf-8").split("---", 2)[1]
        smd = ((yaml.safe_load(front) or {}).get("metadata") or {}).get("smd") or {}
        example = (smd.get("help") or {}).get("example")
        if example:
            texts.append(example)
    assert len(texts) > 5
    for text in texts:
        bare = text.replace('"', "").replace("“", "").replace("”", "")
        rules = {v.rule for v in oc.check(bare, oc.STAFF_SEND)}
        assert "caps_emphasis" not in rules, bare[:200]
