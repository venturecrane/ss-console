"""The revise lane's wave 1 (2026-11-23): duplicate rules folded at freeze, the
reader's bare interrogatory heading and its Index of Exhibits and notes
regions, exhibit letters filled by code from the document's own index, and the
rehearsal's per-response score.

Driven through the real reader, the real in-place editor and the real
rehearsal over recorded answers. Every name, title, number and sentence is
invented (Example / Exampletown). Each test names the line it defends.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import docx
import pytest
from test_firm_wording import _rule, _store, _switch

from jobs import firm_wording
from jobs.drafting.revise import prior, responses as rs, rules as rules_mod, score, set_rehearse

PROD = (
    "Responding Party will produce all non-privileged, responsive documents within Responding Party's "
    "possession, custody, or control, which can be found at Exhibit"
)
NONE_EXIST = "Responding Party has made a diligent search and no such documents are known to exist."
RULES = [
    {
        "rule_id": "wr-prod",
        "text": "Use the production wording.",
        "wording": [f'Where documents will be produced, say "{PROD} "', f'Where none exist, say "{NONE_EXIST}"'],
        "doc_kinds": ["discovery_response"],
        "attorney": None,
    }
]
INDEX = [
    "INDEX OF EXHIBITS",
    "EXHIBIT A: SAMPLE RECORDS",
    "1. Records of Example Clinic (Request Nos. 1)",
    "EXHIBIT B: SAMPLE BILLS",
    "1. Bills of Example Clinic (Request Nos. 2)",
    "2. Letter to Example Adjuster (Request Nos. 2)",
]


def build(
    label: str,
    numbers: list[str],
    answers: dict[str, str] | None = None,
    title: str = "",
    index: list[str] | None = None,
    notes: list[str] | None = None,
) -> bytes:
    d = docx.Document()
    d.add_paragraph("SUPERIOR COURT OF THE STATE OF EXAMPLE")
    if title:
        d.add_paragraph(title)
    for n in numbers:
        d.add_paragraph(f"{label} NO. {n}:")
        d.add_paragraph(f"Describe item {n}.")
        d.add_paragraph(f"RESPONSE TO {label} NO. {n}:")
        d.add_paragraph((answers or {}).get(n, f"Responding party answers item {n}. Documents will be produced."))
    for line in index or []:
        d.add_paragraph(line)
    for line in notes or []:
        d.add_paragraph(line)
    d.add_paragraph("DATED: December 1, 2026")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def md_of(tmp: Path, blob: bytes, name: str = "a.docx") -> str:
    (tmp / name).write_bytes(blob)
    return prior.docx_to_md(tmp / name)


# ---- duplicate rules --------------------------------------------------------------------------
def test_rules_with_the_same_words_fold_to_the_oldest_id_and_the_newest_time() -> None:
    """FALSIFIER: freeze without folding and the same rule is asked about twice."""
    first = {**_rule("wr-a", ["discovery_response"], words="Say it  plainly."), "at": "2026-11-19T15:00:00Z"}
    other = {**_rule("wr-b", ["discovery_response"], words="Another rule."), "at": "2026-11-19T16:00:00Z"}
    again = {**_rule("wr-c", ["discovery_response"], words="say it plainly."), "at": "2026-11-20T09:00:00Z"}
    folded = firm_wording.fold([first, other, again])
    assert [r["rule_id"] for r in folded] == ["wr-b", "wr-a"]  # it stands where its newest row stood
    assert folded[1]["at"] == "2026-11-20T09:00:00Z" and folded[1]["aliases"] == ["wr-c"]
    assert firm_wording.fold([first, other]) == [first, other]


def test_freeze_writes_the_folded_rules(tmp_path, monkeypatch) -> None:
    _switch(tmp_path, monkeypatch, True)
    spec = tmp_path / "spec"
    rows = [
        {**_rule("wr-new", ["discovery_response"], words="One rule."), "at": "2026-11-21T00:00:00Z"},
        {**_rule("wr-old", ["discovery_response"], words="One rule."), "at": "2026-11-18T00:00:00Z"},
    ]
    _store(spec, rows)
    monkeypatch.setenv("JOBS_SPEC_DIR", str(spec))
    data = tmp_path / "data"
    data.mkdir()
    got = firm_wording.freeze(data, "discovery_response", None)
    assert [(r["rule_id"], r["at"], r["aliases"]) for r in got] == [("wr-old", "2026-11-21T00:00:00Z", ["wr-new"])]


# ---- the reader -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("title", "numbers", "hint", "want"),
    [
        ("PLAINTIFF'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE", ["1", "2"], None, "special_interrogatories"),
        ("PLAINTIFF'S RESPONSES TO FORM INTERROGATORIES, SET ONE", ["1", "2"], None, "form_interrogatories"),
        ("", ["1", "2"], "special_interrogatories", "special_interrogatories"),
        ("", ["1.1", "2.1"], None, "form_interrogatories"),
    ],
)
def test_a_bare_interrogatory_heading_takes_its_kind_from_title_hint_or_number(tmp_path, title, numbers, hint, want):
    """FALSIFIER: LABELS[label] on a bare heading raises KeyError and the job fails."""
    doc = rs.read(md_of(tmp_path, build("INTERROGATORY", numbers, title=title)), hint)
    assert doc.set_type == want and doc.numbers == numbers
    assert all(r.request for r in doc.responses)  # the request as restated is paired


def test_a_bare_heading_nothing_settles_does_not_read(tmp_path) -> None:
    with pytest.raises(rs.ResponsesUnread, match="special or form"):
        rs.read(md_of(tmp_path, build("INTERROGATORY", ["1", "2"])), "requests_for_production")


def test_the_index_and_the_notes_are_never_part_of_the_last_response(tmp_path) -> None:
    """FALSIFIER: no index end and the index reads as response 2's paragraphs."""
    notes = ["NOTES FOR THE ATTORNEY", "RESPONSE TO REQUEST FOR PRODUCTION NO. 2: reworded as you asked."]
    md = md_of(tmp_path, build("REQUEST FOR PRODUCTION", ["1", "2"], index=INDEX, notes=notes))
    doc = rs.read(md)
    assert doc.numbers == ["1", "2"]
    last = doc.text_of(doc.by_number()["2"])
    assert "EXHIBIT" not in last and "reworded" not in last
    assert doc.index is not None and [(e.letter, e.title, len(e.lines)) for e in doc.index.exhibits] == [
        ("A", "SAMPLE RECORDS", 1),
        ("B", "SAMPLE BILLS", 2),
    ]
    assert doc.index_lines()[0] == "EXHIBIT A: SAMPLE RECORDS" and doc.notes is not None
    edited = md.replace("2. Letter to Example Adjuster (Request Nos. 2)\n\n", "")
    assert rs.changed(md, edited) == ([], True)  # an index edit is outside every response


