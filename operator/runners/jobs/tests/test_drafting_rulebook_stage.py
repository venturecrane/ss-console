"""The firm-rules stage end to end on a revision (``drafting/rulebook/stage.py``),
with invented rules, an invented book and the model's answers stood in by
stage: what code writes, what it leaves and names, and every hold.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobs import firm_wording
from jobs.drafting.outcome import DraftingFailed
from jobs.drafting.rulebook import grid, hook, makers, reading, stage, written
from jobs.drafting.rulebook.types import Instruction, StageDoc, StageJob

from test_drafting_rulebook import BOOK_ROWS, MD

RULES = [
    {"rule_id": "r1", "doc_kinds": ["discovery_response"],
     "text": "Anytime a request asks for photographs, add the privacy objection."},
    {"rule_id": "r2", "doc_kinds": ["discovery_response"],
     "text": "When a request asks for bills, state that the responding party will not respond further."},
    {"rule_id": "r3", "doc_kinds": ["discovery_response"], "text": "Response #9: add the cumulative objection."},
]  # fmt: skip
READ = {
    "rules": [
        {"rule": "r1", "clauses": [{"quote": "add the privacy objection", "standing": True, "act": "add_objection",
                                    "doc_types": ["rfp_response"], "keys": ["privacy"], "grounds": ["privacy"]}]},
        {"rule": "r2", "clauses": [{"quote": "state that the responding party will not respond further",
                                    "standing": True, "act": "use_phrase", "doc_types": ["rfp_response"],
                                    "words": "the responding party will not respond further", "kind": "statement"}]},
        {"rule": "r3", "clauses": [{"quote": "add the cumulative objection", "standing": True, "act": "add_objection",
                                    "doc_types": ["rfp_response"], "grounds": ["cumulative"]}]},
    ]
}  # fmt: skip
GRID = {
    "items": [
        {"item": "1", "marks": "YN", "cells": {"1": {"state": "apply", "keys": ["privacy"]}}},
        {"item": "2", "marks": "NY", "cells": {"2": {"state": "apply", "keys": ["r2#1"],
                                                     "sentences": ["Responding Party will produce the bills he has."]}}},
    ]
}  # fmt: skip
PRIV = "Responding Party objects to this request on the ground that it invades the privacy of Responding Party."
LEAD = "Responding Party objects to this request on the ground that it"


def _firm(stages=("revise",)) -> SimpleNamespace:
    return SimpleNamespace(
        data={"phrase_book": {"path": "phrase-book.json", "stages": list(stages)}},
        read=lambda path: json.dumps(BOOK_ROWS),
        model=lambda role: f"invented-{role}",
    )


class Model:
    """The model's answers by stage; each call recorded."""

    def __init__(self, **answers) -> None:
        self.answers = {reading.STAGE: READ, grid.STAGE: GRID, **answers}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, stage_name, system, user, max_tokens):
        self.calls.append((stage_name, user))
        got = self.answers[stage_name]
        return got if isinstance(got, str) else json.dumps(got)

    def stages(self) -> list[str]:
        return [s for s, _ in self.calls]


def _job(tmp_path: Path, ask, rules=RULES, mode="revise") -> StageJob:
    tmp_path.mkdir(parents=True, exist_ok=True)
    if rules is not None:
        (tmp_path / firm_wording.FILE).write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return StageJob(data=tmp_path, firm=_firm(), ask=ask, log=print, request_text="invented", mode=mode)


def _doc(**kw) -> StageDoc:
    return StageDoc(key="00", md=MD, doc_type="requests_for_production", hint="requests_for_production", **kw)


def test_code_writes_the_firms_words_where_the_grid_says_and_names_each_in_the_notes(tmp_path):
    model = Model()
    res = stage.run(_job(tmp_path, model), _doc())
    assert model.stages() == [reading.STAGE, grid.STAGE]
    assert "[3]" not in model.calls[1][1]  # the one-matter clause is never judged
    assert f"overbroad. {PRIV} Without waiving said objections" in res.md
    assert "The responding party will not respond further." in res.md
    assert "the bills he has" not in res.md
    assert [(a.source, a.item) for a in res.applied] == [("rule:r1", "1"), ("rule:r2", "2")]
    assert res.notes == (
        "Response 1: the firm's objection added, by rule r1.",
        "Response 2: the firm's wording used, by rule r2.",
    )
    kept = json.loads((tmp_path / stage.DIR / "00.json").read_text())
    assert kept["notes"] == list(res.notes)
    again = Model()
    assert stage.run(_job(tmp_path, again), _doc()).md == res.md and again.calls == []  # cached: $0


