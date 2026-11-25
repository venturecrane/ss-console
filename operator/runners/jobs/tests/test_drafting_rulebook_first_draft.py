"""The firm-rules stage on first drafts (``rulebook.hook.at_gate``): responses
made by code as on a revision, the notes joining the attorney notes; a
propounded set checked only, its findings kept for the set lane. Invented values.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from jobs import firm_wording
from jobs.drafting import render
from jobs.drafting.outcome import DraftingFailed
from jobs.drafting.rulebook import grid, hook, reading, sets_check, stage
from jobs.drafting.rulebook.types import StageDoc, StageJob

from test_drafting_rulebook import BOOK_ROWS, MD
from test_drafting_rulebook_stage import PRIV, RULES, Model


def _firm(stages=("first_draft",)) -> SimpleNamespace:
    return SimpleNamespace(
        data={"phrase_book": {"path": "phrase-book.json", "stages": list(stages)}},
        read=lambda path: json.dumps(BOOK_ROWS),
        model=lambda role: f"invented-{role}",
    )


def _run(tmp_path, model, rules=RULES, stages=("first_draft",)) -> SimpleNamespace:
    (tmp_path / firm_wording.FILE).write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return SimpleNamespace(
        firm=_firm(stages), log=print, cls="discovery_response", data=tmp_path,
        job=SimpleNamespace(request_text="invented"), _ask=lambda model_name: model,
    )  # fmt: skip


def _unit(tmp_path, key="set-1", variant="requests_for_production") -> SimpleNamespace:
    return SimpleNamespace(key=key, variant=variant, data=tmp_path / "unit")


DRAFTED = MD + "\n" + render.NOTES_MARK + "\n\n- Invented note from the draft.\n"


def test_a_first_draft_gets_the_firms_words_and_the_notes_join_the_attorney_notes(tmp_path):
    model = Model()
    out = hook.at_gate(_run(tmp_path, model), _unit(tmp_path), DRAFTED)
    body, notes = render.split_notes(out)
    assert PRIV in body and "The responding party will not respond further." in body
    assert notes.splitlines() == [
        "- Invented note from the draft.",
        "- Response 1: the firm's objection added, by rule r1.",
        "- Response 2: the firm's wording used, by rule r2.",
    ]
    assert model.stages() == [reading.STAGE, grid.STAGE]
    assert json.loads((tmp_path / stage.DIR / "set-1.json").read_text())["applied"]


def test_off_for_first_drafts_the_gate_hook_changes_nothing(tmp_path):
    model = Model()
    assert hook.at_gate(_run(tmp_path, model, stages=("revise",)), _unit(tmp_path), DRAFTED) == DRAFTED
    assert model.calls == []


def test_a_first_draft_with_nothing_to_change_comes_back_byte_for_byte(tmp_path):
    g = {"items": [{"item": "1", "marks": "NN"}, {"item": "2", "marks": "NN"}]}
    assert hook.at_gate(_run(tmp_path, Model(**{grid.STAGE: g})), _unit(tmp_path), DRAFTED) == DRAFTED


# ---- propounded sets: findings only ----------------------------------------------------
SET_MD = (
    "\n\n".join(
        ["PLAINTIFF'S SPECIAL INTERROGATORIES, SET ONE"]
        + [f"SPECIAL INTERROGATORY NO. {n}:\n\nInvented question {n}." for n in range(1, 4)]
        + ["SPECIAL INTERROGATORY NO. 4:\n\nState your invented social media handles."]
    )
    + "\n"
)
SET_RULES = [
    {"rule_id": "s1", "doc_kinds": ["discovery_set"], "text": "Special interrogatories should generally be 50-75."},
    {"rule_id": "s2", "doc_kinds": ["discovery_set"], "text": "Never ask the defendant about social media."},
]  # fmt: skip
SET_READ = {"rules": [
    {"rule": "s1", "clauses": [{"quote": "should generally be 50-75", "standing": True, "act": "count",
                                "doc_types": ["srog_set"]}]},
    {"rule": "s2", "clauses": [{"quote": "never ask the defendant about social media", "standing": True,
                                "act": "forbid_topic", "doc_types": ["srog_set"]}]},
]}  # fmt: skip


def _set_job(tmp_path, model) -> StageJob:
    (tmp_path / firm_wording.FILE).write_text(json.dumps({"rules": SET_RULES}), encoding="utf-8")
    return StageJob(data=tmp_path, firm=_firm(), ask=model, log=print, request_text="invented", mode="first_draft")


def test_a_propounded_set_is_checked_never_written(tmp_path):
    model = Model(**{reading.STAGE: SET_READ, sets_check.STAGE: {"clauses": [{"clause": 1, "numbers": ["4"]}]}})
    doc = StageDoc(key="srog-1", md=SET_MD, doc_type="special_interrogatories", hint="special_interrogatories")
    res = stage.run(_set_job(tmp_path, model), doc)
    assert res.md == SET_MD and not res.applied
    assert [(f.source, f.code, f.item) for f in res.findings] == [
        ("rule:s1", "count_short", ""),
        ("rule:s2", "forbid_topic", "4"),
    ]
    assert res.findings[0].detail == "4 requests; the rule asks at least 50"
    kept = json.loads((tmp_path / stage.DIR / "srog-1.json").read_text())
    assert [f["code"] for f in kept["findings"]] == ["count_short", "forbid_topic"]


@pytest.mark.parametrize(
    "answer",
    [{}, {"clauses": [{"clause": 1, "numbers": ["99"]}]}, {"clauses": []}, {"clauses": [{"clause": 7, "numbers": []}]}],
)
def test_a_refused_topic_answer_holds_unchecked(tmp_path, answer):
    model = Model(**{reading.STAGE: SET_READ, sets_check.STAGE: answer})
    doc = StageDoc(key="srog-1", md=SET_MD, doc_type="special_interrogatories")
    with pytest.raises(DraftingFailed, match="^rule_unchecked: the set's topic rules were refused"):
        stage.run(_set_job(tmp_path, model), doc)
    assert model.stages().count(sets_check.STAGE) == 2


@pytest.mark.parametrize(
    "quote, want",
    [
        ("generally 50-75", (50, 75)),
        ("between 35 to 50", (35, 50)),
        ("up to 50", (None, 50)),
        ("at least 35", (35, None)),
        ("keep it reasonable", None),
    ],
)
def test_a_counts_figures_come_from_the_rules_own_words(quote, want):
    assert sets_check.figures(quote) == want


def test_a_set_over_the_count_and_a_count_with_no_figure():
    from jobs.drafting.rulebook.types import Clause

    over = Clause("s1", 1, "up to 3", True, ("srog_set",), "count")
    vague = Clause("s3", 1, "keep it reasonable", True, ("srog_set",), "count")
    res = sets_check.run(__import__("pathlib").Path("/nonexistent"), SET_MD, [over, vague], None)
    assert [(f.code, f.detail) for f in res.findings] == [("count_over", "4 requests; the rule allows at most 3")]
    assert res.notes == ("Rule s3: its count names no figure code can read; the set was not checked against it.",)


def test_two_units_through_the_gate_at_once_read_the_rules_once_and_keep_both(tmp_path):
    model = Model()

    def slow(stage_name, system, user, max_tokens):
        time.sleep(0.05)  # the first unit's read is still in flight when the second arrives
        return model(stage_name, system, user, max_tokens)

    run = _run(tmp_path, model)
    run._ask = lambda model_name: slow
    out: dict[str, str] = {}

    def one(key: str) -> None:
        out[key] = hook.at_gate(run, _unit(tmp_path, key=key), DRAFTED)

    threads = [threading.Thread(target=one, args=(k,)) for k in ("set-1", "set-2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert model.stages().count(reading.STAGE) == 1
    assert len(out) == 2 and all(PRIV in md for md in out.values())
    assert json.loads((tmp_path / reading.FILE).read_text())  # whole, never torn
    for key in ("set-1", "set-2"):
        assert json.loads((tmp_path / stage.DIR / f"{key}.json").read_text())["applied"]
    assert not list(tmp_path.rglob("*.tmp"))
