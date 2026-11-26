"""A propounded set the Operator filed, revised on the attorney's reply
(revise/propounded.py): three closed ops, every value checked by code.

Pure over markdown and an ``Oracle``: the stand-in oracle below is a real class
implementing the protocol, answering from fixed values (no model, $0). Every
name, number and sentence is invented (Example / Exampletown). Each test names
the line it defends.
"""

from __future__ import annotations


import json
from typing import Any

import pytest

from jobs.drafting import set_items
from jobs.drafting.revise import (
    propounded as p,
    propounded_ask as pa,
    propounded_decl as pdc,
    propounded_define as pd,
    propounded_set as ps,
)

SROG = "special_interrogatories"
RFA = "requests_for_admission"
RFP = "requests_for_production"
LABEL = {SROG: "SPECIAL INTERROGATORY", RFA: "REQUEST FOR ADMISSION", RFP: "REQUEST FOR PRODUCTION"}


def set_md(
    variant: str = SROG,
    texts: list[str] | None = None,
    party: str = "Defendant DELTA EXAMPLE",
    decl: str | None = None,
    inline: bool = False,
) -> str:
    texts = texts or [f"State fact number {n} about the INCIDENT." for n in range(1, 5)]
    head = [
        "PROPOUNDING PARTY: Plaintiff ALPHA EXAMPLE",
        f"RESPONDING PARTY: {party}",
        "SET NO.: One",
        "## DEFINITIONS",
        '1. "INCIDENT" means the collision on June 1, 2026 in Exampletown.',
        '2. "YOU" means the responding party.',
        f"## {set_items.NAMES[variant][1].upper()}",
    ]
    body = []
    for n, t in enumerate(texts, 1):
        if inline:
            body.append(f"**{LABEL[variant]} NO. {n}:** {t}")
        else:
            body += [f"**{LABEL[variant]} NO. {n}:**", t]
    tail = ["Dated: December 1, 2026", "EXAMPLE LAW, by Alpha Lawyer"]
    if decl:
        tail += ["DECLARATION FOR ADDITIONAL DISCOVERY", decl]
    return "\n\n".join(head + body + tail) + "\n"


class StandIn:
    """The protocol answered from fixed values: what the model would name."""

    def __init__(
        self, definition: str = "", additions: list[str] | None = None, ranking: list[int] | None = None
    ) -> None:
        self._definition, self._additions, self._ranking = definition, additions or [], ranking or []
        self.calls: list[str] = []

    def definition(self, term: str, quote: str) -> str:
        self.calls.append(f"definition:{term}")
        return self._definition

    def additions(self, unit: str, md: str, variant: str, n: int, quote: str) -> list[str]:
        self.calls.append(f"additions:{unit}:{n}")
        return list(self._additions)

    def ranking(
        self, unit: str, md: str, variant: str, n: int, protected: set[int], families: dict | None = None
    ) -> list[int]:
        self.calls.append(f"ranking:{unit}:{n}")
        return list(self._ranking)


def unit(md: str, name: str = "Special Interrogatories, Set One.docx", hint: str = "") -> p.Unit:
    return p.read_unit(name, hint, md)


# ---- numbers -----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,count,want",
    [
        ("down to 120-125", 150, 125),
        ("to 120 to 125", 150, 125),
        ("to 120 – 125", 150, 125),
        ("trim to 120125", 150, 125),  # the dash lost: one ascending split, each within the count
        ("trim to 120125", 100, 125),  # same width, ascending: read as "120-125", as the dashed form is
        ("to 1234", 200, 34),  # 1/234 is over the count and 123/4 descends: only 12/34
        ("to 5040", 100, None),  # 50/40 descends and the rest lead with zero: no split
        ("to 12345", 2345, None),  # 1/2345 and 12/345 both fit: two readings, never a guess
        ("to 30", 40, 30),
        ("five more", None, 5),
        ("cut 10 down to 30", 40, None),  # two numbers: code cannot tell which
        ("some of them", 40, None),
    ],
)
def test_numbers_are_parsed_by_code_never_guessed(text, count, want):
    """FALSIFIER: read "120125" as one hundred twenty thousand, or take the
    first of two numbers, and a set is cut to a number the email never said."""
    assert p.parse_number(text, count) == want


def test_a_run_within_the_count_is_itself():
    """ "1213" in a set of 20 splits only as 12/13; in a set of 2,000 it is a number in range."""
    assert p.parse_number("to 1213", 20) == 13
    assert p.parse_number("to 1213", 2000) == 1213


def test_a_cut_reads_to_and_by_and_holds_a_bare_number():
    assert p.cut_drop("down to 120-125", 150) == 30  # a range's low end: SMD cuts to 120 inside 120-125
    assert p.cut_drop("cut by 10", 40) == 10
    assert p.cut_drop("to 40", 40) == 0  # already there: nothing removed
    assert p.cut_drop("to 50", 40) is None  # a target over the count is not a cut: never a guess
    assert p.cut_drop("10", 40) is None  # to ten or by ten? never a guess
    assert p.cut_drop("to 0", 40) is None  # a cut never empties a set


@pytest.mark.parametrize(
    "quote,want",
    [
        ("remove up to 10", 10),
        ("remove 5 or 6", None),
        ("cut only 5", 5),
        ("drop a max of 5", 5),
        ("cut 5 in total", 5),
        ("reduce it to 25", 5),
        ("cut by 16", None),  # more than half the set: the attorney's to confirm, held
        ("cut to 10", None),  # the same, read as a target
        ("cut 120 to 125", None),  # a removal and a target at once: never a guess
    ],
)
def test_a_cut_tells_a_target_from_a_number_to_remove(quote, want):
    """FALSIFIER: read bare "to", "only", "max" or "total" as a target, and
    "remove up to 10" from 30 removes 20."""
    assert p.cut_drop(quote, 30) == want