def test_a_ground_with_no_book_sentence_is_written_for_the_request_or_left(tmp_path):
    rules = [{"rule_id": "r9", "text": "On every production response add the cumulative objection."}]
    read = {"rules": [{"rule": "r9", "clauses": [{"quote": "add the cumulative objection", "standing": True,
                                                  "act": "add_objection", "doc_types": ["rfp_response"],
                                                  "grounds": ["cumulative"], "when": "always"}]}]}  # fmt: skip
    ok = {"sentences": [{"item": n, "ground": "cumulative", "sentence": f"{LEAD} is cumulative."} for n in ("1", "2")]}
    model = Model(**{reading.STAGE: read, written.STAGE: ok})
    res = stage.run(_job(tmp_path / "a", model, rules), _doc())
    assert model.stages() == [reading.STAGE, written.STAGE]  # an always clause needs no grid
    assert res.md.count(f"{LEAD} is cumulative.") == 2
    assert res.notes[0].startswith("Response 1: an objection was written for this request, by rule r9")
    refused = Model(**{reading.STAGE: read, written.STAGE: {"sentences": []}})
    res = stage.run(_job(tmp_path / "b", refused, rules), _doc())
    assert res.md == MD and [(x.item, x.reason) for x in res.left] == [("1", "phrase_missing"), ("2", "phrase_missing")]
    assert res.notes[0].startswith("Response 1: left as drafted; the firm's standard wording")


PROD_RULES = [{"rule_id": "r5", "text": "For bills, produce them under the exhibit they are in."}]
PROD_READ = {"rules": [{"rule": "r5", "clauses": [{"quote": "produce them under the exhibit", "standing": True,
                                                   "act": "use_phrase", "doc_types": ["rfp_response"],
                                                   "keys": ["prod"]}]}]}  # fmt: skip


def _prod_grid(title: str) -> dict:
    return {"items": [{"item": "1", "marks": "N"},
                      {"item": "2", "marks": "Y", "cells": {"1": {"state": "apply", "keys": ["prod"], "exhibits": [title],
                                                                  "sentences": ["Responding Party will produce the bills he has."]}}}]}  # fmt: skip


def test_code_fills_the_exhibit_letter_from_the_index_by_title(tmp_path):
    model = Model(**{reading.STAGE: PROD_READ, grid.STAGE: _prod_grid("Medical Bills")})
    res = stage.run(_job(tmp_path, model, PROD_RULES), _doc())
    assert "which can be found at Exhibit B." in res.md and res.applied[0].item == "2"


def test_an_exhibit_the_index_cannot_name_is_left_and_named(tmp_path):
    model = Model(**{reading.STAGE: PROD_READ, grid.STAGE: _prod_grid("Invented Title")})
    res = stage.run(_job(tmp_path, model, PROD_RULES), _doc())
    assert res.md == MD and [(x.item, x.reason) for x in res.left] == [("2", makers.EXHIBIT)]
    assert res.notes == ("Response 2: the exhibit it names could not be matched to a file, so it is left as drafted",)


def test_a_conflict_holds_the_document(tmp_path):
    g = {"items": [{"item": "1", "marks": "YN", "cells": {"1": {"state": "conflict"}}}, {"item": "2", "marks": "NN"}]}
    with pytest.raises(DraftingFailed, match="^rule_unmet: .*would contradict clause r1#1"):
        stage.run(_job(tmp_path, Model(**{grid.STAGE: g})), _doc())


def test_a_refused_grid_or_rule_read_holds_unchecked(tmp_path):
    with pytest.raises(DraftingFailed, match="^rule_unchecked: the rule grid"):
        stage.run(_job(tmp_path / "g", Model(**{grid.STAGE: "{}"})), _doc())
    with pytest.raises(DraftingFailed, match="^rule_unchecked: the firm's rules could not be read"):
        stage.run(_job(tmp_path / "r", Model(**{reading.STAGE: "{}"})), _doc())


def test_a_made_sentence_the_recheck_cannot_find_holds_unmet(tmp_path, monkeypatch):
    monkeypatch.setattr(makers, "add", lambda blocks, own, sentence, book, names=(): (list(blocks), ""))
    with pytest.raises(DraftingFailed, match="^rule_unmet: document 00: response 1: missing"):
        stage.run(_job(tmp_path, Model()), _doc())


