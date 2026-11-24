"""The firm-rules stage's parts (``drafting/rulebook``): the book, the sentence
reader, the rule read, the grid, the makers and the written fallback. Every
value here is invented; every refusal path has a test.
"""

from __future__ import annotations

import json

import pytest

from jobs.drafting.revise import responses as rs
from jobs.drafting.rulebook import book as book_mod, grid, makers, parts, reading, written
from jobs.drafting.rulebook.types import Cell, Clause

BOOK_ROWS = [
    {"key": "overbroad", "kind": "objection", "names": ["overbroad"], "sets": [], "source": "invented",
     "text": "Responding Party objects to this {item} on the ground that it is overbroad."},
    {"key": "privacy", "kind": "objection", "names": ["privacy"], "sets": [], "source": "invented",
     "text": "Responding Party objects to this {item} on the ground that it invades the privacy of Responding Party."},
    {"key": "trans", "kind": "transition", "names": [], "sets": [], "source": "invented",
     "text": "Without waiving said objections, Responding Party responds as follows:"},
    {"key": "prod", "kind": "disposition", "names": ["produce"], "sets": [], "source": "invented",
     "text": "Responding Party will produce all documents, which can be found at Exhibit {exhibit}."},
    {"key": "nodocs", "kind": "disposition", "names": ["no documents"], "sets": [], "source": "invented",
     "text": "Responding Party has no documents responsive to this Request."},
]  # fmt: skip
BOOK = book_mod.parse(json.dumps(BOOK_ROWS))

MD = """PLAINTIFF'S RESPONSES TO REQUESTS FOR PRODUCTION

REQUEST FOR PRODUCTION NO. 1:

All photographs of the scene.

RESPONSE TO REQUEST FOR PRODUCTION NO. 1:

Responding Party objects to this Request on the ground that it is overbroad. Without waiving said objections, \
Responding Party responds as follows: Responding Party will produce all documents, which can be found at Exhibit A.

REQUEST FOR PRODUCTION NO. 2:

All bills from Invented Clinic.

RESPONSE TO REQUEST FOR PRODUCTION NO. 2:

Responding Party will produce the bills he has.

INDEX OF EXHIBITS

EXHIBIT A: PHOTOGRAPHS

EXHIBIT B: MEDICAL BILLS
"""


def says(answer: dict):
    """A model call that always answers ``answer``."""

    def ask(stage: str, system: str, user: str, max_tokens: int) -> str:
        return json.dumps(answer)

    return ask


def _doc() -> rs.Document:
    return rs.read(MD)


def _own(doc: rs.Document | None = None) -> dict[str, tuple]:
    doc = doc or _doc()
    return {r.number: parts.item_parts(doc, r, BOOK) for r in doc.responses}


# ---- book -------------------------------------------------------------------------------
def test_the_book_finds_a_sentence_with_its_slots_filled():
    assert BOOK.find("Responding Party objects to this Request on the ground that it is overbroad") == "overbroad"
    assert BOOK.find("responding party will produce all documents, which can be found at Exhibits A and B.") == "prod"
    assert BOOK.find("Responding Party will produce the bills he has.") is None


def test_the_objection_lead_is_what_every_objection_entry_starts_with():
    assert BOOK.objection_lead() == "Responding Party objects to this {item} on the ground that it"
    one = book_mod.Book({"overbroad": BOOK.entries["overbroad"]})
    assert one.objection_lead() == ""


def test_stated_words_take_the_books_casing_and_a_full_stop():
    assert BOOK.cased("the responding party will not respond further") == (
        "The Responding Party will not respond further."
    )


def test_an_entry_already_in_the_book_keeps_its_words():
    other = book_mod.Entry("prod", "disposition", "Invented other words.")
    assert BOOK.with_entries([other]).entries["prod"].text == BOOK.entries["prod"].text