# ---- exhibit letters --------------------------------------------------------------------------
def _doc(tmp: Path, index: list[str] | None = INDEX) -> rs.Document:
    return rs.read(md_of(tmp, build("REQUEST FOR PRODUCTION", ["1", "2"], index=index)))


def test_the_exhibit_letter_comes_from_the_documents_own_index(tmp_path) -> None:
    """FALSIFIER: write the phrase as the rule quotes it and the response ends
    '...found at Exhibit' with no letter."""
    doc = _doc(tmp_path)
    raw = {"values": [{"request": "2", "rule": "wr-prod", "action": "replace", "phrase": 1,
                       "sentence": "Documents will be produced.", "exhibits": ["Sample Bills"]}]}  # fmt: skip
    values, _c, problems = rules_mod.check(raw, RULES, doc)
    assert not problems and values[0].exhibits == ("Sample Bills",)
    new, did, skipped = rs.apply(doc, values)
    assert did and not skipped
    assert new.text_of(new.by_number()["2"]).endswith(f"{PROD} B.")  # its own period, not two


def test_a_letter_from_the_model_is_refused(tmp_path) -> None:
    doc = _doc(tmp_path)
    for exhibits in (["B"], [], None, ["Exhibit B"]):
        raw = {"values": [{"request": "2", "rule": "wr-prod", "action": "end", "phrase": 1, "exhibits": exhibits}]}
        values, _c, problems = rules_mod.check(raw, RULES, doc)
        assert not values and problems, exhibits
    raw = {"values": [{"request": "2", "rule": "wr-prod", "action": "end", "phrase": 2}]}
    assert rules_mod.check(raw, RULES, doc)[0]  # a phrase with no slot takes no exhibit