def test_a_joined_range_and_a_dashed_one_agree_when_the_set_is_already_under_it():
    assert p.cut_drop("down to 120125", 100) == p.cut_drop("down to 120-125", 100) == 0


def test_an_add_reads_a_count_or_a_total_and_defaults_to_ten():
    assert p.add_count("", 20) == p.DEFAULT_ADD == 10
    assert p.add_count("five more", 20) == 5
    assert p.add_count("add up to 5", 20) == 5
    assert p.add_count("bring it to 30 total", 20) == 10
    assert p.add_count("a bunch more", 20) is None


# ---- reading the filed sets -----------------------------------------------------------------------------
def test_a_hand_filed_set_takes_its_type_from_its_name_and_its_party_from_the_document():
    """A set SMD filed by hand (deliver_by_smd) carries no served_sets: the unit
    hint is "". FALSIFIER: require the hint and every hand-filed set fails."""
    md = set_md(RFA, party="Defendant GAMMA EXAMPLE CORP.")
    u = p.read_unit("Requests for Admission, Set One.docx", "", md)
    assert (u.set_type, u.party, u.count) == (RFA, "Defendant GAMMA EXAMPLE CORP.", 4)
    # No name to go by: the labels say.
    assert p.read_unit("Set One.docx", "", md).set_type == RFA


def test_labels_word_numbers_itself_read_as_nothing_and_the_base_is_unproven():
    """Word list numbering puts the number outside the text: the labels read
    "SPECIAL INTERROGATORY NO.:" with no digit. FALSIFIER: treat zero items as an
    empty set and a cut or an add is made against numbers code never read."""
    md = set_md().replace("NO. 1:", "NO.:").replace("NO. 2:", "NO.:").replace("NO. 3:", "NO.:")
    md = md.replace("NO. 4:", "NO.:")
    with pytest.raises(p.Refused) as e:
        p.read_unit("Special Interrogatories, Set One.docx", "", md)
    assert e.value.code == "revise_base_unproven"


def test_a_file_labeled_as_another_kind_than_delivered_is_unproven():
    with pytest.raises(p.Refused) as e:
        p.read_unit("x.docx", RFA, set_md(SROG))
    assert e.value.code == "revise_base_unproven"


def test_a_form_set_is_kept_as_filed_never_edited():
    u = p.read_unit("Form Interrogatories, Set One.docx", "form_interrogatories", "anything")
    assert u.supported is False


def test_a_bold_label_on_its_own_line_is_not_read_as_carrying_its_text():
    """FALSIFIER (set_items._INLINE before 2026-11-25): the closing "**" read as
    text, so the set's last request ended at its label and an append went
    between the label and its words."""
    md = set_md(texts=["One.", "Two."])
    last = set_items.read(md, SROG)[-1]
    assert last.text(md).endswith("Two.")
    inline = set_md(texts=["One.", "Two."], inline=True)
    assert set_items.read(inline, SROG)[-1].text(inline).endswith("Two.")


# ---- the plan ------------------------------------------------------------------------------------------
EMAIL = (
    "Thanks. Please define VEHICLE, and cut the requests for admission to Gamma Example down to 120125. "
    "Also add five more special interrogatories about his phone."
)


def test_code_keeps_an_op_only_in_the_emails_own_words():
    raw = {
        "ops": [
            {"op": "define", "term": "VEHICLE", "quote": "Please define VEHICLE", "set_types": []},
            {"op": "cut", "quote": "cut the requests for admission to Gamma Example", "number_quote": "down to 120125",
             "party_quote": "Gamma Example", "set_types": [RFA]},
            {"op": "add", "quote": "add ten more special interrogatories", "set_types": [SROG]},
        ],
        "other": [],
    }  # fmt: skip
    ops, other, problems = p.check_plan(raw, EMAIL)
    assert [o.op for o in ops] == ["define", "cut"]
    assert problems == ["op 3: quote is not the email's exact words"]


def test_a_plan_still_unverifiable_after_its_retry_holds_never_drops_the_ask():
    """FALSIFIER: drop the op the model misquoted and the job files a partial
    revision the attorney did not ask for."""
    bad = {"ops": [{"op": "add", "quote": "add ten more", "set_types": []}], "other": []}
    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(stage)
        return json.dumps(bad)

    ops, other = pa.ask_plan(EMAIL, [unit(set_md())], ask)
    assert calls == ["revise-plan", "revise-plan"] and ops == [] and other  # held by the run as unsupported


def test_a_ranking_too_short_to_cut_is_refused_after_its_retry():
    md = set_md(texts=["A.", "Cites Special Interrogatory No. 1.", "C.", "D."])
    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(stage)
        return json.dumps({"ranking": [1, 3]})  # 1 is cited: only one can go

    with pytest.raises(p.Refused) as e:
        pa.ask_ranking(md, SROG, 2, set(), ask)
    assert e.value.code == "revise_unchecked" and calls == ["revise-cut", "revise-cut"]


def test_the_model_oracle_asks_each_value_once_and_keeps_it():
    kept: list[dict[str, Any]] = []
    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(stage)
        return json.dumps({"definition": '"VEHICLE" means the car.'})

    o = pa.ModelOracle(ask, "define VEHICLE", saved=lambda a: kept.append(dict(a)))
    assert o.definition("VEHICLE", "define VEHICLE") == o.definition("vehicle", "define VEHICLE")
    assert calls == ["revise-define"] and kept == [{"definition:VEHICLE": '"VEHICLE" means the car.'}]


def test_an_ask_that_is_none_of_the_three_is_held_unsupported_before_any_write_call():
    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(stage)
        return json.dumps({"ops": [], "other": [{"quote": "reword No. 2"}]})

    with pytest.raises(p.Held) as e:
        pa.settle_plan("Please reword No. 2.", [unit(set_md())], ask)
    assert e.value.code == "revise_set_unsupported" and calls == ["revise-plan"]