# ---- parts -------------------------------------------------------------------------------
def test_a_response_is_read_into_sentences_with_roles_keys_and_places():
    got = parts.read(MD, "rfp_response", None, BOOK)
    one = got.items["1"]
    assert [p.role for p in one] == ["objection", "transition", "disposition"]
    assert [p.key for p in one] == ["overbroad", "trans", "prod"]
    assert one[0].ground == "overbroad"
    doc = _doc()
    for p in one:
        assert doc.blocks[p.block][p.at : p.at + len(p.text)] == p.text
    assert [p.role for p in got.items["2"]] == ["other"]


def test_without_a_book_the_lead_ins_still_give_the_roles():
    got = parts.read(MD, "rfp_response")
    assert [p.role for p in got.items["1"]] == ["objection", "transition", "other"]


def test_a_document_that_is_not_responses_reads_as_nothing():
    assert parts.read("Invented prose only.", "rfp_response").items == {}


# ---- the rule read ---------------------------------------------------------------------
RULES = [
    {"rule_id": "r1", "doc_kinds": ["discovery_response"],
     "text": "Anytime a request asks for photographs, add the privacy objection."},
    {"rule_id": "r2", "doc_kinds": ["discovery_response"],
     "text": "When a request asks for tax returns, state that the responding party will not respond further."},
    {"rule_id": "r3", "doc_kinds": ["discovery_response"],
     "text": "Response #9: no coverage claim here. #10: add the cumulative objection."},
]  # fmt: skip


def _clause(**kw) -> dict:
    base = {"quote": "add the privacy objection", "standing": True, "standing_quote": "", "act": "add_objection",
            "doc_types": ["rfp_response"], "keys": ["privacy"], "grounds": ["privacy"], "words": "", "kind": "",
            "when": "judged"}  # fmt: skip
    return {**base, **kw}


def _answer(r1=None, r2=None, r3=None) -> dict:
    return {
        "rules": [
            {"rule": "r1", "clauses": [r1 or _clause()]},
            {"rule": "r2", "clauses": [r2 or _clause(
                quote="state that the responding party will not respond further", act="use_phrase", keys=[],
                grounds=[], words="the responding party will not respond further", kind="statement")]},
            {"rule": "r3", "clauses": [r3 or _clause(quote="add the cumulative objection", keys=[],
                                                     grounds=["cumulative"])]},
        ]
    }  # fmt: skip


def test_a_good_read_is_kept_with_the_rules_own_words_as_an_entry():
    got, problems = reading.check(_answer(), RULES, BOOK)
    assert problems == []
    c1, c2, c3 = (got[r]["clauses"][0] for r in ("r1", "r2", "r3"))
    assert c1.standing and c1.keys == ("privacy",) and c1.grounds == ("privacy",)
    entry = got["r2"]["entries"][0]
    assert entry.key == "r2#1" and c2.keys == ("r2#1",)
    assert entry.text == "The Responding Party will not respond further." and entry.source == "rule:r2"
    assert not c3.standing and c3.numbers == ("10",)  # a part labelled for one response is one matter's


def test_a_one_matter_part_is_standing_only_with_its_own_standing_quote():
    rules = [{"rule_id": "r3", "text": "Response #9: add the cumulative objection, make this a rule."}]
    ans = {"rules": [{"rule": "r3", "clauses": [_clause(quote="add the cumulative objection", keys=[],
                                                         grounds=["cumulative"], standing_quote="make this a rule")]}]}  # fmt: skip
    got, problems = reading.check(ans, rules, BOOK)
    assert problems == [] and got["r3"]["clauses"][0].standing
    ans["rules"][0]["clauses"][0]["standing_quote"] = "every matter"
    got, _ = reading.check(ans, rules, BOOK)
    assert not got["r3"]["clauses"][0].standing


