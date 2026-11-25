"""The firm's revise lane settling a draft's candidate-objection markers, on the
six causes measured 2026-11-25 (three real revision requests, the seat's live
rules and phrase book: 1 of 23 required changes made). Every entry, rule and
response here is invented, shaped like the real ones: the firm keeps a short
and a long form of the attorney-client and work product objections, a rule
says "Privilege", and a rule says to adopt the candidate objections."""

from __future__ import annotations

import json

from jobs.drafting.revise import markers as M
from jobs.drafting.rulebook import book as book_mod, makers
from jobs.drafting.rulebook.book import fold
from jobs.drafting.rulebook.reading import _named
from jobs.drafting.rulebook.types import Part

LEAD = "Responding Party objects to this {item}"
AC_SHORT = f"{LEAD} to the extent it seeks attorney-client privileged information."
AC_LONG = (
    f"{LEAD} to the extent it seeks information protected by the attorney-client privilege, which "
    "protects confidential communications between a client and the client's attorney."
)
WP_SHORT = f"{LEAD} to the extent it seeks attorney work product."
WP_LONG = (
    f"{LEAD} to the extent it seeks information protected by the attorney work product doctrine, "
    "including the impressions, conclusions and opinions of counsel."
)
OVERBROAD = f"{LEAD} on the ground that it is overbroad as to time and scope and not limited to the incident."
ROWS = [
    {"key": "obj_031", "kind": "objection", "text": OVERBROAD, "names": ["overbroad"], "sets": [], "source": ""},
    {"key": "obj_033", "kind": "objection", "text": AC_SHORT, "names": ["attorney-client"], "sets": [], "source": ""},
    {"key": "obj_034", "kind": "objection", "text": AC_LONG, "names": ["attorney-client"], "sets": [], "source": ""},
    {"key": "obj_030", "kind": "objection", "text": WP_SHORT, "names": ["work product"], "sets": [], "source": ""},
    {
        "key": "obj_036",
        "kind": "objection",
        "text": WP_LONG,
        "names": ["work product privilege"],
        "sets": [],
        "source": "",
    },  # fmt: skip
    {
        "key": "tr",
        "kind": "transition",
        "text": "Without waiving these objections, Responding Party responds:",
        "names": [],
        "sets": [],
        "source": "",
    },  # fmt: skip
    {
        "key": "start",
        "kind": "statement",
        "text": "The request is answered as follows.",
        "names": [],
        "sets": [],
        "source": "",
    },  # fmt: skip
]
BOOK = book_mod.parse(json.dumps(ROWS))
RFP = "requests_for_production"


def _fill(text: str) -> str:
    return text.replace("{item}", "request")


def _marker(ground: str, words: str = "the documents are privileged") -> str:
    return f"{{{{ATTORNEY: CANDIDATE OBJECTION: {ground} | {words}}}}}"


# ---- 1. a ground two entries match is settled in code, and the choice recorded ----


def test_a_ground_two_entries_match_takes_the_long_form_and_records_it():
    blocks = [f"{_marker('attorney-client')} Responding Party will produce the file."]
    out, done = M.settle("4", blocks, BOOK, RFP, {"obj_033", "obj_034"})
    assert out == [f"{_fill(AC_LONG)} Responding Party will produce the file."]
    assert (done[0].key, done[0].how, done[0].among) == ("obj_034", M.LONG_FORM, ("obj_033", "obj_034"))


def test_the_entry_the_marker_already_holds_is_chosen():
    held = _marker("work product", _fill(WP_SHORT))
    out, done = M.settle("5", [held], BOOK, RFP, {"obj_030", "obj_036"})
    assert out == [_fill(WP_SHORT)] and (done[0].key, done[0].how) == ("obj_030", M.HELD)


def test_two_entries_of_one_length_take_the_first_in_book_order():
    twin = book_mod.parse(json.dumps([{**ROWS[1], "key": "a"}, {**ROWS[1], "key": "b"}, ROWS[5]]))
    key, how = M.choose(["a", "b"], "", twin)
    assert (key, how) == ("a", M.BOOK_ORDER)


# ---- 2. a ground no entry names: a rule adopting the candidates takes the marker's own words ----


def test_a_rule_adopting_the_candidates_takes_the_markers_own_sentence():
    marker = _marker("equal access", "the information is equally available to Propounding Party")
    blocks = [f"{marker} Responding Party will produce the file."]
    out, done = M.settle("7", blocks, BOOK, RFP, set(), adopt_own=True)
    assert out == ["The information is equally available to Propounding Party. Responding Party will produce the file."]
    assert done[0].how == M.OWN and not done[0].left
    left, done = M.settle("7", blocks, BOOK, RFP, set())
    assert left == blocks and done[0].left == M.PHRASE_MISSING
    rules = [{"rule_id": "r-adopt", "wording": ["Adopt the candidate objections in the draft."]}]
    assert M.adopts(rules) and not M.adopts([{"rule_id": "r", "wording": ["Object on privacy."]}])


# ---- 3. a rule's ground names entries through one table of synonyms ----


def test_a_rule_saying_privilege_names_the_attorney_client_entries_and_not_work_product():
    assert M.named_by(["Privilege"], BOOK) == {"obj_033", "obj_034"}
    assert M.named_by(["work product"], BOOK) == {"obj_030", "obj_036"}
    keys, _grounds, why = _named(fold("object on privilege where it applies"), BOOK, ["obj_034"], [])
    assert keys == ("obj_034",) and why == ""
    out, done = M.settle("2", [_marker("privilege")], BOOK, RFP, M.named_by(["Privilege"], BOOK))
    assert out == [_fill(AC_LONG)] and done[0].key == "obj_034"


