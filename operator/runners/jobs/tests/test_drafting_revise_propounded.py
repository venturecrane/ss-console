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
from jobs.drafting.revise import propounded as p, propounded_ask as pa

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

    def ranking(self, unit: str, md: str, variant: str, n: int, protected: set[int]) -> list[int]:
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
        ("trim to 120125", 100, None),  # neither number is within the count: never a guess
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
    assert p.cut_drop("down to 120-125", 150) == 25
    assert p.cut_drop("cut by 10", 40) == 10
    assert p.cut_drop("to 40", 40) == 0  # already there: nothing removed
    assert p.cut_drop("to 50", 40) is None  # a target over the count is not a cut: never a guess
    assert p.cut_drop("10", 40) is None  # to ten or by ten? never a guess
    assert p.cut_drop("to 0", 40) is None  # a cut never empties a set


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
    assert p.scope(role, us, "define VEHICLE for the defendant") == [0, 1, 2]


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
    out, line = p.declaration(md, 40, 38, SROG)
    assert (
        "a total of 38 specially prepared" in out and line == "the declaration's count of this set is now 38 (was 40)"
    )
    assert out.replace("38 specially", "40 specially") == md  # nothing else


@pytest.mark.parametrize("old,new", [(30, 40), (40, 30)])
def test_crossing_35_either_way_holds_for_smd(old, new):
    with pytest.raises(p.Held) as e:
        p.declaration(set_md(decl=DECL), old, new, SROG)
    assert e.value.code == "revise_declaration"


def test_an_old_count_not_in_the_declaration_exactly_once_holds():
    twice = set_md(decl="I previously served 40. This set contains a total of 40 interrogatories.")
    with pytest.raises(p.Held) as e:
        p.declaration(twice, 40, 38, SROG)
    assert e.value.code == "revise_declaration"
    with pytest.raises(p.Held):
        p.declaration(set_md(), 40, 38, SROG)  # over 35 and no declaration found


def test_production_sets_carry_no_count_to_rewrite():
    md = set_md(RFP, decl=DECL)
    assert p.declaration(md, 40, 30, RFP) == (md, None)


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
    res = p.revise_unit(md, u, ops, oracle)
    items = [it.text(res.md) for it in set_items.read(res.md, SROG)]
    assert [it.number for it in set_items.read(res.md, SROG)] == [1, 2, 3]
    assert "About the VEHICLE." in items[0] and "About the INCIDENT." in items[1] and "VEHICLE trip" in items[2]
    assert (res.before, res.after) == (4, 3)
    assert res.lines[0] == 'Added the definition of "VEHICLE".'
    assert "Added 2 special interrogatories, Nos. 5, 6." in res.lines
    assert "Removed former Nos. 2, 3, 5." in res.lines
    assert any("former No. 4 is now No. 2, former No. 6 is now No. 3" in ln for ln in res.lines)
    assert oracle.calls == ["definition:VEHICLE", f"additions:{u.name}:2", f"ranking:{u.name}:3"]
    lines = p.summary(u.name, res, SROG)
    assert lines[0] == f"{u.name} (4 special interrogatories, now 3):"


def test_requests_are_added_to_special_interrogatories_only():
    md = set_md(RFA)
    res = p.revise_unit(md, unit(md, hint=RFA), [p.Op("add", "add five", (), "", "", "five")], StandIn())
    assert not res.changed and "special interrogatories only" in res.left[0]


def test_another_sets_citation_is_never_cut_and_is_named_when_it_moves():
    """An SROG cites "Request for Admission No. 3": FALSIFIER, cut it, or move it
    silently, and the SROG file asks about a request that is gone or renumbered."""
    srog = set_md(texts=["State all facts for your denial of Request for Admission No. 3."])
    rfa = set_md(RFA)
    us = [unit(srog), p.read_unit("Requests for Admission, Set One.docx", RFA, rfa)]
    foreign = p.foreign_refs(us, [srog, rfa], 1)
    assert foreign == {3: [us[0].name]}
    res = p.revise_unit(rfa, us[1], [p.Op("cut", "cut by 2", (), "", "", "by 2")], StandIn(ranking=[3, 1, 2]), foreign)
    assert [it.number for it in set_items.read(res.md, RFA)] == [1, 2]
    assert "Removed former Nos. 1, 2." in res.lines
    assert any("cites request for admission No. 3, now No. 1" in ln for ln in res.lines)