def test_a_quoted_passage_in_the_quote_is_taken_as_the_words():
    rules = [{"rule_id": "q", "text": 'For phone bills respond "Subject to the objections, none will be produced".'}]
    ans = {"rules": [{"rule": "q", "clauses": [_clause(
        quote='respond "Subject to the objections, none will be produced"', act="use_phrase", keys=[], grounds=[])]}]}  # fmt: skip
    got, problems = reading.check(ans, rules, BOOK)
    assert problems == []
    assert got["q"]["entries"][0].text == "Subject to the objections, none will be produced."


@pytest.mark.parametrize(
    "clause, fragment",
    [
        (_clause(quote="add the relevance objection"), "not copied exactly"),
        (_clause(keys=["overbroad"]), "none of its short names"),
        (_clause(keys=["invented"]), "not in the phrase book"),
        (_clause(grounds=["relevance"]), "not named in the quote"),
        (_clause(doc_types=["memo"]), "doc_types"),
        (_clause(doc_types=[]), "doc_types"),
        (_clause(act="rewrite"), "act is one of"),
        (_clause(when="sometimes"), "act is one of"),
        ("not an object", "a clause is an object"),
    ],
)
def test_every_unverifiable_clause_is_refused(clause, fragment):
    _got, problems = reading.check(_answer(r1=clause), RULES, BOOK)
    assert any(fragment in p for p in problems), problems


def test_doc_types_outside_the_rules_kinds_are_refused():
    rules = [{**RULES[0], "doc_kinds": ["discovery_set"]}]
    _got, problems = reading.check({"rules": [{"rule": "r1", "clauses": [_clause()]}]}, rules, BOOK)
    assert any("outside the rule's recorded" in p for p in problems)


@pytest.mark.parametrize(
    "answer, fragment",
    [
        ({}, 'an object {"rules"'),
        ({"rules": [{"rule": "r1", "clauses": []}]}, "at least one clause"),
        ({"rules": [{"rule": "zz", "clauses": [_clause()]}]}, "not one of the rules"),
        ({"rules": [{"rule": "r1", "clauses": [_clause()]}]}, "r2: missing"),
    ],
)
def test_an_incomplete_read_is_refused(answer, fragment):
    _got, problems = reading.check(answer, RULES, BOOK)
    assert any(fragment in p for p in problems), problems


def test_the_read_is_asked_once_more_then_refused(tmp_path):
    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(user)
        return "{}"

    with pytest.raises(reading.ReadRefused):
        reading.read(tmp_path, RULES, BOOK, ask)
    assert len(calls) == 2 and "REFUSED BY CODE" in calls[1]


def test_a_read_is_cached_by_rule_text_and_never_asked_again(tmp_path):
    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(stage)
        return json.dumps(_answer())

    clauses, bk = reading.read(tmp_path, RULES, BOOK, ask)
    again, _ = reading.read(tmp_path, RULES, BOOK, None)
    assert calls == [reading.STAGE] and again == clauses and "r2#1" in bk.entries
    assert reading.plan_chars(RULES, BOOK, tmp_path) == 0
    with pytest.raises(reading.ReadRefused):
        reading.read(tmp_path, [*RULES, {"rule_id": "r4", "text": "Invented new rule."}], BOOK, None)


def test_many_rules_are_read_a_batch_at_a_time_and_each_batch_is_kept(tmp_path, monkeypatch):
    """One call for every rule outgrew the answer's token cap once the firm had
    enough rules: the cut-off answer parsed as no object and every drafting job
    failed rule_unchecked. Rules go a batch per call, each batch saved."""
    monkeypatch.setattr(reading, "BATCH", 2)
    full = {r["rule"]: r for r in _answer()["rules"]}
    asked: list[list[str]] = []

    def ask(stage, system, user, max_tokens):
        ids = [rid for rid in full if f"[rule {rid}]" in user]
        asked.append(ids)
        return json.dumps({"rules": [full[r] for r in ids]})

    clauses, _bk = reading.read(tmp_path, RULES, BOOK, ask)
    assert [len(a) for a in asked] == [2, 1] and sorted(sum(asked, [])) == ["r1", "r2", "r3"]
    assert {c.rule for c in clauses} == {"r1", "r2", "r3"}


