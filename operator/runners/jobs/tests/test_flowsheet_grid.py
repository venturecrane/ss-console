"""A flowsheet's time rows are read by code, from the page and its neighbours
(2026-11-06, ``medchron/flowsheet_grid.py``).

A real update held because its drafter and its audit read one flowsheet
page's time rows as footers of the values above them; each is the header of
the values below it. The independent review was right, the audit reverted the
finisher's correct edit, and SMD corrected the sentence by hand. The cited
page alone fits both readings (every block five wide); its neighbours, whose
blocks run short, decide it. Every name, time and value here is synthetic,
shaped like the three real pages.
"""

from __future__ import annotations


from jobs.medchron import flowsheet_grid as G


def _t(*times: str) -> str:
    return "Row Name    " + "    ".join(times)


def _v(*vals: int) -> str:
    return "OTHER\nSample Early Warning Score    " + "    ".join(f"{v} -ZZ" for v in vals)


#: before the cited page: blocks of five, then a block of one (decides: header above)
P1 = "\n\n".join(
    ["Flowsheets - Other", _t("0800"), _v(5), _t("2345", "2330", "2315", "2300", "2245"), _v(3, 3, 3, 3, 3)]
    + [_t("1845", "1830", "1815", "1800", "1745"), _v(3, 3, 3, 3, 3), _t("1730", "1715", "1700", "1645", "1631")]
)
#: the cited page: every block five wide (fits both readings); the machine
#: transcription printed each score line ABOVE its time line
P2 = "\n".join(
    [
        _v(3, 3, 3, 3, 3),
        _t("1630", "1615", "1600", "1545", "1530"),
        _v(3, 3, 3, 3, 3),
        _t("1515", "1500", "1445", "1430", "1415"),
        _v(3, 3, 3, 3, 7),
        _t("1400", "1345", "1330", "1315", "1300"),
        _v(7, 7, 7, 7, 7),
        _t("1245", "1230", "1215", "1200", "1145"),
    ]
)
#: after it: five wide, then a block of one (decides: header above)
P3 = "\n".join([_v(7, 7, 7, 7, 7), _t("1130", "1115", "1100", "1045", "1030"), _v(7, 7, 7, 7, 7), _t("1015"), _v(7)])
PAGES = {1: P1, 2: P2, 3: P3}

#: the drafter's reading (time rows as footers) and the page's
WRONG = "The score was 7 at each entry from 1145 through 1300 and 3 at each entry from 1315 through 1630."
RIGHT = "The score was 7 at each entry from 1300 through 1415 and 3 at each entry from 1430 through 1630."
CITE = " (Exhibit 2 - p. 2)"


def _ev(pages: dict[int, str]) -> G.Evidence:
    return lambda ex, p: pages.get(p) if ex == 2 else None


# ---- the check -------------------------------------------------------------------------
def test_the_cited_page_fits_both_readings_and_its_neighbours_decide_header_above() -> None:
    assert G.vote(P2) is None and G.vote(P1) is True and G.vote(P3) is True
    assert G.orientation(P2, [P1, P3]) is True
    tab = dict(G.table(P2, True))
    assert tab["1415"] == "7" and tab["1430"] == "3" and "1145" not in tab  # 1145's values are on the next page


def test_the_drafters_footer_reading_is_corrected_to_the_pages_span() -> None:
    assert G.check(WRONG + CITE, _ev(PAGES), 2, 2) == RIGHT + CITE
    assert G.agrees(RIGHT + CITE, _ev(PAGES), 2, 2) and not G.agrees(WRONG + CITE, _ev(PAGES), 2, 2)
    assert G.check(RIGHT + CITE, _ev(PAGES), 2, 2) is None  # already the page's: nothing changes


#: the same cited page between neighbours whose short blocks print each time row
#: BELOW its values (footers): there the drafter's reading is the page's
F1 = "\n".join(
    [_v(5), _t("0800"), _v(3, 3, 3, 3, 3), _t("2345", "2330", "2315", "2300", "2245")]
    + [_v(3, 3, 3, 3, 3), _t("1845", "1830", "1815", "1800", "1745")]
)
F3 = "\n".join(
    [_v(7, 7, 7, 7, 7), _t("1130", "1115", "1100", "1045", "1030"), _v(7, 7, 7, 7, 7)]
    + [_t("1015", "1000", "0945", "0930", "0915"), _v(7), _t("0900")]
)


def test_neighbours_that_print_time_rows_below_their_values_read_the_page_as_footers() -> None:
    footers = {1: F1, 2: P2, 3: F3}
    assert G.vote(F1) is False and G.vote(F3) is False and G.reading(_ev(footers), 2, 2) is False
    assert G.check(RIGHT + CITE, _ev(footers), 2, 2) == WRONG + CITE
    assert G.check(WRONG + CITE, _ev(footers), 2, 2) is None


def test_falsifier_a_page_nothing_decides_changes_nothing() -> None:
    assert G.check(WRONG + CITE, _ev({2: P2}), 2, 2) is None  # no neighbours: undecided
    flipped = "\n".join(reversed(P1.split("\n")))  # a neighbour that votes the other way
    assert G.vote(flipped) is False
    assert G.check(WRONG + CITE, _ev({1: flipped, 2: P2, 3: P3}), 2, 2) is None
    plain = "The score was 7 at the last check. (Exhibit 2 - p. 2)"
    assert G.check(plain, _ev(PAGES), 2, 2) is None  # no stated span: nothing to set