@pytest.mark.parametrize(
    "index",
    [
        None,  # no index at all
        INDEX[:3],  # the title is not in it
        [*INDEX, "EXHIBIT C: SAMPLE BILLS"],  # the title is in it twice
    ],
)
def test_an_exhibit_the_index_does_not_name_once_leaves_the_response_as_filed(tmp_path, index) -> None:
    doc = _doc(tmp_path, index)
    value = rs.Value("2", "wr-prod", "end", PROD, "", ("Sample Bills",))
    new, did, skipped = rs.apply(doc, [value])
    assert not did and new.md == doc.md
    assert skipped and rs.EXHIBIT_UNRESOLVED in skipped[0]


def test_values_keep_their_exhibits_through_the_jobs_file() -> None:
    v = rs.Value("2", "wr-prod", "end", PROD, "", ("Sample Bills",))
    back, _c = rules_mod.from_json(rules_mod.to_json([v], []))
    assert back == [v]
    old = json.dumps({"values": [{k: x for k, x in v.to_dict().items() if k != "exhibits"}], "conflicts": []})
    assert rules_mod.from_json(old)[0][0].exhibits == ()  # a values.json written before this reads


# ---- the score --------------------------------------------------------------------------------
def test_the_score_counts_made_correct_missed_and_extra() -> None:
    def doc(answers: dict[str, str], index: list[str]) -> rs.Document:
        blocks = []
        for n in ("1", "2", "3", "4"):
            blocks += [f"REQUEST FOR PRODUCTION NO. {n}:", f"RESPONSE TO REQUEST FOR PRODUCTION NO. {n}:"]
            blocks.append(answers.get(n, f"Answer {n}."))
        return rs.read("\n\n".join([*blocks, *index, "DATED: today"]) + "\n")

    base = doc({}, INDEX)
    out = doc({"1": "Answer one, revised.", "2": "Answer two, my way.", "4": "Answer four, extra."}, INDEX)
    key = doc({"1": "Answer one, revised.", "2": "Answer two, the key's way.", "3": "Answer three, key."}, INDEX[:-1])
    got = score.responses(base, out, key)
    assert got["responses"] == {"1": "correct", "2": "wrong", "3": "missed", "4": "extra"}
    assert {c: got[c] for c in score.COUNTS} == {"made": 3, "correct": 1, "missed": 2, "extra": 1, "wrong": 1}
    idx = score.index(base, out, key)
    assert (idx["made"], idx["correct"], idx["missed"], idx["extra"]) == (0, 0, 1, 0)


def test_the_rehearsal_scores_each_response_and_says_delivered_or_held(tmp_path) -> None:
    """FALSIFIER: score by 'changed the same responses' and a response made in
    other words than the key's counts as correct."""
    units = tmp_path / "units" / "rfp"
    units.mkdir(parents=True)
    title = "PLAINTIFF'S RESPONSES TO REQUESTS FOR PRODUCTION"
    (units / "base.docx").write_bytes(build("REQUEST FOR PRODUCTION", ["1", "2"], title=title, index=INDEX))
    key = {"2": f"Responding party answers item 2. {PROD} B.", "1": f"Responding party answers item 1. {NONE_EXIST}"}
    (units / "key.docx").write_bytes(build("REQUEST FOR PRODUCTION", ["1", "2"], key, title=title, index=INDEX))
    rec = units.parent / "recordings.jsonl"
    rep = set_rehearse.rehearse_set(units.parent, "apply the new rules", RULES, rec)
    assert rep["request"]["outcome"] == "incomplete" and len(rep["needed"]) == 1
    answer = {"values": [{"request": "2", "rule": "wr-prod", "action": "replace", "phrase": 1,
                          "sentence": "Documents will be produced.", "exhibits": ["SAMPLE BILLS"]}]}  # fmt: skip
    n = rep["needed"][0]
    row = {"sha": n["sha"], "text": json.dumps(answer), "stop": "end_turn", "in": 1, "out": 1, "stage": n["stage"]}
    rec.write_text(json.dumps(row) + "\n")
    rep = set_rehearse.rehearse_set(units.parent, "apply the new rules", RULES, rec)
    d = rep["documents"][0]
    assert d["responses"]["responses"] == {"2": "correct", "1": "missed"}
    assert rep["totals"]["responses"] == {"made": 1, "correct": 1, "missed": 1, "extra": 0, "wrong": 0}
    assert rep["totals"]["index"]["made"] == 0 and rep["request"] == {"outcome": "delivered", "reason": ""}