def test_a_refused_batch_keeps_the_batches_before_it(tmp_path, monkeypatch):
    monkeypatch.setattr(reading, "BATCH", 2)
    full = {r["rule"]: r for r in _answer()["rules"]}

    def ask(stage, system, user, max_tokens):
        if "[rule r3]" in user and "[rule r1]" not in user:
            return '{"rules": [ {"rule": "r3", "clau'  # cut off mid-answer
        return json.dumps({"rules": [full["r1"], full["r2"]]})

    with pytest.raises(reading.ReadRefused):
        reading.read(tmp_path, RULES, BOOK, ask)
    assert len(reading._cache(tmp_path)) == 2  # r1 and r2 are kept; a retry asks only r3


# ---- the grid ------------------------------------------------------------------------------
C_PRIV = Clause(
    "r1", 1, "add the privacy objection", True, ("rfp_response",), "add_objection", ("privacy",), ("privacy",)
)
C_PROD = Clause("r5", 1, "use the production phrase", True, ("rfp_response",), "use_phrase", ("prod",))
C_CUT = Clause("r6", 1, "remove sentences about what he has", True, ("rfp_response",), "remove_part")
CLAUSES = [C_PRIV, C_PROD, C_CUT]


def _items(**over) -> dict:
    rows = {
        "1": {"item": "1", "marks": "YNN", "cells": {"1": {"state": "apply", "keys": ["privacy"]}}},
        "2": {"item": "2", "marks": "NYN", "cells": {"2": {"state": "apply", "keys": ["prod"], "exhibits": ["Medical Bills"],
                                                         "sentences": ["Responding Party will produce the bills he has."]}}},
    }  # fmt: skip
    rows.update(over)
    return {"items": [r for r in rows.values() if r is not None]}


def test_a_complete_grid_is_kept():
    cells, problems = grid.check(_items(), CLAUSES, _own(), BOOK)
    assert problems == []
    assert cells[0] == Cell("1", "r1#1", "apply", ("privacy",))
    assert cells[1].exhibits == ("Medical Bills",)


@pytest.mark.parametrize(
    "over, fragment",
    [
        ({"2": None}, "item 2: missing"),
        ({"9": {"item": "9", "marks": "NNN"}}, "not one of the document's responses"),
        ({"1": {"item": "1", "marks": "YN"}}, "marks is 3 characters"),
        ({"1": {"item": "1", "marks": "YNX"}}, "marks is 3 characters"),
        ({"1": {"item": "1", "marks": "YNN", "cells": {}}}, "state is one of"),
        ({"1": {"item": "1", "marks": "YNN", "cells": {"1": {"state": "apply", "keys": ["prod"]}}}}, "not one of the clause"),
        ({"1": {"item": "1", "marks": "YNN", "cells": {"1": {"state": "apply"}}}}, "names a key or a ground"),
        ({"1": {"item": "1", "marks": "NYN", "cells": {"2": {"state": "apply", "keys": []}}}}, "exactly one key"),
        ({"1": {"item": "1", "marks": "NNY", "cells": {"3": {"state": "apply"}}}}, "names the response's own sentences"),
        ({"1": {"item": "1", "marks": "NNY", "cells": {"3": {"state": "apply", "sentences": ["Invented."]}}}}, "not one of the response's own"),
        ({"1": {"item": "1", "marks": "NYN", "cells": {"2": {"state": "apply", "keys": ["prod"], "exhibits": ["B"]}}}}, "never by its letter"),
        ({"1": {"item": "1", "marks": "YNN", "cells": {"1": {"state": "apply", "keys": "privacy"}}}}, "lists of text"),
    ],
)  # fmt: skip
def test_every_incomplete_or_unverifiable_grid_is_refused(over, fragment):
    _cells, problems = grid.check(_items(**over), CLAUSES, _own(), BOOK)
    assert any(fragment in p for p in problems), problems


