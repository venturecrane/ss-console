"""A3 (2026-11-23): the Index of Exhibits in a production response, by code.

``exhibit_plan`` places each pulled file in an exhibit from the firm's table;
``exhibit_index`` reads the letters each response produces from, feeds a
response that names an empty exhibit to the converge loop as a finding, and
writes the index region. Every file name, number and pattern is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs.drafting import compose, endings, exhibit_index as xi, exhibit_plan as xp, response_scope as rs
from jobs.drafting.replay import compare, key as key_mod

TABLE = {
    "letters": [
        {"letter": "A", "title": "Medical Records", "categories": ["medical_records"]},
        {"letter": "B", "title": "MEDICAL BILLS", "categories": ["billing"]},
        {"letter": "D", "title": "INCIDENT DOCUMENTS", "categories": ["incident_report"], "names": [r"(?i)scene"]},
        {"letter": "E", "title": "WAGE LOSS", "categories": ["employment_wage"]},
    ],
    "never": [
        {"reason": "an insurance letter", "names": [r"(?i)letter.*(farmers|insur)"]},
        {"reason": "correspondence is never an exhibit", "categories": ["correspondence"]},
    ],
}
FIRM = {"response_scope": {"exhibits": TABLE}}
FILES = [
    {"id": "1", "name": "Valley Ortho records.pdf", "folder": "/Medical/Valley Ortho", "category": "medical_records"},
    {"id": "2", "name": "Valley Ortho ledger.pdf", "folder": "/Medical/Valley Ortho", "category": "billing"},
    {"id": "3", "name": "Letter to Farmers 3-2.pdf", "folder": "/Insurance", "category": "insurance"},
    {"id": "4", "name": "Email to adjuster.msg", "folder": "/Corr", "category": "correspondence"},
    {"id": "5", "name": "scene photo 1.jpg", "folder": "/Photos", "category": "photographs"},
    {"id": "6", "name": "Fee agreement.pdf", "folder": "/Admin", "category": "other"},
    {"id": "7", "name": "Printed client email.pdf", "folder": "/Medical", "category": "medical_records"},
]
PLAN = [{"key": "set-1", "variant": "requests_for_production", "label": "x", "served": 0}]


def _data(tmp_path: Path, walled: list[str] = ()) -> Path:
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / rs.SCOPE).write_text(json.dumps({"files": [{**f, "pages": 1} for f in FILES]}))
    if walled:
        (data / "walled.json").write_text(json.dumps([{"id": w} for w in walled]))
    return data


# ---- the firm table --------------------------------------------------------------------------


def test_the_table_validates_inside_response_scope_and_patterns_stay_optional():
    assert rs.validate({"exhibits": TABLE}) == []
    assert rs.validate({"patterns": {"billing": [r"\bBILL"]}, "exhibits": TABLE}) == []
    assert rs.validate({"patterns": {"billing": [r"\bBILL"]}}) == []  # an old firm file reads as before
    assert rs.validate({}) and rs.validate({"exhibits": TABLE, "other": 1})
    # A firm that authored only the table keeps the default category patterns.
    assert (
        rs.patterns({"response_scope": {"exhibits": TABLE}})["billing"][0].pattern == rs.DEFAULT_PATTERNS["billing"][0]
    )


@pytest.mark.parametrize(
    "bad, said",
    [
        ({"letters": [{"letter": "AB", "title": "X", "categories": ["billing"]}]}, "one capital letter"),
        ({"letters": [{"letter": "A", "title": "X", "categories": ["bills"]}]}, "is not one of"),
        ({"letters": [{"letter": "A", "title": "X", "names": ["("]}]}, "invalid regex"),
        ({"letters": [{"letter": "A", "title": "X"}]}, "needs one or more"),
        ({"letters": [{"letter": "A", "title": "X", "names": ["a"]}, {"letter": "A", "title": "Y", "names": ["b"]}]},
         "each used once"),
        ({"letters": [{"letter": "A", "title": "X", "names": ["a"]}], "never": [{"names": ["x"]}]}, "expected {reason}"),
    ],
)  # fmt: skip
def test_a_malformed_table_names_what_is_wrong(bad, said):
    assert any(said in p for p in rs.validate({"exhibits": bad})), rs.validate({"exhibits": bad})


def test_no_table_or_no_production_unit_writes_no_plan_and_removes_a_stale_one(tmp_path):
    data = _data(tmp_path)
    (data / xp.FILE).write_text("{}")
    assert xp.run(data, {}, PLAN) is None and not (data / xp.FILE).exists()
    assert xp.run(data, FIRM, [{"key": "s", "variant": "special_interrogatories"}]) is None


# ---- placement ---------------------------------------------------------------------------------


def test_every_file_is_placed_or_left_out_with_its_reason_and_walled_files_are_only_counted(tmp_path):
    rec = xp.run(_data(tmp_path, walled=["7"]), FIRM, PLAN)
    assert rec is not None
    got = {e["letter"]: [f["name"] for f in e["files"]] for e in rec["exhibits"]}
    assert got == {"A": ["Valley Ortho records.pdf"], "B": ["Valley Ortho ledger.pdf"], "D": ["scene photo 1.jpg"],
                   "E": []}  # fmt: skip
    left = {f["name"]: f["reason"] for f in rec["left_out"]}
    assert left == {
        "Letter to Farmers 3-2.pdf": "an insurance letter",
        "Email to adjuster.msg": "correspondence is never an exhibit",
        "Fee agreement.pdf": "no exhibit takes other files",
    }
    assert rec["walled"] == 1 and "Printed client email.pdf" not in json.dumps(rec)


def test_a_name_pattern_wins_over_a_category_and_never_wins_over_both():
    t = xp.table({"response_scope": {"exhibits": TABLE}})
    assert t is not None
    photo = {"id": "x", "name": "scene photo.jpg", "folder": "/Medical", "category": "medical_records"}
    assert xp.place([photo], t, set())["exhibits"][2]["files"][0]["name"] == "scene photo.jpg"
    letter = {"id": "y", "name": "Letter to insurer.pdf", "folder": "/Medical", "category": "medical_records"}
    assert xp.place([letter], t, set())["left_out"][0]["reason"] == "an insurance letter"


def test_a_file_walled_after_the_plan_leaves_its_exhibit_when_the_plan_is_read(tmp_path):
    data = _data(tmp_path)
    xp.run(data, FIRM, PLAN)
    (data / "walled.json").write_text(json.dumps([{"id": "1"}, {"id": "3"}]))
    rec = xp.load(data)
    assert rec is not None and [f["id"] for f in rec["exhibits"][0]["files"]] == ["7"] and rec["walled"] == 2
    assert all(f["id"] != "3" for f in rec["left_out"])


def test_the_inventory_gains_an_exhibit_column_only_with_a_plan(tmp_path):
    data = _data(tmp_path)
    assert "| Exhibit |" not in rs.inventory(data)
    xp.run(data, FIRM, PLAN)
    inv = rs.inventory(data)
    assert "| Valley Ortho ledger.pdf | /Medical/Valley Ortho | billing | 1 | B |" in inv
    assert "Exhibit A (MEDICAL RECORDS), Exhibit B (MEDICAL BILLS), Exhibit D (INCIDENT DOCUMENTS)" in inv
    assert "Exhibit E" not in inv.splitlines()[0]


# ---- the letters a response produces from ----------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("Responding Party will produce the documents attached as Exhibit B.", ["B"]),
        ("See Exhibits A and C.", ["A", "C"]),
        ("See Exhibits A, B, and D.", ["A", "B", "D"]),
        ("Produced as Exhibits A through C.", ["A", "B", "C"]),
        ("Produced as Exhibit A-B; see also Exhibit B.", ["A", "B"]),
        ("No documents are produced. Exhibitions of art are not at issue.", []),
    ],
)
def test_letters_in_reads_each_way_a_response_names_an_exhibit(text, want):
    assert xi.letters_in(text) == want


DOC = """PLAINTIFF'S RESPONSES TO REQUESTS FOR PRODUCTION, SET ONE

