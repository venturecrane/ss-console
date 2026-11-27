"""Falsifiers for the 2026-11-26 census replay (every name invented).

D5: a request to "the <Name> Middle School and <Name> School District", the
caption printing the district with words the request left out, planned sets to
the middle school only, with no stop. Now the short spelling names the one
party it fits (``party_phrase.hits``), and a second party phrase that names no
caption party stops ``party_unknown`` (``party_phrase.unnamed``).

D10: "to the defense" on a caption ending "BETA LLC and DOES 1-25, Inclusive"
planned a third party, the Doe defendants, so three extra sets.
"""

from __future__ import annotations

import pytest

from jobs.drafting import parties

SCHOOL = "OAKRIDGE MIDDLE SCHOOL"
ASK = "Please prepare sets of discovery to the Oakridge Middle School and Oakridge School District."


@pytest.mark.parametrize(
    "district",
    [
        "OAKRIDGE CITY SCHOOL DISTRICT",
        "RIVERTON CITY OAKRIDGE UNIFIED SCHOOL DISTRICT",
        "OAKRIDGE CITY ELEMENTARY SCHOOL DISTRICT",
    ],
)
@pytest.mark.parametrize("ask", [ASK, ASK.replace("School District", "school district")])
def test_the_middle_school_and_the_districts_short_form_name_both_parties(district, ask):
    """FALSIFIER: on e50de3a32 each planned [SCHOOL] only, silently."""
    assert parties.resolve(ask, [SCHOOL, district]).planned == [SCHOOL, district]


def test_a_second_party_phrase_naming_no_caption_party_stops_never_plans_one():
    """FALSIFIER: on e50de3a32 this planned [SCHOOL]: the attorney got half."""
    ask = "Please prepare sets of discovery to the Oakridge Middle School and Riverton School District."
    with pytest.raises(parties.PartyUnknown, match='"Riverton School District".*fewer parties'):
        parties.resolve(ask, [SCHOOL, "OAKRIDGE CITY SCHOOL DISTRICT"])


def test_a_short_form_two_parties_fit_stops():
    values = [SCHOOL, "OAKRIDGE CITY SCHOOL DISTRICT", "OAKRIDGE VALLEY SCHOOL DISTRICT"]
    with pytest.raises(parties.PartyUnknown, match="could be"):
        parties.resolve(ASK, values)


def test_a_short_form_never_names_a_party_of_another_kind():
    """ "Oakridge High School" is not the middle school: every word must be in the caption's name."""
    with pytest.raises(parties.PartyUnknown):
        parties.resolve("Please prepare discovery to Oakridge High School and Oakridge School District.", [
            SCHOOL, "OAKRIDGE CITY SCHOOL DISTRICT"])  # fmt: skip


@pytest.mark.parametrize(
    "ask",
    [
        "Please prepare discovery to the Oakridge Middle School and send a copy to me.",
        "Please prepare RFAs and RFPs to the Oakridge Middle School and the plaintiff's counsel.",
        "Please prepare discovery to the Oakridge Middle School, a public entity.",
        "SROGs to the Oakridge Middle School about the fall and the injuries.",
    ],
)
def test_words_after_a_named_party_that_are_no_party_never_stop(ask):
    assert parties.resolve(ask, [SCHOOL, "OAKRIDGE CITY SCHOOL DISTRICT"]).planned == [SCHOOL]


# ---- D10: fictitious defendants after "and" inside one caption piece ----------------------


@pytest.mark.parametrize(
    "value",
    [
        "ALPHA CORP, BETA LLC and DOES 1-25, Inclusive",
        "ALPHA CORP, BETA LLC and Does 1 through 50, inclusive",
        "ALPHA CORP, BETA LLC and ROES 1-10",
    ],
)
def test_the_defense_plans_the_two_real_parties_never_the_does(value):
    """FALSIFIER: on e50de3a32 the Doe range was a third party (9 sets, not 6)."""
    assert parties.entities(value) == ["ALPHA CORP", "BETA LLC"]
    assert parties.resolve("Please prepare discovery to the defense.", [value]).planned == ["ALPHA CORP", "BETA LLC"]