def test_a_repeated_item_is_refused():
    raw = _items()
    raw["items"].append(raw["items"][0])
    assert any("listed twice" in p for p in grid.check(raw, CLAUSES, _own(), BOOK)[1])


def test_a_clause_for_some_numbers_is_refused_on_another():
    only2 = Clause(
        "email", 1, "for No. 2", True, ("rfp_response",), "add_objection", ("privacy",), (), "judged", ("2",)
    )
    raw = {"items": [{"item": "1", "marks": "Y", "cells": {"1": {"state": "apply", "keys": ["privacy"]}}},
                     {"item": "2", "marks": "N"}]}  # fmt: skip
    assert any("for response(s) 2 only" in p for p in grid.check(raw, [only2], _own(), BOOK)[1])


def test_the_grid_is_asked_once_more_then_refused_and_cached_when_kept(tmp_path):
    calls = []

    def bad(stage, system, user, max_tokens):
        calls.append(user)
        return "not json"

    with pytest.raises(grid.GridRefused):
        grid.ask(tmp_path, CLAUSES, _doc(), _own(), BOOK, bad)
    assert len(calls) == 2
    first = grid.ask(tmp_path, CLAUSES, _doc(), _own(), BOOK, says(_items()))
    assert grid.ask(tmp_path, CLAUSES, _doc(), _own(), BOOK, None) == first
    with pytest.raises(grid.GridRefused):
        grid.ask(tmp_path / "other", CLAUSES, _doc(), _own(), BOOK, None)
    assert grid.ask(tmp_path, [], _doc(), _own(), BOOK, None) == []


# ---- makers -------------------------------------------------------------------------------
PRIV = "Responding Party objects to this Request on the ground that it invades the privacy of Responding Party."


def test_an_objection_goes_before_the_transition():
    doc = _doc()
    blocks, why = makers.add(doc.blocks, _own(doc)["1"], PRIV, BOOK)
    assert why == ""
    got = rs.Document(blocks, doc.responses, doc.index, doc.notes)
    assert [p.key for p in _own(got)["1"]] == ["overbroad", "privacy", "trans", "prod"]


def test_an_objection_already_there_changes_nothing():
    doc = _doc()
    sentence = "Responding Party objects to this Request on the ground that it is overbroad."
    assert makers.add(doc.blocks, _own(doc)["1"], sentence, BOOK) == (doc.blocks, makers.ALREADY)


def test_with_no_objection_or_transition_the_books_transition_follows_it():
    doc = _doc()
    blocks, why = makers.add(doc.blocks, _own(doc)["2"], PRIV, BOOK)
    assert why == ""
    got = rs.Document(blocks, doc.responses, doc.index, doc.notes)
    assert [p.role for p in _own(got)["2"]] == ["objection", "transition", "other"]
    bare = book_mod.Book({k: v for k, v in BOOK.entries.items() if k != "trans"})
    assert makers.add(doc.blocks, _own(doc)["2"], PRIV, bare)[1] == makers.NO_TRANSITION
    assert makers.add(doc.blocks, (), PRIV, BOOK)[1] == makers.NO_PARAGRAPH


def test_a_phrase_takes_the_place_of_the_named_sentence_with_its_exhibit_letter():
    doc = _doc()
    sentence, why = makers.words(BOOK, "prod", "Request", doc, ("Medical Bills",))
    assert why == "" and sentence.endswith("found at Exhibit B.")
    blocks, why = makers.put(doc.blocks, _own(doc)["2"], sentence, ("Responding Party will produce the bills he has.",))
    assert why == "" and blocks[doc.by_number()["2"].body[0]] == sentence
    two, _ = makers.words(BOOK, "prod", "Request", doc, ("Photographs", "Medical Bills"))
    assert two.endswith("found at Exhibits A and B.")
    assert makers.words(BOOK, "prod", "Request", doc, ("Invented",)) == ("", makers.EXHIBIT)