REQUEST FOR PRODUCTION NO. 1:

All medical records.

**RESPONSE TO REQUEST FOR PRODUCTION NO. 1:**

Responding Party will produce the records attached as Exhibit A.

REQUEST FOR PRODUCTION NO. 2:

All bills.

RESPONSE TO REQUEST FOR PRODUCTION NO. 2:

Responding Party will produce Exhibits A and B.

REQUEST FOR PRODUCTION NO. 3:

All photographs.

RESPONSE TO REQUEST FOR PRODUCTION NO. 3:

Responding Party will produce Exhibit D.
"""
CLOSE = "\nDATED: November 23, 2026\n\nALPHA EXAMPLE\n"


def _plan(tmp_path: Path) -> dict:
    rec = xp.run(_data(tmp_path, walled=["7"]), FIRM, PLAN)
    assert rec is not None
    return rec


def test_each_response_produces_from_the_letters_its_own_words_name():
    assert xi.produced(DOC) == {1: ["A"], 2: ["A", "B"], 3: ["D"]}


def test_code_writes_the_index_after_the_last_response_before_the_closing(tmp_path):
    out, rec = xi.write(DOC + CLOSE, _plan(tmp_path))
    region = out.split("Responding Party will produce Exhibit D.")[1].split("DATED:")[0]
    assert [p for p in region.split("\n\n") if p.strip()] == [
        "INDEX OF EXHIBITS",
        "EXHIBIT A: MEDICAL RECORDS",
        "1. Valley Ortho records.pdf (Request Nos. 1, 2)",
        "EXHIBIT B: MEDICAL BILLS",
        "1. Valley Ortho ledger.pdf (Request No. 2)",
        "EXHIBIT D: INCIDENT DOCUMENTS",
        "1. scene photo 1.jpg (Request No. 3)",
    ]
    assert out.endswith(CLOSE) and out.startswith(DOC.rstrip("\n"))
    assert [e["letter"] for e in rec["listed"]] == ["A", "B", "D"] and rec["unresolved"] == []
    assert xi.write(out, _plan(tmp_path / "again"))[0] == out  # the gate may run twice: same document


def test_a_model_written_index_is_replaced_and_its_heading_kept(tmp_path):
    model = (
        DOC
        + "\n**INDEX OF EXHIBITS**\n\nEXHIBIT C: CORRESPONDENCE\n\n1. Letter to Farmers 3-2.pdf (Request No. 9)\n"
        + CLOSE
    )
    out, _rec = xi.write(model, _plan(tmp_path))
    assert "Letter to Farmers" not in out and "CORRESPONDENCE" not in out
    assert out.count("INDEX OF EXHIBITS") == 1 and "**INDEX OF EXHIBITS**\n\nEXHIBIT A: MEDICAL RECORDS" in out


def test_an_exhibit_no_response_produces_from_is_not_listed_and_the_notes_say_so(tmp_path):
    plan = _plan(tmp_path)
    plan["exhibits"][3]["files"] = [
        {"id": "9", "name": "W-2 2025.pdf", "folder": "/Wage", "category": "employment_wage"}
    ]
    out, rec = xi.write(DOC, plan)
    assert "WAGE LOSS" not in out and rec["unproduced"][0]["letter"] == "E"
    xi.keep(tmp_path, rec)
    assert any(n.startswith("Exhibit E (WAGE LOSS): no response produces from it") for n in xi.notes(tmp_path))


def test_a_document_with_no_response_heading_is_left_alone(tmp_path):
    assert xi.write("No responses here.\n", _plan(tmp_path))[0] == "No responses here.\n"


# ---- a response producing from an empty exhibit: a finding for the converge loop --------------

EMPTY = DOC.replace("Responding Party will produce Exhibit D.", "Responding Party will produce Exhibits E and G.")


def test_an_empty_or_unknown_exhibit_is_a_finding_row_the_converge_loop_reads(tmp_path):
    rows = xi.audit_rows(EMPTY, _plan(tmp_path))
    assert len(rows) == 2 and all(compose.row_verdict(r) == "INVENTED" and compose.row_item(r) == 3 for r in rows)
    assert "Exhibit E (WAGE LOSS) holds no document" in rows[0] and "there is no Exhibit G" in rows[1]
    assert "A (MEDICAL RECORDS), B (MEDICAL BILLS), D (INCIDENT DOCUMENTS)" in rows[0]


def test_the_rows_land_in_the_section_that_holds_the_response_once(tmp_path):
    draft = "# CAPTION\n\nx\n\n## RESPONSES\n\n" + EMPTY
    audit = "# Audit v1\n\nTallies: {}\n\n## AUDIT: RESPONSES\n\n- SUPPORTED | fine\n"
    once = xi.add_rows(audit, draft, _plan(tmp_path))
    assert xi.add_rows(once, draft, _plan(tmp_path / "b")) == once
    rows = compose.strict_rows(once)
    assert [r[0] for r in rows] == ["RESPONSES", "RESPONSES"]


def test_watch_adds_the_findings_to_every_audit_the_drafter_writes(tmp_path):
    data = _data(tmp_path)
    xp.run(data, FIRM, PLAN)
    unit = data / "set-1"
    unit.mkdir()

    class Drafter:
        def __init__(self) -> None:
            self.data = unit

        def audit(self, version: int, digest: str, corpus_text: str, full: bool = False) -> dict:
            (unit / f"draft-v{version}.md").write_text("## RESPONSES\n\n" + EMPTY)
            (unit / f"audit-v{version}.md").write_text("# Audit\n\n## AUDIT: RESPONSES\n\n")
            return {"tallies": {}}

    d = Drafter()
    xi.watch(d, data)
    d.audit(1, "", "")
    assert len(compose.strict_rows((unit / "audit-v1.md").read_text())) == 2


def test_an_empty_exhibit_still_named_at_the_gate_is_never_listed_and_the_notes_name_the_response(tmp_path):
    out, rec = xi.write(EMPTY, _plan(tmp_path))
    assert "EXHIBIT E" not in out and "EXHIBIT D" not in out
    xi.keep(tmp_path, rec)
    want = endings.FIRM_MESSAGES[endings.ending("exhibit_unresolved").message].format(
        item="Response to Request for Production No. 3"
    )
    assert xi.notes(tmp_path).count(want) == 2


def test_the_notes_name_every_placed_and_left_out_file(tmp_path):
    data = _data(tmp_path, walled=["7"])
    plan = xp.run(data, FIRM, PLAN)
    assert plan is not None
    _out, rec = xi.write(DOC, plan)
    xi.keep(tmp_path, rec)
    notes = xi.notes(tmp_path)
    assert notes[0].endswith("3 documents in 3 exhibits")
    assert "Exhibit B (MEDICAL BILLS), Request No. 2: Valley Ortho ledger.pdf" in notes
    left = next(n for n in notes if n.startswith("Not placed"))
    assert "Letter to Farmers 3-2.pdf (an insurance letter)" in left and "Fee agreement.pdf" in left
    assert "1 documents held back by the privilege wall are never placed in an exhibit" in notes
    assert "Printed client email" not in json.dumps(notes)


# ---- the $0 replay stage ------------------------------------------------------------------------


def _key(exhibits) -> key_mod.Key:
    return key_mod.parse({"served_sets": [{"type": "rfp", "set": "One"}], "exhibits": exhibits})


def test_the_replay_compares_placed_files_and_request_numbers_with_the_key(tmp_path):
    plan = _plan(tmp_path)
    listed = xi.index_lines(DOC, plan)[1]["listed"]
    key = _key([
        {"letter": "A", "files": ["1. Valley Ortho records (Request Nos. 1, 2)"]},
        {"letter": "B", "files": [{"name": "Valley Ortho ledger.pdf", "requests": [2]}]},
        {"letter": "D", "files": ["1. scene photo 1.jpg (Request No. 3)"]},
    ])  # fmt: skip
    sec = compare.exhibits(key, plan, listed)
    assert sec.status == "ok", sec.mismatches


def test_the_replay_names_an_extra_a_miss_with_codes_reason_and_a_wrong_number(tmp_path):
    plan = _plan(tmp_path)
    listed = xi.index_lines(DOC, plan)[1]["listed"]
    key = _key([
        {"letter": "A", "files": ["1. Valley Ortho records.pdf (Request No. 1)"]},
        {"letter": "C", "files": ["1. Letter to Farmers 3-2.pdf (Request No. 4)"]},
        {"letter": "B", "files": ["1. Valley Ortho ledger.pdf (Request No. 2)"]},
    ])  # fmt: skip
    got = compare.exhibits(key, plan, listed).mismatches
    assert any("Request Nos. [1, 2] in code's index, [1] in the key" in m for m in got)
    assert any("Letter to Farmers 3-2.pdf: missed" in m and "an insurance letter" in m for m in got)
    assert any("scene photo 1: extra" in m for m in got)


def test_a_key_with_no_exhibits_compares_nothing_and_says_so():
    sec = compare.exhibits(_key(None), {}, None)
    assert sec.status == "ok" and "no exhibits" in sec.notes[0]


def _replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key_files: list) -> tuple[int, list[str]]:
    import yaml
    from drafting_testkit import firm_data
    from test_drafting_response_replay import _install, _key as replay_key, _matter, _mismatches, _served

    from jobs.drafting import response_replay as R

    _install(monkeypatch, _served(), [])
    firm = tmp_path / "drafting-firm.yaml"
    firm.write_text(yaml.safe_dump({**firm_data(), "response_scope": {"exhibits": TABLE}}))
    production = tmp_path / "rfp.md"
    production.write_text("RESPONSE TO REQUEST FOR PRODUCTION NO. 1:\n\nResponding Party will produce Exhibit A.\n")
    key = replay_key(tmp_path, exhibits=[{"letter": "A", "files": key_files}])
    args = [str(_matter(tmp_path)), "--key", str(key), "--stage", "exhibits", "--firm", str(firm)]
    return R.main([*args, "--production", str(production)]), _mismatches(key)


def test_the_replay_stage_places_the_saved_files_with_the_real_scope_and_scores_them(tmp_path, monkeypatch):
    code, mism = _replay(tmp_path, monkeypatch, ["1. Sample Clinic records.pdf (Request No. 1)"])
    assert code == 0, mism


def test_the_replay_stage_exits_one_on_a_wrong_line(tmp_path, monkeypatch):
    code, mism = _replay(tmp_path, monkeypatch, ["1. Sample Clinic records.pdf (Request Nos. 1, 4)"])
    assert code == 1 and any("Request Nos. [1] in code's index, [1, 4] in the key" in m for m in mism)
