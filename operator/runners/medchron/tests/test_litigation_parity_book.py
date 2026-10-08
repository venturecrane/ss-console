"""Parity against the last list, and the workbook: its tabs, its responsible
column, clean(), and a leak scan that passes on what book builds."""

from __future__ import annotations

import copy
import datetime as dt

from openpyxl import load_workbook

from medchron.litigation import book, gates, parity, vocab
from test_litigation_gates import good

TODAY = dt.date(2026, 10, 7)


def test_an_overturn_that_cites_a_document_is_explained():
    prior = good()
    new = copy.deepcopy(prior)
    new["defendants"][0]["served"]["date"] = "2026-04-09"
    ov = [{"path": "defendants[Delta Example].served", "source": {"file_id": "f1"}}]
    cs = parity.compare(prior, new, moved_files=set(), overturns=ov, today=TODAY)
    assert cs and not parity.unexplained(cs)


def test_the_same_papers_with_a_different_answer_hold():
    prior = good()
    new = copy.deepcopy(prior)
    new["defendants"][0]["status"] = vocab.SERVED_NO_ANSWER
    new["defendants"][0]["answered"] = {"date": None, "source": None}
    bad = parity.unexplained(parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY))
    assert {c["path"] for c in bad} >= {"defendants[Delta Example].answered"}


def test_a_new_document_explains_its_value_and_a_passed_court_date_moves_on():
    prior = good()
    prior["next_court_date"]["date"] = "2026-09-01"
    new = copy.deepcopy(prior)
    new["next_court_date"] = {"date": "2026-12-01", "source": {"file_id": "fnew"}}
    cs = parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY)
    assert not parity.unexplained(cs)
    new["case_status"] = {"value": vocab.SETTLED_OWED, "source": {"file_id": "fmail"}}
    assert not parity.unexplained(parity.compare(prior, new, moved_files={"fmail"}, overturns=[], today=TODAY))
    assert parity.unexplained(parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY))


def test_a_new_matter_is_one_explained_change():
    cs = parity.compare(None, good(), moved_files=set(), overturns=[], today=TODAY)
    assert cs == [{"path": "matter", "old": None, "new": "added to the list", "explained": True, "why": "new matter"}]


def test_the_workbook_has_every_tab_and_the_rosters_name(tmp_path):
    a = good()
    b = copy.deepcopy(good())
    b.update(matter_id="m2", number="100002", responsible="Beta Example")
    b["case_status"]["value"] = vocab.SETTLED_OWED
    b["defendants"][0]["status"] = vocab.SERVED_NO_ANSWER
    b["defendants"][0]["answered"] = {"date": None, "source": None}
    b["discovery_served_on_client"] = [
        {
            "set": "Special interrogatories, set one",
            "served_by": "Delta Example",
            "date": "2026-07-01",
            "source": {"file_id": "f9", "name": "SROGS.pdf", "doc_date": None},
            "responses_served": {"value": "no", "date": None, "source": None},
        }
    ]
    c = copy.deepcopy(good())
    c.update(matter_id="m3", number="100003", responsible="Alpha Example", defendants=[])
    c["case_status"]["value"] = vocab.NOT_FILED
    c["notes"] = "Demand stage; no complaint (second read confirmed, see 4de824d4 results.json)"
    out = tmp_path / "w.xlsx"
    stats = book.build(
        [a, b, c],
        {
            "m1": [
                {
                    "path": "defendants[Delta Example].served",
                    "old": "2026-04-09",
                    "new": "2026-04-10",
                    "explained": True,
                    "why": "x",
                }
            ]
        },
        out,
        TODAY,
    )
    wb = load_workbook(out)
    assert wb.sheetnames == [
        "Summary",
        "Needs action",
        "Settled - dismissal owed",
        "All defendants",
        "Discovery",
        "Case notes",
        "Not filed",
        "What changed",
    ]
    assert stats["needs_action"] == 0  # the settled matter's defendant is owed a dismissal, not an answer
    assert [r[0].value for r in wb["Summary"].iter_rows(min_row=2)] == ["Alpha Example", "Beta Example"]
    assert wb["Settled - dismissal owed"].max_row == 2
    disc = [[c.value for c in r] for r in wb["Discovery"].iter_rows(min_row=2)]
    assert any(r[3] == "Served on the firm's client" and r[8] == "no" for r in disc)
    assert any(r[3] == "Propounded by the firm" for r in disc)
    nf = wb["Not filed"]["D2"].value
    assert "second read" not in nf and "4de824d4" not in nf and "results.json" not in nf
    assert wb["What changed"]["D2"].value == "Delta Example: served"
    assert gates.leak_scan(out) == []


def test_needs_action_lists_an_unanswered_defendant_in_an_active_case(tmp_path):
    a = good()
    a["defendants"][0].update(status=vocab.SERVED_NO_ANSWER, answered={"date": None, "source": None})
    assert book.build([a], {}, tmp_path / "w.xlsx", TODAY)["needs_action"] == 1


def test_clean_strips_internal_vocabulary():
    s = book.clean(
        "Answer filed (second read: verifier saw it) see [doc 4] id 11111111-1111-4111-8111-111111111111 — ok"
    )
    for bad in ("second read", "verifier", "[doc", "11111111-", "—"):
        assert bad not in s