def test_a_phrase_replaces_the_disposition_or_ends_a_response_without_one():
    doc = _doc()
    blocks, _ = makers.put(doc.blocks, _own(doc)["1"], BOOK.text("nodocs"), ())
    assert blocks[doc.by_number()["1"].body[0]].endswith("as follows: " + BOOK.text("nodocs"))
    plain = rs.read(MD.replace("Responding Party will produce the bills he has.", "Invented answer."))
    blocks, _ = makers.put(plain.blocks, _own(plain)["2"], BOOK.text("nodocs"), ())
    assert blocks[plain.by_number()["2"].body[0]] == "Invented answer. " + BOOK.text("nodocs")


def test_a_phrase_takes_the_place_of_an_answer_left_for_the_attorney():
    """FALSIFIER: append after "[ATTORNEY TO CONFIRM]" and the recheck cannot find the phrase (r4, 2026-11-24)."""
    marked = rs.read(MD.replace("Responding Party will produce the bills he has.", "[ATTORNEY TO CONFIRM]"))
    blocks, why = makers.put(marked.blocks, _own(marked)["2"], BOOK.text("nodocs"), ())
    body = blocks[marked.by_number()["2"].body[0]]
    assert why == "" and "ATTORNEY" not in body and body.endswith(BOOK.text("nodocs"))


def test_a_cut_removes_the_sentence_and_never_empties_a_paragraph():
    doc = _doc()
    first = "Responding Party objects to this Request on the ground that it is overbroad."
    blocks, why = makers.cut(doc.blocks, _own(doc)["1"], (first,))
    assert why == "" and blocks[doc.by_number()["1"].body[0]].startswith("Without waiving")
    only = ("Responding Party will produce the bills he has.",)
    assert makers.cut(doc.blocks, _own(doc)["2"], only) == (doc.blocks, makers.EMPTIES)


# ---- written -----------------------------------------------------------------------------
NEED = written.Need("2", "cumulative", "r3", "All bills from Invented Clinic.", "add the cumulative objection")
LEAD = "Responding Party objects to this Request on the ground that it"


@pytest.mark.parametrize(
    "sentence, fragment",
    [
        ("Plaintiff objects that it is cumulative.", "does not start with the lead"),
        (f"{LEAD} is duplicative.", "does not name the ground"),
        (f"{LEAD} is cumulative. It is also unfair.", "not one sentence"),
        (f"{LEAD} is cumulative", "not one sentence"),
        (f"{LEAD} is cumulative of 14 earlier requests.", "states '14'"),
        (f"{LEAD} is cumulative of the Smith records.", "names 'Smith'"),
    ],
)
def test_a_written_sentence_is_refused_unless_it_is_the_firms_shape(sentence, fragment):
    assert fragment in written.check_one(sentence, NEED, LEAD, BOOK)


def test_a_written_sentence_may_name_what_the_request_names():
    assert written.check_one(f"{LEAD} is cumulative of the Invented Clinic bills.", NEED, LEAD, BOOK) == ""


def test_the_writer_retries_once_and_leaves_what_stays_refused():
    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(user)
        return json.dumps({"sentences": [{"item": "2", "ground": "cumulative", "sentence": "Invented."}]})

    assert written.write([NEED], "Request", BOOK, ask) == {}
    assert len(calls) == 2 and "REFUSED BY CODE" in calls[1]
    ok = says({"sentences": [{"item": "2", "ground": "cumulative", "sentence": f"{LEAD} is cumulative."}]})
    assert written.write([NEED], "Request", BOOK, ok) == {("2", "cumulative"): f"{LEAD} is cumulative."}
    no_lead = book_mod.Book({"overbroad": BOOK.entries["overbroad"]})
    assert written.write([NEED], "Request", no_lead, ok) == {}