def test_numbers_and_the_35_line_are_checked_before_any_write_call():
    """FALSIFIER: check after the edit, and the model is paid for a set code then holds."""
    u = unit(set_md(texts=[f"Fact {n}." for n in range(1, 39)], decl="Plaintiff propounds 38 special interrogatories."))
    with pytest.raises(p.Held) as e:
        p.check_numbers([p.Op("cut", "cut to 30", (), "", "", "to 30")], [[0]], [u])
    assert e.value.code == "revise_declaration"
    with pytest.raises(p.Held) as e:
        p.check_numbers([p.Op("cut", "cut some", (), "", "", "some")], [[0]], [u])
    assert e.value.code == "revise_scope_unclear"
    p.check_numbers([p.Op("cut", "cut to 36", (), "", "", "to 36")], [[0]], [u])  # stays above 35: no hold


# ---- scope -------------------------------------------------------------------------------------------
def _units() -> list[p.Unit]:
    return [
        p.read_unit("Special Interrogatories to Delta, Set One.docx", SROG, set_md(SROG)),
        p.read_unit("Requests for Admission to Delta, Set One.docx", RFA, set_md(RFA)),
        p.read_unit("Requests for Admission to Gamma, Set One.docx", RFA, set_md(RFA, party="Defendant GAMMA EXAMPLE")),
        p.read_unit("Form Interrogatories, Set One.docx", "form_interrogatories", ""),
    ]


def test_scope_is_the_sets_the_words_name_and_the_party_they_name():
    us = _units()
    cut = p.Op("cut", "cut the requests for admission to Gamma Example", (RFA,), "Gamma Example", "", "down to 2")
    assert p.scope(cut, us, EMAIL) == [2]
    every = p.Op("define", "Please define VEHICLE", (), "", "VEHICLE")
    assert p.scope(every, us, EMAIL) == [0, 1, 2]  # no set and no party named: every set it edits
    role = p.Op("define", "define VEHICLE for the defendant", (), "the defendant", "VEHICLE")
    assert p.scope(role, us[:2], "define VEHICLE for the defendant") == [0, 1]  # one defendant: its sets


def test_a_role_that_fits_two_parties_holds():
    """FALSIFIER: the dead role branch, or a role read as every party, and "the
    defendant" edits both defendants' sets."""
    role = p.Op("define", "define VEHICLE for the defendant", (), "to the defendant", "VEHICLE")
    with pytest.raises(p.Held) as e:
        p.scope(role, _units(), "define VEHICLE for the defendant")
    assert e.value.code == "revise_scope_unclear"
    assert p.party_match("to the defendant", "Defendant DELTA EXAMPLE")  # stop words name no one


def test_a_scope_no_file_matches_is_smds_never_a_question():
    with pytest.raises(p.Held) as e:
        p.scope(p.Op("cut", "cut the ones to Omega", (), "Omega", "", "by 1"), _units(), "cut the ones to Omega")
    assert e.value.code == "revise_scope_unclear"
    # The model named a set type the email never names: not taken on its say-so.
    with pytest.raises(p.Held) as e:
        p.scope(p.Op("cut", "cut them", (RFP,), "", "", "by 1"), _units(), "cut them by 1")
    assert e.value.code == "revise_scope_unclear"


def test_two_jobs_files_in_one_thread_are_one_scope():
    """Units from two prior jobs (two folders) are scoped together: FALSIFIER,
    scope by the newest job only and the older job's set is never changed."""
    us = [
        p.read_unit("Special Interrogatories, Set One.docx", SROG, set_md(SROG)),
        p.read_unit("Special Interrogatories, Set Two.docx", SROG, set_md(SROG, party="Defendant GAMMA EXAMPLE")),
    ]
    assert p.scope(p.Op("define", "define VEHICLE", (), "", "VEHICLE"), us, "define VEHICLE") == [0, 1]


# ---- define, add, cut ---------------------------------------------------------------------------------------
def test_a_definition_goes_in_alphabetically_and_the_rest_are_numbered_on():
    md, why = p.define(set_md(), "VEHICLE", '"VEHICLE" means the 2020 Example sedan.')
    assert why is None
    assert '1. "INCIDENT"' in md and '2. "VEHICLE" means' in md and '3. "YOU" means' in md
    again, why = p.define(md, "vehicle", '"VEHICLE" means something else.')
    assert again == md and "already defined" in (why or "")


def test_definitions_titled_by_a_bold_paragraph_are_read_too():
    """FALSIFIER: read only a heading-titled Definitions, and a set filed from
    the firm's own file (a bold title, no heading style) refuses every define."""
    bold = set_md().replace("## DEFINITIONS", "**DEFINITIONS**").replace("## SPECIAL", "**SPECIAL")
    bold = bold.replace("**SPECIAL INTERROGATORIES\n", "**SPECIAL INTERROGATORIES**\n")
    md, why = p.define(bold, "VEHICLE", '"VEHICLE" means the 2020 Example sedan.')
    assert why is None and '2. "VEHICLE" means' in md and '3. "YOU" means' in md


def test_a_definition_that_does_not_open_with_its_term_is_refused():
    assert p.definition_ok("VEHICLE", "means the car.") is None
    assert p.definition_ok("VEHICLE", '"Vehicle" means the car.') == '"VEHICLE" means the car.'


def test_added_requests_are_labeled_and_numbered_by_code():
    md, added = p.add(set_md(texts=["One.", "Two."]), SROG, ["SPECIAL INTERROGATORY NO. 9: Identify your insurer."])
    assert added == [3]
    assert "**SPECIAL INTERROGATORY NO. 3:**\n\nIdentify your insurer." in md  # its label, never the model's
    inline, added = p.add(set_md(RFA, texts=["Admit one.", "Admit two."], inline=True), RFA, ["Admit three."])
    assert added == [3] and "**REQUEST FOR ADMISSION NO. 3:** Admit three." in inline


