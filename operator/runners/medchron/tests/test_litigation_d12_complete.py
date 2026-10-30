"""The twelve sections completed from the status list (``discovery12/complete.py``).
Synthetic data only. 2026-10-30 independent review: the breakdown left served
dates blank and sets out that the status list held, and printed "None in file"
for sections the status list had."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from medchron.litigation import printout
from medchron.litigation.discovery12 import book as d12book, complete, sections

SRC = {"file_id": "f1", "name": "Def RFP set one.pdf", "doc_date": "2026-03-01"}


def _e(
    item: str, *, served: dict | None = None, party: str = "Acme Corp", direction: str = "served on the firm's client"
):
    return {"item": item, "direction": direction, "party": party, "served": served or {}, "due": {}, "response": {},
            "extension": {}, "notes": ""}  # fmt: skip


def _row(
    set_name: str, date: str | None, *, by: str = "Acme Corp", source: dict | None = SRC, resp: dict | None = None
):
    return {"set": set_name, "served_by": by, "date": date, "source": source, "responses_served": resp or {}}


def test_a_blank_served_date_is_filled_from_the_status_list_with_its_source():
    d12 = {"read": True, "sections": {"rfps": [_e("Requests for Production, Set One")]}}
    got = complete.merge({"discovery_served_on_client": [_row("Requests for Production, Set One", "2026-03-01")]}, d12)
    e = got["sections"]["rfps"][0]
    assert e["served"] == {"date": "2026-03-01", "source": SRC, "from_status": True}
    assert complete.FILLED_NOTE in e["notes"] and got["completed"]["filled"] == 1


def test_a_breakdown_value_with_its_own_paper_is_never_overwritten():
    own = {"date": "2026-02-01", "source": {"file_id": "f9", "name": "own.pdf"}}
    d12 = {"read": True, "sections": {"rfps": [_e("RFP Set One", served=own)]}}
    got = complete.merge({"discovery_served_on_client": [_row("Requests for Production, Set One", "2026-03-01")]}, d12)
    assert got["sections"]["rfps"][0]["served"] == own and got["completed"] == {
        "filled": 0,
        "added": 0,
        "unplaced": 0,
    }


def test_a_set_the_breakdown_printed_twice_is_filled_in_both_rows():
    d12 = {"read": True, "sections": {"srogs": [_e("Special Interrogatories, Set One"), _e("SROGs Set One")]}}
    got = complete.merge({"discovery_served_on_client": [_row("Special Interrogatories, Set One", "2026-05-01")]}, d12)
    assert [e["served"].get("date") for e in got["sections"]["srogs"]] == ["2026-05-01", "2026-05-01"]


def test_two_different_sets_tied_for_the_best_match_are_not_filled():
    d12 = {"read": True, "sections": {"srogs": [_e("SROGs", party="Acme Corp"), _e("SROGs", party="Beta Acme LLC")]}}
    got = complete.merge(
        {"discovery_served_on_client": [_row("Special Interrogatories", "2026-05-01", by="Acme")]}, d12
    )
    assert [e["served"].get("date") for e in got["sections"]["srogs"]] == [None, None]
    assert got["completed"] == {"filled": 0, "added": 0, "unplaced": 0}


def test_a_blank_row_of_a_set_dated_on_another_row_still_gets_its_status_date():
    """2026-10-30 targeted check: the stricter twin rule (#3641) left blank
    every row of a set the breakdown had dated on another row, and 261 served
    dates printed nowhere. The 09:06 merge fills them; the contradicted status
    values the reviewers listed are removed by an explicit list instead."""
    own = {"date": "2026-05-01", "source": {"file_id": "f9", "name": "own.pdf"}}
    d12 = {
        "read": True,
        "sections": {"srogs": [_e("Special Interrogatories, Set One", served=own), _e("SROGs Set One")]},
    }
    got = complete.merge({"discovery_served_on_client": [_row("Special Interrogatories, Set One", "2026-05-01")]}, d12)
    assert got["sections"]["srogs"][0]["served"] == own
    assert got["sections"]["srogs"][1]["served"]["date"] == "2026-05-01" and got["completed"]["filled"] == 1


def test_an_empty_section_whose_kind_a_paper_names_prints_a_hand_check_not_none_in_file(tmp_path: Path):
    """Second review, 2026-10-30: three sections printed None in file beside a
    paper of that kind."""
    d12 = complete.merge({}, {"read": True, "sections": {}}, {"depositions": ["Notice of depo.pdf"], "frogs": ["x"]})
    d12["sections"]["frogs"] = [_e("Form Interrogatories, Set One")]
    d12 = complete.merge({}, d12, {"depositions": ["Notice of depo.pdf"], "frogs": ["x"]})
    assert set(d12["kind_hits"]) == {"depositions"}
    out, shown = _shown(tmp_path, {**d12, "read": True})
    text = " ".join("\n".join(printout.text_of(out)).split())
    assert f"{sections.LABEL['depositions']} {complete.KIND_HIT} Notice of depo.pdf" in text
    rows = d12book.rows([{"number": "1001", "title": "A v. B", "responsible": "Lee", "read": True, **d12}])
    assert [r[4] for r in rows if r[3] == sections.LABEL["depositions"]] == [f"{complete.KIND_HIT} Notice of depo.pdf"]


def test_an_unconfirmed_breakdown_date_is_not_replaced():
    unc = {"date": None, "unconfirmed": True, "source": {"file_id": "f9", "name": "own.pdf"}}
    d12 = {"read": True, "sections": {"rfps": [_e("RFP Set One", served=unc)]}}
    got = complete.merge({"discovery_served_on_client": [_row("Requests for Production, Set One", "2026-03-01")]}, d12)
    assert got["sections"]["rfps"][0]["served"] == unc


def test_a_set_the_breakdown_omitted_is_added_and_marked():
    d12 = {"read": True, "sections": {"rfps": [_e("RFP Set One")]}}
    prior = {"discovery_served_on_client": [_row("Requests for Production, Set Two", "2026-04-01")]}
    got = complete.merge(prior, d12)
    added = got["sections"]["rfps"][1]
    assert added["item"] == "Requests for Production, Set Two" and added["notes"] == complete.STATUS_NOTE
    assert added["served"]["date"] == "2026-04-01" and got["completed"]["added"] == 1


def test_a_status_value_with_no_paper_says_so():
    d12 = {"read": True, "sections": {}}
    got = complete.merge({"discovery_propounded": [{"set": "Form Interrogatories, Set One", "served_on": "Acme",
                                                    "date": "2026-01-05", "source": None}]}, d12)  # fmt: skip
    assert got["sections"]["frogs"][0]["served"]["source"] == complete.NO_PAPER


def test_an_unnamed_kind_and_an_unread_case_go_to_the_status_block_never_dropped():
    got = complete.merge({"discovery_served_on_client": [_row("Discovery, Set One", "2026-01-01")]}, {"read": True})
    assert got["status_rows"] and got["completed"]["unplaced"] == 1
    unread = complete.merge({"discovery_served_on_client": [_row("RFP Set One", "2026-01-01")]}, {"read": False})
    assert len(unread["status_rows"]) == 1 and not unread["sections"].get("rfps")


def _shown(tmp_path: Path, d12: dict) -> tuple[Path, list[dict]]:
    m = {"matter_id": "m1", "number": "1001", "case_name": "A v. B", "title": "A v. B", "responsible": "Lee",
         "court_dates": {"rows": [], "read": True}, "next_court_date": {}, "discovery12": d12}  # fmt: skip
    out = tmp_path / "x.pdf"
    printout.build([m], out, dt.date(2026, 10, 30))
    return out, [m]


def test_none_in_file_prints_only_when_neither_source_has_the_section(tmp_path: Path):
    prior = {"discovery_served_on_client": [_row("Requests for Production, Set One", "2026-03-01")]}
    d12 = complete.merge(prior, {"read": True, "sections": {}})
    out, shown = _shown(tmp_path, {**d12, "read": True})
    text = " ".join("\n".join(printout.text_of(out)).split())
    assert "2026-03-01" in text and complete.STATUS_NOTE in text
    assert f"{sections.LABEL['rfps']} {sections.NONE_IN_FILE}" not in text
    assert f"{sections.LABEL['frogs']} {sections.NONE_IN_FILE}" in text
    assert printout.check(out, shown) == []
    rows = d12book.rows([{"number": "1001", "title": "A v. B", "responsible": "Lee", "read": True, **d12}])
    assert [r[3] for r in rows if r[4] == sections.NONE_IN_FILE].count(sections.LABEL["rfps"]) == 0


def test_a_declined_case_still_prints_what_the_status_list_holds(tmp_path: Path):
    prior = {"discovery_served_on_client": [_row("Requests for Production, Set One", "2026-03-01")]}
    d12 = {**complete.merge(prior, {"read": True, "declined": True}), "declined": True, "read": True}
    out, shown = _shown(tmp_path, d12)
    text = " ".join("\n".join(printout.text_of(out)).split())
    assert printout.D12_DECLINED in text and "2026-03-01" in text and complete.STATUS_LABEL in text
    assert printout.check(out, shown) == []
    rows = d12book.rows([{"number": "1001", "title": "A v. B", "responsible": "Lee", "read": True, **d12}])
    assert rows[0][4] == d12book.DECLINED and any(r[3] == complete.STATUS_LABEL for r in rows)