@pytest.mark.parametrize("mode", ["first_draft", "revise"])
def test_a_job_with_no_rules_is_unchanged_and_asks_nothing(tmp_path, mode):
    model = Model()
    assert stage.run(_job(tmp_path, model, None, mode), _doc()).md == MD and model.calls == []


def test_a_document_that_answers_no_numbered_responses_is_unchanged(tmp_path):
    model = Model()
    doc = StageDoc(key="00", md="Invented prose.", doc_type="requests_for_production")
    assert stage.run(_job(tmp_path, model), doc).md == "Invented prose." and model.calls == []


def test_an_instruction_applies_to_its_numbers_only_with_no_grid(tmp_path):
    ins = Instruction("email", "for No. 2 add the privacy objection", ("2",), "requests_for_production",
                      "add_objection", ("privacy",))  # fmt: skip
    model = Model()
    res = stage.run(_job(tmp_path, model, rules=[]), _doc(instructions=(ins,)))
    assert model.calls == []
    assert [(a.source, a.item) for a in res.applied] == [("email", "2")]
    assert res.md.count(PRIV) == 1


def test_an_instruction_removing_an_objection_by_key(tmp_path):
    ins = Instruction("email", "for No. 1 take out overbroad", ("1",), "requests_for_production",
                      "remove_objection", ("overbroad",))  # fmt: skip
    res = stage.run(_job(tmp_path, Model(), rules=[]), _doc(instructions=(ins,)))
    assert "is overbroad" not in res.md and res.notes == ("Response 1: a sentence removed, as the request asks.",)


def test_an_email_objection_the_book_has_no_sentence_for_is_written_for_its_number_only(tmp_path):
    """An emailed ground with no book entry goes through the written path (checked), never a refusal."""
    ins = Instruction("email", "for No. 2 add a cumulative objection", ("2",), "requests_for_production",
                      "add_objection", (), ("cumulative",))  # fmt: skip
    ok = {"sentences": [{"item": "2", "ground": "cumulative", "sentence": f"{LEAD} is cumulative."}]}
    model = Model(**{written.STAGE: ok})
    res = stage.run(_job(tmp_path / "a", model, rules=[]), _doc(instructions=(ins,)))
    assert model.stages() == [written.STAGE]
    assert res.md.count(f"{LEAD} is cumulative.") == 1
    assert res.notes[0].startswith("Response 2: an objection was written for this request")
    bad = {"sentences": [{"item": "2", "ground": "cumulative", "sentence": f"{LEAD} is cumulative of 14 visits."}]}
    res = stage.run(_job(tmp_path / "b", Model(**{written.STAGE: bad}), rules=[]), _doc(instructions=(ins,)))
    assert res.md == MD and [(x.item, x.reason) for x in res.left] == [("2", "phrase_missing")]


def test_a_hyphenated_ground_is_named_by_its_words_either_way():
    """FALSIFIER: a substring test refuses "attorney-client privilege" for the ground "attorney client privilege"."""
    need = written.Need("2", "attorney client privilege", "email", "invented request", "add privilege")
    book = stage.book_mod.Book({})
    assert written.check_one(f"{LEAD} is protected by the attorney-client privilege.", need, LEAD, book) == ""
    need = written.Need("2", "attorney-client privilege", "email", "invented request", "add privilege")
    assert written.check_one(f"{LEAD} is protected by the attorney client privilege.", need, LEAD, book) == ""


def test_the_estimate_counts_the_read_and_each_grid_before_any_call(tmp_path):
    job = _job(tmp_path, None)
    assert stage.plan_chars(job, [_doc()]) > len(MD)
    assert stage.plan_chars(_job(tmp_path / "none", None, None), [_doc()]) == 0


def test_the_revise_hook_asks_through_the_runs_own_doorway(tmp_path):
    model = Model()
    (tmp_path / firm_wording.FILE).write_text(json.dumps({"rules": RULES}), encoding="utf-8")
    run = SimpleNamespace(
        firm=_firm(), log=print, cls="discovery_response", data=tmp_path,
        job=SimpleNamespace(request_text="invented"), _ask=lambda model_name: model,
    )  # fmt: skip
    out = hook.at_revise(run, 0, MD, "requests_for_production")
    assert PRIV in out and model.stages() == [reading.STAGE, grid.STAGE]
    assert hook.at_revise(SimpleNamespace(**{**vars(run), "firm": _firm(("first_draft",))}), 0, MD, None) == MD