def test_a_cut_skips_any_request_a_kept_request_cites():
    """FALSIFIER: drop No. 1 while No. 2 still asks about "your answer to
    Special Interrogatory No. 1" and the served set cites a request it no longer has."""
    md = set_md(
        texts=["State the date.", "State facts for your answer to Special Interrogatory No. 1.", "Three.", "Four."]
    )
    out, dropped = p.cut(md, SROG, [1, 3, 4, 2], 2, set())
    assert dropped == [3, 4] and "State the date." in out and "Three." not in out
    # Dropping the citing request with it frees the cited one.
    _out, dropped = p.cut(md, SROG, [2, 1], 2, set())
    assert dropped == [2, 1]


# ---- the declaration -------------------------------------------------------------------------------------
DECL = "I am an attorney for plaintiff. This set contains a total of 40 specially prepared interrogatories."


def test_the_declaration_count_is_rewritten_only_inside_the_declaration():
    md = set_md(decl=DECL)
    out, line = pdc.declaration(md, 40, 38, SROG)
    assert (
        "a total of 38 specially prepared" in out and line == "the declaration's count of this set is now 38 (was 40)"
    )
    assert out.replace("38 specially", "40 specially") == md  # nothing else


@pytest.mark.parametrize("old,new", [(30, 40), (40, 30)])
def test_crossing_35_either_way_holds_for_smd(old, new):
    with pytest.raises(p.Held) as e:
        pdc.declaration(set_md(decl=DECL), old, new, SROG)
    assert e.value.code == "revise_declaration"


def test_a_declaration_whose_statements_of_the_count_disagree_holds():
    """D10. FALSIFIER: carry a list naming an item the set never had, and the
    declaration lists a request that is not there."""
    off = set_md(decl="This set contains a total of 40 interrogatories. (A) Nos. 1-38, 41.")
    with pytest.raises(p.Held) as e:
        pdc.declaration(off, 40, 38, SROG)
    assert e.value.code == "revise_declaration"
    with pytest.raises(p.Held):
        pdc.declaration(set_md(), 40, 38, SROG)  # over 35 and no declaration found


def test_a_count_spelled_out_beside_the_number_holds():
    """FALSIFIER: rewrite the digits alone, and "forty (40)" becomes "forty (38)"."""
    md = set_md(decl="This set contains a total of forty (40) specially prepared interrogatories.")
    with pytest.raises(p.Held) as e:
        pdc.declaration(md, 40, 38, SROG)
    assert e.value.code == "revise_declaration"


def test_a_caption_naming_the_declaration_is_never_the_declaration():
    """FALSIFIER: start the block at the first line that names it, and the whole
    set is the block: a request's "40" could be rewritten as the count."""
    md = set_md(decl=DECL)
    md = md.replace("SET NO.: One", "SET NO.: One\n\nDECLARATION FOR ADDITIONAL DISCOVERY ATTACHED")
    a, _b = pdc.decl_block(md, SROG)
    assert a > set_items.read(md, SROG)[-1].start


def test_a_definition_after_the_last_keeps_its_sub_paragraphs_whole():
    """FALSIFIER: insert after the last definition's first line, and its (a) and (b) are split from it."""
    md = set_md().replace(
        '2. "YOU" means the responding party.',
        '2. "YOU" means the responding party, including:\n\n(a) its agents; and\n\n(b) its attorneys.',
    )
    out, why = p.define(md, "ZONE", '"ZONE" means the area of the INCIDENT.')
    assert why is None
    assert '(a) its agents; and\n\n(b) its attorneys.\n\n3. "ZONE" means' in out


def test_cutting_a_last_request_that_runs_on_holds():
    """FALSIFIER: keep what follows the last request's first paragraph, and its
    second paragraph is left as an orphan under the request before it."""
    md = set_md(texts=["A.", "B.", "C.", "D."]).replace("\n\nD.\n\n", "\n\nD.\n\nAnd a second paragraph of D.\n\n")
    with pytest.raises(p.Held) as e:
        p.cut(md, SROG, [4], 1, set())
    assert e.value.code == "revise_scope_unclear"
    assert p.cut(md, SROG, [2], 1, set())[1] == [2]  # a request before the last: its paragraphs go with it


def test_a_cut_naming_no_kind_holds_when_the_thread_has_several_kinds():
    """FALSIFIER: "cut the requests down to 30" cuts every set of every kind."""
    us = _units()[:2]  # an SROG and an RFA, one party
    with pytest.raises(p.Held) as e:
        p.scope(p.Op("cut", "cut these down to 3", (), "", "", "down to 3"), us, "cut these down to 3")
    assert e.value.code == "revise_scope_unclear"
    add = p.Op("add", "add two more", (), "", "", "two more")
    assert p.scope(add, us, "add two more") == [0]  # an add names its kind: requests are added to SROGs alone


def test_production_sets_carry_no_count_to_rewrite():
    md = set_md(RFP, decl=DECL)
    assert pdc.declaration(md, 40, 30, RFP) == (md, None)


