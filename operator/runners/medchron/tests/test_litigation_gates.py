"""Every gate can fail: one passing matter, then one planted violation per rule."""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from litigation_testkit import make_inputs
from medchron.litigation import gates, vocab
from medchron.litigation.firm import load

TODAY = dt.date(2026, 10, 7)


def src(fid: str = "f1", name: str = "POS Delta.pdf") -> dict:
    return {"file_id": fid, "name": name, "doc_date": None}


def good() -> dict:
    return {
        "matter_id": "m1",
        "number": "100001",
        "responsible": "Alpha Example",
        "case_status": {"value": vocab.ACTIVE, "source": src("f0", "Complaint.pdf")},
        "complaint_filed": {"date": "2026-03-02", "source": src("f0", "Complaint.pdf")},
        "next_court_date": {"date": "2026-11-20", "event": "CMC", "source": src("f2", "Notice of CMC.pdf")},
        "defendants": [
            {
                "name": "Delta Example",
                "status": vocab.ANSWERED,
                "served": {"date": "2026-04-10", "method": "personal", "source": src()},
                "answered": {"date": "2026-04-30", "source": src("f3", "Answer.pdf")},
                "flags": [],
            }
        ],
        "discovery_propounded": [
            {
                "set": "Form interrogatories, set one",
                "served_on": "Delta Example",
                "date": "2026-06-01",
                "source": src("f4", "FROGS.pdf"),
            }
        ],
        "discovery_served_on_client": [],
        "matter_flags": [],
        "integrity": [],
        "settlement_reviewed": [],
    }


FILES = {"m1": {"f0", "f1", "f2", "f3", "f4", "f5"}}
TEXTS = {
    "f0": "COMPLAINT filed 03/02/2026",
    "f1": "served on April 10, 2026",
    "f2": "CMC set for 11/20/2026",
    "f3": "Answer filed 4/30/2026",
    "f4": "served 06/01/2026",
}


def run(ms, saved=None, hits=None):
    return gates.run(
        ms,
        today=TODAY,
        files=FILES,
        saved=saved or {"m1": {}},
        text_of=lambda mid, fid: TEXTS.get(fid),
        hits=hits or {},
    )


def test_a_clean_matter_passes():
    assert run([good()]) == {"passed": True, "issues": {}}


def _set(m, path, value):
    *head, last = path
    cur = m
    for k in head:
        cur = cur[k]
    cur[last] = value
    return m


CASES = [
    ("case status not normalized", ("case_status", "value"), "Open-ish"),
    ("no responsible person", ("responsible",), ""),
    ("filed case with no complaint date", ("complaint_filed", "date"), None),
    ("future date", ("defendants", 0, "served", "date"), "2027-01-01"),
    ("date not YYYY-MM-DD", ("complaint_filed", "date"), "3/2/2026"),
    ("next court date is in the past (active case)", ("next_court_date", "date"), "2026-09-01"),
    ("no defendants", ("defendants",), []),
    ("defendant status not normalized", ("defendants", 0, "status"), "Answered (probably)"),
    ("date without source", ("defendants", 0, "answered", "source"), None),
    ("case status without source", ("case_status", "source"), None),
    ("answered but no answer date", ("defendants", 0, "answered"), {"date": None, "source": None}),
    ("answer date but status says no answer", ("defendants", 0, "status"), vocab.SERVED_NO_ANSWER),
    ("served date but status says not served", ("defendants", 0, "status"), vocab.NOT_SERVED),
    ("em dash in text", ("matter_flags",), ["served — no answer"]),
    ("source is not a file on the matter", ("complaint_filed", "source"), src("zz", "Elsewhere.pdf")),
    ("future date", ("discovery_propounded", 0, "date"), "2026-12-01"),
]


@pytest.mark.parametrize("rule,path,value", CASES)
def test_each_rule_refuses_its_violation(rule, path, value):
    m = _set(copy.deepcopy(good()), path, value)
    g = run([m])
    assert not g["passed"] and rule in g["issues"], g


def test_a_duplicate_matter_is_refused():
    assert "duplicate matter" in run([good(), good()])["issues"]


def test_a_uim_only_matter_needs_no_complaint_date():
    m = good()
    m["complaint_filed"] = {"date": None, "source": None}
    m["defendants"][0].update(
        status=vocab.UIM, answered={"date": None, "source": None}, served={"date": None, "source": None}
    )
    assert "filed case with no complaint date" not in run([m])["issues"]


def test_a_save_date_the_document_does_not_carry_is_refused():
    m = good()
    m["defendants"][0]["served"]["date"] = "2026-04-11"  # the day Smokeball saved the POS
    g = run([m], saved={"m1": {"f1": {"2026-04-11"}}})
    assert "date is the Smokeball save date, not in the document" in g["issues"]
    # the same date is fine when the document itself carries it
    TEXTS["f1b"] = "served on 04/11/2026"
    FILES["m1"].add("f1b")
    m["defendants"][0]["served"]["source"] = src("f1b")
    assert run([m], saved={"m1": {"f1b": {"2026-04-11"}}})["passed"]


def test_an_unreadable_file_must_be_named_on_the_matter():
    m = good()
    m["integrity"] = [{"file_id": "f5", "name": "Old answer.doc", "problem": "doc_unreadable"}]
    assert "unreadable or missing file not named on the matter" in run([m])["issues"]
    m["matter_flags"] = ["The file 'Old answer.doc' could not be read; it could not be checked."]
    assert run([m])["passed"]


def test_an_unresolved_settlement_hit_is_refused_on_an_open_matter(tmp_path):
    firm = load(make_inputs(tmp_path / "in"))
    hits = {"m1": gates.settlement_hits({"f5": "Good news: the case has settled for the policy limit."}, firm)}
    assert hits == {"m1": ["f5"]}
    assert "settlement-scan hit not resolved" in run([good()], hits=hits)["issues"]
    m = good()
    m["settlement_reviewed"] = [{"file_id": "f5", "finding": "a different case"}]
    assert run([m], hits=hits)["passed"]
    closed = good()
    closed["case_status"]["value"] = vocab.DISMISSED
    assert "settlement-scan hit not resolved" not in run([closed], hits=hits)["issues"]


@pytest.mark.parametrize(
    "text,what",
    [
        ("served, see 4de824d4 for proof", "hex id"),
        ("confirmed on second read", "internal process wording"),
        ("the verifier disagreed", "internal process wording"),
        ("download returns NoSuchKey", "storage error"),
        ("see results.json", "internal file name"),
        ("per [doc 12]", "doc reference"),
        ("body was cp1252", "encoding jargon"),
        ("Normalized status note: x", "status-note jargon"),
        ("served — no answer", "em dash"),
        ("id 11111111-1111-4111-8111-111111111111", "uuid"),
    ],
)
def test_the_leak_scan_finds_each_kind_in_a_built_workbook(tmp_path, text, what):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Notes"])
    wb.active.append([text])
    p = tmp_path / "w.xlsx"
    wb.save(p)
    assert what in [w for _c, w, _m in gates.leak_scan(p)]


def test_the_leak_scan_passes_plain_client_copy(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Served 2026-04-10 (personal), Case No. CV-0001, CMC 11/20/2026"])
    p = tmp_path / "w.xlsx"
    wb.save(p)
    assert gates.leak_scan(p) == []