# ---- 4. a ground the response already states is never written twice ----


def test_a_marker_whose_ground_the_response_already_states_goes_even_when_not_allowed():
    blocks = [_fill(AC_LONG), f"{_marker('attorney-client')} Responding Party will produce the file."]
    out, done = M.settle("3", blocks, BOOK, RFP, set())
    assert out == [_fill(AC_LONG), "Responding Party will produce the file."]
    assert done[0].how == M.ALREADY and not done[0].left
    own = "the information is equally available to Propounding Party"
    stated = ["The information is equally available to Propounding Party.", _marker("equal access", own)]
    out, _done = M.settle("3", stated, BOOK, RFP, set(), adopt_own=True)
    assert out == ["The information is equally available to Propounding Party."]


# ---- 5. the long-form objection takes the short-form clause out of the old sentence ----


def test_adding_the_long_form_overbroad_objection_removes_the_short_clause():
    old = "Responding Party objects to this Request on the ground that it is vague and ambiguous, and that it is overbroad."
    trans = "Without waiving these objections, Responding Party responds:"
    block = f"{old} {trans} Yes."
    own = (Part(old, "objection", block=0, at=0), Part(trans, "transition", block=0, at=len(old) + 1))
    out, why = makers.add([block], own, _fill(OVERBROAD), BOOK, ("overbroad",))
    assert why == ""
    assert out == [
        "Responding Party objects to this Request on the ground that it is vague and ambiguous. "
        f"{_fill(OVERBROAD)} {trans} Yes."
    ]
    assert out[0].count("overbroad") == 1
    only = "Responding Party objects to this Request on the ground that it is overbroad."
    assert makers.short_clause(only, ("overbroad",)) == only  # its only ground: never emptied


# ---- 6. a rule's stated words keep the firm's casing ----


def test_stated_words_keep_their_own_casing_and_only_the_first_letter_is_capitalised():
    assert BOOK.cased("Responding Party objects to this interrogatory and the request as stated") == (
        "Responding Party objects to this interrogatory and the request as stated."
    )
    assert BOOK.cased("each response is verified") == "Each response is verified."


# ---- the second rehearsal, 2026-11-25: the firm's own rule wording, and markers with no "|" ----

INCLUDE_RULE = {
    "rule_id": "r-include",
    "wording": ["Include all legally applicable California objections for each response."],
}
NO_PIPE = (
    "Responding Party objects to this interrogatory on the ground that it seeks information protected by the "
    "right of privacy of third parties under Article I, section 1 of the California Constitution."
)


def test_a_rule_that_tells_the_draft_to_include_objections_is_an_adopt_rule():
    assert M.adopts([INCLUDE_RULE])
    assert M.adopts([{"rule_id": "r", "wording": ["Assert any applicable objection to each request."]}])
    assert not M.adopts([{"rule_id": "r", "wording": ["Add a privacy objection where it applies."]}])
    assert not M.adopts([{"rule_id": "r", "wording": ["Do not include boilerplate objections."]}])


def test_a_marker_with_no_pipe_adopts_its_objection_sentence():
    marker = f"{{{{ATTORNEY: CANDIDATE OBJECTION: {NO_PIPE}}}}}"
    out, done = M.settle("1", [f"{marker} Answer one."], BOOK, "special_interrogatories", set(), adopt_own=True)
    assert out == [f"{NO_PIPE} Answer one."] and done[0].how == M.OWN


def test_a_marker_with_no_pipe_drops_its_trailing_note_to_the_attorney():
    for note in (
        "Note: confirm with the client whether the third parties consented.",
        "Consider whether the tax returns are relevant. [Attorney: decide]",
        "[ATTORNEY: confirm the date range]",
    ):
        marker = f"{{{{ATTORNEY: CANDIDATE OBJECTION: {NO_PIPE} {note}}}}}"
        out, _done = M.settle("17", [marker], BOOK, "special_interrogatories", set(), adopt_own=True)
        assert out == [NO_PIPE], note


def test_a_marker_with_no_pipe_and_no_objection_sentence_is_left_and_recorded():
    marker = "{{ATTORNEY: CANDIDATE OBJECTION: privacy of third parties may apply here}}"
    out, done = M.settle("9", [marker], BOOK, "special_interrogatories", set(), adopt_own=True)
    assert out == [marker] and done[0].left == M.NO_SENTENCE


def test_the_item_word_is_lower_case_mid_sentence_and_capitalised_only_at_a_sentence_start():
    assert book_mod.fill_item("Responding Party objects to this {item} as vague.", "Interrogatory") == (
        "Responding Party objects to this interrogatory as vague."
    )
    assert book_mod.fill_item("{item} No. 4 is compound. {item} No. 5: this {item} too.", "Request") == (
        "Request No. 4 is compound. Request No. 5: this request too."
    )
    assert BOOK.text("obj_031", item="Interrogatory").startswith("Responding Party objects to this interrogatory on")
    out, _done = M.settle("1", [_marker("overbroad")], BOOK, "special_interrogatories", {"obj_031"})
    assert out == [OVERBROAD.replace("{item}", "interrogatory")]