# ---- one set, end to end ---------------------------------------------------------------------------------
def test_one_set_is_defined_added_to_cut_renumbered_and_its_reply_written_by_code():
    """FALSIFIER: renumber without the reply naming the move, or rewrite a
    defined term the email never named, and the attorney cannot see what changed."""
    md = set_md(texts=["About the vehicle.", "About the car.", "About the truck.", "About the INCIDENT."])
    u = unit(md)
    ops = [
        p.Op("define", "define VEHICLE", (), "", "VEHICLE"),
        p.Op("add", "add two more", (), "", "", "two more"),
        p.Op("cut", "cut by 3", (), "", "", "by 3"),
    ]
    oracle = StandIn(
        '"VEHICLE" means the 2020 Example sedan.', ["About the owner.", "About the vehicle trip."], [2, 3, 5]
    )
    res = ps.revise_unit(md, u, ops, oracle)
    items = [it.text(res.md) for it in set_items.read(res.md, SROG)]
    assert [it.number for it in set_items.read(res.md, SROG)] == [1, 2, 3]
    assert "About the VEHICLE." in items[0] and "About the INCIDENT." in items[1] and "VEHICLE trip" in items[2]
    assert (res.before, res.after) == (4, 3)
    assert res.lines[0] == 'Added the definition of "VEHICLE".'
    assert "Added 2 special interrogatories, Nos. 5, 6." in res.lines
    assert "Removed former Nos. 2, 3, 5." in res.lines
    assert any("former No. 4 is now No. 2, former No. 6 is now No. 3" in ln for ln in res.lines)
    assert oracle.calls == [f"additions:{u.name}#1:2", "definition:VEHICLE", f"ranking:{u.name}#2:3"]
    lines = ps.summary(u.name, res, SROG)
    assert lines[0] == f"{u.name} (4 special interrogatories, now 3):"


def test_requests_are_added_to_special_interrogatories_only():
    md = set_md(RFA)
    res = ps.revise_unit(md, unit(md, hint=RFA), [p.Op("add", "add five", (), "", "", "five")], StandIn())
    assert not res.changed and "special interrogatories only" in res.left[0]


def test_another_sets_citation_is_never_cut_and_is_rewritten_when_it_moves():
    """An SROG cites "Request for Admission No. 3": FALSIFIER, cut it, or leave
    the SROG's citation, and the SROG file asks about a request that is gone or
    now carries another number."""
    srog = set_md(texts=["State all facts for your denial of Request for Admission No. 3."])
    rfa = set_md(RFA)
    us = [unit(srog), p.read_unit("Requests for Admission, Set One.docx", RFA, rfa)]
    assert ps.foreign_refs(us, [srog, rfa], 1) == {3: [us[0].name]}
    cut = p.Op("cut", "cut the requests for admission by 2", (RFA,), "", "", "by 2")
    out = ps.revise_all(us, [srog, rfa], [cut], [[1]], StandIn(ranking=[3, 1, 2]))
    assert [it.number for it in set_items.read(out[1].md, RFA)] == [1, 2]
    assert "Removed former Nos. 1, 2." in out[1].lines
    assert "Request for Admission No. 1." in out[0].md and "No. 3" not in out[0].md
    assert out[0].lines == [f"Its citations of {us[1].name} follow the new numbering (No. 3 is now No. 1)."]


def test_a_set_to_another_party_neither_protects_nor_is_rewritten():
    """FALSIFIER: protect across parties, and party B's citation keeps party A's request."""
    srog = set_md(
        texts=["State all facts for your denial of Request for Admission No. 3."], party="Defendant GAMMA EXAMPLE"
    )
    rfa = set_md(RFA)
    us = [unit(srog), p.read_unit("Requests for Admission, Set One.docx", RFA, rfa)]
    assert ps.foreign_refs(us, [srog, rfa], 1) == {}
    cut = p.Op("cut", "cut the requests for admission by 2", (RFA,), "", "", "by 2")
    out = ps.revise_all(us, [srog, rfa], [cut], [[1]], StandIn(ranking=[3, 1, 2]))
    assert "Removed former Nos. 1, 3." in out[1].lines and 0 not in out


def test_a_citation_in_another_file_code_cannot_place_holds():
    """Two request-for-admission sets to one party: FALSIFIER, rewrite the SROG's
    citation by either, and it may now point into the wrong set."""
    srog = set_md(texts=["State all facts for your denial of Request for Admission No. 3."])
    rfa = set_md(RFA)
    us = [unit(srog), p.read_unit("Requests for Admission, Set One.docx", RFA, rfa),
          p.read_unit("Requests for Admission, Set Two.docx", RFA, rfa)]  # fmt: skip
    cut = p.Op("cut", "cut the requests for admission by 2", (RFA,), "", "", "by 2")
    with pytest.raises(p.Held) as e:
        ps.revise_all(us, [srog, rfa, rfa], [cut], [[1]], StandIn(ranking=[1, 2, 3]))
    assert e.value.code == "revise_scope_unclear"


@pytest.mark.parametrize(
    "cite,cited",
    [
        ("Interrogatory No. 3", {3}),
        ("Special Interrogatory Nos. 3-4", {3, 4}),
        ("Special Interrogatories Nos. 2 through 4", {2, 3, 4}),
        ("Interrogatory Nos. 3 and 4", {3, 4}),
    ],
)
def test_short_and_plural_citations_are_read_and_protected(cite, cited):
    """FALSIFIER: read only "Special Interrogatory No. 3", and a cut removes a
    request the set still cites in a shorter or plural form."""
    md = set_md(texts=["A.", "B.", "C.", "D.", "E.", f"Identify every witness to your answer to {cite}."])
    assert p.references(md, SROG) == cited
    new, dropped = p.cut(md, SROG, [3, 4, 2, 1, 5], 2, set())
    assert not set(dropped) & cited


def test_a_range_citation_follows_the_renumbering_or_holds():
    """FALSIFIER: rewrite only the endpoints of "Nos. 3-4" after a cut inside the range."""
    md = set_md(
        texts=["A.", "B.", "C.", "D.", "E.", "Identify each witness to Special Interrogatory Nos. 4 through 5."]
    )
    res = ps.revise_unit(md, unit(md), [p.Op("cut", "cut by 1", (), "", "", "by 1")], StandIn(ranking=[2, 1]))
    assert "Special Interrogatory Nos. 3 through 4." in res.md


def test_a_renumbered_set_citing_a_request_in_a_form_code_cannot_read_holds():
    """ "Request No. 2" in an interrogatory set: FALSIFIER, renumber around it and it goes stale."""
    md = set_md(texts=["A.", "B.", "C.", "D.", "Identify each witness to Request No. 4."])
    with pytest.raises(p.Held) as e:
        ps.revise_unit(md, unit(md), [p.Op("cut", "cut by 1", (), "", "", "by 1")], StandIn(ranking=[1]))
    assert e.value.code == "revise_scope_unclear"


def test_two_adds_on_one_set_each_get_their_own_answer():
    """FALSIFIER: key the answer by file, and the second ask reuses the first's requests."""
    md = set_md()
    o = pa.ModelOracle(lambda *a: "", "add two about the phone and two about the car")
    seen = []

    def ask(stage, system, user, max_tokens):
        seen.append(user)
        topic = "car" if "about the car" in user else "phone"  # the ask, not the set it already holds
        return json.dumps({"items": [f"Identify each {topic} record.", f"Describe each {topic} used."]})

    o.ask = ask
    ops = [
        p.Op("add", "add two about the phone", (), "", "", "two"),
        p.Op("add", "add two about the car", (), "", "", "two"),
    ]
    res = ps.revise_unit(md, unit(md), ops, o)
    texts = [it.text(res.md) for it in set_items.read(res.md, SROG)]
    assert any("phone record" in t for t in texts) and any("car record" in t for t in texts)
    assert len(seen) == 2


# ---- the firm's own layout (an invented set laid out as the firm files them) ---------------------------
def firm_md(texts: list[str] | None = None, decl: str | None = None) -> str:
    """Definitions under a bold title, each an UNNUMBERED paragraph opening with
    its term in curly quotes, the comma inside the closing quote."""
    texts = texts or [f"State fact number {n} about the INCIDENT." for n in range(1, 6)]
    head = [
        "PROPOUNDING PARTY: Plaintiff ALPHA EXAMPLE",
        "RESPONDING PARTY: Defendant DELTA EXAMPLE",
        "SET NO.: One",
        "**DEFINITIONS**",
        "\u201cIDENTIFY,\u201d when used with a person, means to state the full name.",
        "\u201cINCIDENT,\u201d means the event of June 1, 2026 in Exampletown.",
        "\u201cYOU,\u201d means the responding party.",
    ]
    body = []
    for n, t in enumerate(texts, 1):
        body += [f"**SPECIAL INTERROGATORY NO. {n}:**", t]
    tail = ["Dated: December 1, 2026", "EXAMPLE LAW, by Alpha Lawyer"]
    if decl:
        tail += ["DECLARATION FOR ADDITIONAL DISCOVERY", decl]
    return "\n\n".join(head + body + tail) + "\n"


def test_unnumbered_definitions_under_a_bold_title_take_a_new_one_in_their_form():
    """D1/D3. FALSIFIER: read only numbered definitions, or a closing quote with no
    comma before it, and every real set refuses the define."""
    md, why = p.define(firm_md(), "ACCOMMODATION(S)", '"ACCOMMODATION(S)" means any change made for a tenant.')
    assert why is None
    assert "\n\n\u201cACCOMMODATION(S),\u201d means any change made for a tenant.\n\n\u201cIDENTIFY,\u201d" in md
    assert "1. " not in md  # never numbered among unnumbered neighbours
    assert pd.defined(md) == ["ACCOMMODATION(S)", "IDENTIFY", "INCIDENT", "YOU"]


def test_a_term_the_email_misspells_is_defined_as_the_word_the_set_uses():
    """D2. FALSIFIER: require the email's exact spelling, and "ACCOMODATION(S)" is
    refused; take any spelling, and a word no one wrote is defined."""
    email = "Please define ACCOMODATION(S) in each set."
    ops, _other, problems = p.check_plan(
        {"ops": [{"op": "define", "quote": "define ACCOMODATION(S)", "term": "ACCOMMODATION(S)"}]}, email
    )
    assert not problems and ops[0].term == "ACCOMMODATION(S)"
    _o, _x, problems = p.check_plan(
        {"ops": [{"op": "define", "quote": "define ACCOMODATION(S)", "term": "HOUSING"}]}, email
    )
    assert problems
    md = firm_md(texts=["Identify each accommodation YOU gave.", "B.", "C.", "D.", "E."])
    res = ps.revise_unit(md, unit(md), ops, StandIn('"ACCOMMODATION(S)" means any change made.'))
    assert 'Added the definition of "ACCOMMODATION".' in res.lines  # D13: the set's definitions carry no "(S)"
    unused = firm_md()
    with pytest.raises(p.Held) as e:  # no set uses the corrected word: not confirmed
        ps.revise_all([unit(unused)], [unused], ops, [[0]], StandIn('"ACCOMMODATION(S)" means any change made.'))
    assert e.value.code == "revise_scope_unclear"


def test_every_use_and_plural_is_put_in_capitals_after_a_bold_titled_definitions():
    """D4. FALSIFIER: look for a heading-titled Definitions only, or match the
    singular only, and "accommodations" stays lower case in every request."""
    md = firm_md(
        texts=[
            "Identify every reasonable accommodations YOU made.",
            "State each accommodation requested in Example v. Accommodation Co.",
            'Describe the "accommodation" form.',
            "C.",
            "D.",
        ]
    )
    out = p.capitalize(md, ["ACCOMMODATION(S)"])
    assert "reasonable ACCOMMODATIONS YOU made" in out
    assert "each ACCOMMODATION requested in Example v. Accommodation Co." in out  # a case name is never touched
    assert '"accommodation" form' in out  # nor a quotation
    assert "\u201cIDENTIFY,\u201d when used" in out  # nor the Definitions


def test_a_declaration_saying_the_count_four_times_with_its_item_list_is_rewritten_whole():
    """D6. FALSIFIER: hold on more than one statement of the count, and every
    real set's declaration holds; rewrite the prose alone, and its list of item
    numbers still runs to the old count."""
    decl = (
        "I am serving 40 specially prepared interrogatories. Code of Civil Procedure section 2030.050 (40). "
        "This set of 40 is warranted. (E) The interrogatories are Nos. 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, "
        "14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39 "
        "and 40, and Nos. 1-40 in all."
    )
    out, line = pdc.declaration(firm_md(decl=decl), 40, 38, SROG)
    got = out[out.index("DECLARATION FOR") :]
    assert "40" not in got.replace("2030.050", "")
    assert got.count("38") == 5 and "are Nos. 1-38, and Nos. 1-38 in all" in got
    assert line == "the declaration's count of this set is now 38 (was 40)"
    with pytest.raises(p.Held):
        pdc.declaration(firm_md(decl="This set of forty (40) interrogatories."), 40, 38, SROG)


@pytest.mark.parametrize(
    "quote,count,want",
    [
        ("to the most important 120125", 193, 73),
        ("120-125", 193, 73),
        ("120125", 193, 73),
        ("120-125", 120, 0),  # already at the low end: unchanged
        ("120-125", 123, 0),  # D12: already inside the range: unchanged, as SMD's key leaves it
        ("120-125", 125, 0),
        ("120-125", 126, 6),  # over the top: cut to the low end
        ("120-125", 118, 0),
        ("5 or 6", 30, None),  # "or" with no "to": which is the attorney's
    ],
)
def test_the_attorneys_cut_words_read_as_a_target_at_the_low_end(quote, count, want):
    """D7. FALSIFIER: read the run-together or the bare range as nothing, or cut to
    its top, and the set ends at a size SMD never filed."""
    assert p.cut_drop(quote, count) == want


def test_a_second_emails_bare_range_takes_the_sets_the_first_named():
    """D7. FALSIFIER: scope the second email's "120-125" alone, and it names no set
    in a thread of two kinds: held, or cut everywhere."""
    us = [unit(firm_md()), p.read_unit("Requests for Admission, Set One.docx", RFA, set_md(RFA))]
    email = "Cut the special interrogatories to the most important 120125.\n\nSorry, I meant 120-125."
    plan = {
        "ops": [
            {"op": "cut", "quote": "Cut the special interrogatories to the most important 120125",
             "set_types": [SROG], "number_quote": "to the most important 120125"},
            {"op": "cut", "quote": "I meant 120-125", "number_quote": "120-125"},
        ],
        "other": [],
    }  # fmt: skip
    ops, scoped = pa.settle_plan(email, us, lambda *a: json.dumps(plan))
    assert scoped == [[0], [0]]


def test_follow_ups_go_directly_after_their_parents_and_everything_is_renumbered():
    """D9. FALSIFIER: append follow-ups at the end, or leave the numbers and the
    citations as they were, and each follow-up is cut off from the request it follows."""
    md = firm_md(texts=["One.", "Two.", "Three.", "Four.", "Five. See Special Interrogatory No. 5.", "Six."])

    class Follow(StandIn):
        def follow_ups(self, unit, md, variant, n, quote):
            self.calls.append(f"follow-ups:{n}")
            return [(2, "Identify each witness to YOUR answer to the one before."),
                    (2, "Identify each document."), (5, "State every fact.")]  # fmt: skip

    oracle = Follow()
    res = ps.revise_unit(md, unit(md), [p.Op("add", "add follow-up questions", (), "", "", "")], oracle)
    texts = [(it.number, it.text(res.md).split("\n\n", 1)[1]) for it in set_items.read(res.md, SROG)]
    assert [n for n, _t in texts] == list(range(1, 10))
    assert (
        texts[1][1] == "Two."
        and texts[2][1].startswith("Identify each witness")  # D13: the model's own words, not capitalised
        and texts[3][1] == "Identify each document."
    )
    assert texts[6][1] == "Five. See Special Interrogatory No. 7." and texts[7][1] == "State every fact."
    assert oracle.calls == ["follow-ups:None"]  # no number named: the model chooses
    assert "Added 3 follow-up special interrogatories, each after the one it follows (Nos. 3, 4, 8)." in res.lines
    assert res.numbers == {1: 1, 2: 2, 3: 5, 4: 6, 5: 7, 6: 9}


# ---- the real sets' second rerun (invented sets in the firm's layout) ----------------------------------
SIGNED = "| Dated: December 1, 2026 | EXAMPLE LAW By: Alpha Lawyer |"


def test_a_define_scoped_to_every_set_skips_a_set_that_never_uses_the_term():
    """N1. FALSIFIER: hold the job on the set that never says the word, and every
    reply defining a term for "each set" holds; skip every set, and nothing is defined."""
    uses = firm_md(texts=["Identify each VEHICLE YOU own.", "B.", "C.", "D.", "E."])
    plain = firm_md()
    us = [unit(uses), unit(plain, "Special Interrogatories, Set Two.docx")]
    ops = [p.Op("define", "define VEHICLE in each set", (), "", "VEHICLE")]
    out = ps.revise_all(us, [uses, plain], ops, [[0, 1]], StandIn('"VEHICLE" means any car.'))
    assert out[0].lines == ['Added the definition of "VEHICLE".']
    assert not out[1].changed and out[1].md == plain
    assert out[1].left == ['it does not use "VEHICLE", so no definition was added to it']


def test_a_signature_table_straight_after_the_last_request_is_the_sets_closing():
    """D11. FALSIFIER: read the signature row as the last request's second
    paragraph, and cutting the last request holds on every real set."""
    md = firm_md().replace("Dated: December 1, 2026\n\nEXAMPLE LAW, by Alpha Lawyer", SIGNED)
    res = ps.revise_unit(md, unit(md), [p.Op("cut", "cut by 1", (), "", "", "by 1")], StandIn(ranking=[5]))
    assert [it.number for it in set_items.read(res.md, SROG)] == [1, 2, 3, 4]
    assert "fact number 5" not in res.md and res.md.rstrip().endswith(SIGNED)
    by = md.replace(SIGNED, "By: Alpha Lawyer")
    res = ps.revise_unit(by, unit(by), [p.Op("cut", "cut by 1", (), "", "", "by 1")], StandIn(ranking=[5]))
    assert res.md.rstrip().endswith("By: Alpha Lawyer")


def test_a_new_definition_goes_last_among_definitions_not_in_alphabetical_order():
    """D13. FALSIFIER: insert alphabetically into a list the firm never ordered,
    and the new definition lands in the middle of someone else's sequence."""
    md = firm_md().replace("“IDENTIFY,”", "“ZONE,”")  # ZONE, INCIDENT, YOU: not in order
    out, why = p.define(md, "ACCOMMODATION", '"ACCOMMODATION" means any change.')
    assert why is None and pd.defined(out) == ["ZONE", "INCIDENT", "YOU", "ACCOMMODATION"]
    out, _why = p.define(firm_md(), "ACCOMMODATION", '"ACCOMMODATION" means any change.')
    assert pd.defined(out)[0] == "ACCOMMODATION"  # an alphabetical list keeps its order


def test_a_new_term_takes_the_sets_plural_form():
    """D13. FALSIFIER: always keep the email's "(S)", and a set that writes its
    definitions plain gains the one term in another form; always drop it, and a
    set that writes "(S)" loses it."""
    assert pd.term_form(firm_md(), "ACCOMMODATION(S)") == "ACCOMMODATION"
    marked = firm_md().replace("“INCIDENT,”", "“INCIDENT(S),”")
    assert pd.term_form(marked, "ACCOMMODATION(S)") == "ACCOMMODATION(S)"


def test_new_requests_keep_the_models_lower_case_and_only_the_new_term_is_capitalised():
    """D13. FALSIFIER: capitalise every defined word in the model's text, and a
    plain "identify" in a new request reads as the defined term."""
    md = firm_md(texts=["Identify each VEHICLE.", "B.", "C.", "D.", "E."])
    ops = [p.Op("define", "define VEHICLE", (), "", "VEHICLE"), p.Op("add", "add one more", (), "", "", "one more")]
    oracle = StandIn('"VEHICLE" means any car.', ["Please identify the vehicle you drove."])
    res = ps.revise_unit(md, unit(md), ops, oracle)
    added = set_items.read(res.md, SROG)[-1].text(res.md)
    assert "Please identify the VEHICLE you drove." in added


class _Follow(StandIn):
    def __init__(self, pairs: list[tuple[int, str]], ranking: list[int]) -> None:
        super().__init__(ranking=ranking)
        self.pairs = pairs
        self.families: list[Any] = []

    def follow_ups(self, unit, md, variant, n, quote):
        return list(self.pairs)

    def ranking(self, unit, md, variant, n, protected, families=None):
        self.families.append(dict(families or {}))
        return list(self._ranking)


def test_follow_ups_come_first_and_a_cut_takes_a_request_with_its_follow_ups():
    """D14. FALSIFIER: cut before the follow-ups, and the target ignores them (a
    set cut "to 6" ends at 7); cut a parent alone, and its follow-up is orphaned."""
    md = firm_md(texts=[f"Text {n}." for n in range(1, 7)])
    up = p.Op("add", "add follow-up questions", (), "", "", "")
    to6 = p.Op("cut", "cut to 6", (), "", "", "to 6")
    res = ps.revise_unit(md, unit(md), [up, to6], _Follow([(3, "Follow three.")], [2]))
    assert res.after == 6 and "Text 2." not in res.md and "Follow three." in res.md
    o = _Follow([(3, "Follow three.")], [3, 5])
    res = ps.revise_unit(md, unit(md), [up, p.Op("cut", "cut by 2", (), "", "", "by 2")], o)
    assert o.families == [{3: [4]}]
    texts = [it.text(res.md).split("\n\n", 1)[1] for it in set_items.read(res.md, SROG)]
    assert texts == ["Text 1.", "Text 2.", "Text 4.", "Text 5.", "Text 6."]  # No. 3 went with its follow-up
    assert "Removed former No. 3." in res.lines


def test_a_five_list_declaration_follows_a_follow_up_a_cut_and_the_renumbering():
    """D10. FALSIFIER: read the "(A)".."(E)" lists as one whole-set list, and every
    real declaration holds; leave them as written, and they name requests that
    moved or are gone."""
    decl = (
        "I am serving 40 specially prepared interrogatories. Code of Civil Procedure section 2030.050 (40). "
        "(A) Interrogatory Nos. 1-14, 16, 17 concern the INCIDENT. (B) Nos. 15, 18-20 concern damages. "
        "(C) Nos. 21 through 28 concern witnesses. (D) Nos. 29-38 concern records. (E) Nos. 39 and 40 concern costs."
    )
    md = firm_md(texts=[f"Text {n}." for n in range(1, 41)], decl=decl)
    o = _Follow([(2, "Follow two.")], [17, 22, 41])  # former 16, 21 and 40, numbered after the follow-up
    ops = [p.Op("add", "add follow-up questions", (), "", "", ""), p.Op("cut", "cut by 3", (), "", "", "by 3")]
    res = ps.revise_unit(md, unit(md), ops, o)
    got = res.md[res.md.index("DECLARATION FOR") :]
    assert res.after == 38
    assert "I am serving 38 " in got and "2030.050 (38)" in got
    assert "(A) Interrogatory Nos. 1-15, 17 concern" in got  # the follow-up joins its parent's list
    assert "(B) Nos. 16, 18-20 concern" in got
    assert "(C) Nos. 21 through 27 concern" in got  # former 21 cut, the rest one down
    assert "(D) Nos. 28-37 concern" in got
    assert "(E) Nos. 38 concern" in got  # former 40 cut, former 39 now 38
    assert "Removed former Nos. 16, 21, 40." in res.lines
