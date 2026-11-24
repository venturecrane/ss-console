"""The revise core's code-only parts (A2): response numbers read from words, the
attorney's email instructions checked, candidate markers settled, the Index of
Exhibits recomputed, and the block-level edit. Pure: no seat, no model.
Every name, number and sentence is invented (Example / Exampletown)."""

from __future__ import annotations

import io
from pathlib import Path

import docx
import pytest

from jobs.drafting.revise import blocks, index_fix, instructions as ins, markers, numbers as num
from jobs.drafting.revise import responses as rs, set_units as su
from jobs.drafting.rulebook.book import Book, Entry

PRIVACY = "Responding party objects to this {item} on the ground that it seeks private information."
RELEVANCE = "Responding party objects to this {item} on the ground that it seeks irrelevant information."
NODOCS = "No responsive documents exist."
BOOK = Book(
    {
        "privacy": Entry("privacy", "objection", PRIVACY, ("privacy", "private")),
        "relevance": Entry("relevance", "objection", RELEVANCE, ("relevance", "irrelevant")),
        "no_docs": Entry("no_docs", "disposition", NODOCS, ("no documents",)),
    }
)
DOCS = [
    ins.Doc("00", "special_interrogatories", ("1", "2", "3")),
    ins.Doc("01", "requests_for_production", ("1", "2", "3", "9")),
    ins.Doc("02", "form_interrogatories", ("1.1", "2.1")),
]


# ---- numbers ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, want",
    [
        ("For No. 9 add a relevance objection.", ("9",)),
        ("Nos. 4, 7-9 and 15 need the phrase.", ("4", "7", "8", "9", "15")),
        ("On 11, the client had no coverage.", ("11",)),
        ("Form interrogatory 12.1-12.3 should be shorter.", ("12.1", "12.2", "12.3")),
        ("Pay $2,500 by 2026 for 5 visits.", ()),
    ],
)
def test_response_numbers_are_read_only_after_a_word_that_says_so(text, want) -> None:
    """FALSIFIER: read bare figures and a dollar amount becomes response 2,500."""
    assert num.numbers_in(text) == want


# ---- email instructions -----------------------------------------------------------------------
def _item(**kw) -> dict:
    return {"quote": "", "numbers": [], "set_type": "", "action": "add_objection", "keys": [], **kw}


def test_a_checked_instruction_names_its_one_document() -> None:
    email = "Thanks. For RFP No. 9 add a privacy objection."
    raw = {"items": [_item(quote="For RFP No. 9 add a privacy objection", numbers=["9"], keys=["privacy"])]}
    got, problems = ins.check(raw, email, DOCS, BOOK)
    assert problems == []
    (i,) = got.instructions["01"]
    assert (i.source, i.numbers, i.doc_type, i.act, i.keys) == (
        "email", ("9",), "requests_for_production", "add_objection", ("privacy",)
    )  # fmt: skip


@pytest.mark.parametrize(
    "email, item, fragment",
    [
        ("For No. 9 add a privacy objection.", _item(quote="For No. 9 add a relevance objection", numbers=["9"],
         keys=["relevance"]), "not copied"),
        ("For No. 9 add a privacy objection.", _item(quote="For No. 9 add a privacy objection", numbers=["8"],
         keys=["privacy"]), "the quote names responses"),
        ("For No. 2 add a privacy objection.", _item(quote="For No. 2 add a privacy objection", numbers=["2"],
         keys=["privacy"]), "say which set"),
        ("For No. 9 add a privacy objection.", _item(quote="For No. 9 add a privacy objection", numbers=["9"],
         keys=["relevance"]), "none of its names"),
        ("For No. 9 add a privacy objection.", _item(quote="For No. 9 add a privacy objection", numbers=["9"],
         keys=["no_docs"]), "is a disposition"),
        ("For No. 9 do not add a privacy objection.", _item(quote="For No. 9 do not add a privacy objection",
         numbers=["9"], keys=["privacy"]), "says not to"),
        ("For No. 9 add the privacy objection.", _item(quote="For No. 9 add the privacy objection", numbers=["9"],
         action="remove_objection", keys=["privacy"]), "asks to add"),
        ("For No. 9 add a privacy objection.", _item(quote="For No. 9 add a privacy objection", numbers=["9"],
         action="redo", keys=["privacy"]), "action is one of"),
        ("For special rog No. 9 add a privacy objection.", _item(quote="For special rog No. 9 add a privacy objection",
         numbers=["9"], keys=["privacy"]), "no document in scope"),
    ],
)  # fmt: skip
def test_an_instruction_code_cannot_verify_is_refused(email, item, fragment) -> None:
    """Each refusal path. FALSIFIER: keep any of these and a response changes on a model's guess."""
    got, problems = ins.check({"items": [item]}, email, DOCS, BOOK)
    assert not got.instructions and any(fragment in p for p in problems), problems


def test_a_response_number_no_kept_quote_covers_is_refused() -> None:
    """FALSIFIER: drop the omission guard and "also fix No. 3" is silently ignored."""
    email = "For RFP No. 9 add a privacy objection. Also fix RFP No. 3."
    raw = {"items": [_item(quote="For RFP No. 9 add a privacy objection", numbers=["9"], keys=["privacy"])]}
    _got, problems = ins.check(raw, email, DOCS, BOOK)
    assert problems == ["the email names response(s) 3 in words no instruction kept"]


def test_an_attorney_fact_carries_only_his_words() -> None:
    email = "On special rog No. 2, the client did not have UM coverage with Example Mutual."
    quote = "On special rog No. 2, the client did not have UM coverage with Example Mutual"
    ok = _item(
        quote=quote,
        numbers=["2"],
        action="add_fact",
        text="Responding party did not have UM coverage with Example Mutual.",
    )
    got, problems = ins.check({"items": [ok]}, email, DOCS, BOOK)
    assert problems == [] and got.facts[0].unit == "00" and got.facts[0].number == "2"
    bad = {**ok, "text": "Responding party had coverage with Example Casualty until 2025."}
    _got, problems = ins.check({"items": [bad]}, email, DOCS, BOOK)
    assert problems and "is not in the attorney's words" in problems[0]


def test_an_email_naming_no_response_makes_no_call() -> None:
    def never(*_a):
        raise AssertionError("no call")

    assert ins.read("Please apply all new rules to the responses.", DOCS, BOOK, never).instructions == {}


def test_a_refused_answer_is_asked_once_more_then_refused() -> None:
    import json

    calls = []

    def ask(stage, system, user, max_tokens):
        calls.append(user)
        return json.dumps({"items": []})

    with pytest.raises(ins.InstructionsRefused):
        ins.read("For RFP No. 9 add a privacy objection.", DOCS, BOOK, ask)
    assert len(calls) == 2 and "REFUSED BY CODE" in calls[1]


# ---- candidate markers ------------------------------------------------------------------------
def test_a_named_candidate_objection_becomes_the_firms_words_and_nothing_else_moves() -> None:
    body = [
        "{{ATTORNEY: CANDIDATE OBJECTION: privacy | it asks for tax returns}} Responding party will comply.",
        "{{ATTORNEY: confirm the date}}",
        "{{CANDIDATE OBJECTION: relevance | the period}}",
        "{{CANDIDATE OBJECTION: compound | two questions}}",
    ]
    new, done = markers.settle("4", body, BOOK, "requests_for_production", {"privacy"})
    assert new[0] == PRIVACY.replace("{item}", "Request") + " Responding party will comply."
    assert new[1] == "{{ATTORNEY: confirm the date}}"  # never touched
    assert new[2:] == body[2:]  # not named by a rule: left
    assert [(s.key, s.left) for s in done] == [
        ("privacy", ""),
        ("relevance", "ground_not_named"),
        ("", "phrase_missing"),
    ]


def test_a_marker_whose_objection_is_already_stated_is_only_removed() -> None:
    stated = PRIVACY.replace("{item}", "Interrogatory")
    new, _ = markers.settle(
        "1", [stated, "{{CANDIDATE OBJECTION: privacy | why}}"], BOOK, "special_interrogatories", {"privacy"}
    )
    assert new == [stated]  # the emptied paragraph goes


# ---- the index --------------------------------------------------------------------------------
INDEX_MD = (
    "\n\n".join(
        [
            "RESPONSE TO REQUEST FOR PRODUCTION NO. 1:",
            "Responsive documents can be found at Exhibit A.",
            "RESPONSE TO REQUEST FOR PRODUCTION NO. 2:",
            "Responsive documents can be found at Exhibits A and B.",
            "INDEX OF EXHIBITS",
            "EXHIBIT A: SAMPLE RECORDS",
            "1. Records of Example Clinic (Request Nos. 1, 2)",
            "EXHIBIT B: SAMPLE BILLS",
            "1. Bills of Example Clinic (Request No. 2)",
            "2. Bills of Example Lab (Request No. 2)",
        ]
    )
    + "\n"
)


def test_the_index_follows_what_the_revision_changed() -> None:
    """Response 1 moves from Exhibit A to B. FALSIFIER: leave the lines and the
    index lists a request that no longer produces the file."""
    doc = rs.read(INDEX_MD)
    after = rs.read(blocks.build(doc, {"1": ["Responsive documents can be found at Exhibit B."]}))
    ch = index_fix.recompute(doc, index_fix.produced(doc), index_fix.produced(after))
    lines = [ch.edits.get(i, doc.blocks[i]) for i in doc.index.blocks]
    assert lines[1] == "1. Records of Example Clinic (Request No. 2)"
    assert lines[3:] == [
        "1. Bills of Example Clinic (Request Nos. 1, 2)",
        "2. Bills of Example Lab (Request Nos. 1, 2)",
    ]


def test_a_line_left_with_no_request_and_an_unlisted_letter_are_named_not_written() -> None:
    doc = rs.read(INDEX_MD)
    after = rs.read(blocks.build(doc, {"1": ["Found at Exhibit C."], "2": ["Found at Exhibit B."]}))
    ch = index_fix.recompute(doc, index_fix.produced(doc), index_fix.produced(after))
    codes = sorted(c for _n, c, _w in ch.left)
    assert codes == ["exhibit_unresolved", "index_line_kept"] and doc.index.exhibits[0].lines[0] not in ch.edits


def test_an_index_file_pairs_with_one_production_document() -> None:
    idx = {"index": True}
    prod = {"set_type": "requests_for_production"}
    assert index_fix.pair([prod, idx], 1) == (0, "")
    assert index_fix.pair([{"set_type": "special_interrogatories"}, idx], 1)[1].startswith("index_unpaired")
    assert index_fix.pair([prod, prod, idx], 2)[1].startswith("index_unpaired")


# ---- the block-level edit ---------------------------------------------------------------------
def _filed(paragraphs: list[str], tmp: Path) -> Path:
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    (tmp / "f.docx").write_bytes(buf.getvalue())
    return tmp / "f.docx"


PARAS = [
    "RESPONSE TO SPECIAL INTERROGATORY NO. 1:",
    "Responding party answers item 1.",
    "RESPONSE TO SPECIAL INTERROGATORY NO. 2:",
    "Responding party answers item 2.",
    "DATED: December 1, 2026",
]


def test_a_paragraph_is_added_and_removed_in_the_filed_file_and_proven(tmp_path) -> None:
    filed = _filed(PARAS, tmp_path)
    md, doc = su.read_base(filed, tmp_path, None)
    new = blocks.build(doc, {"2": ["An objection of its own.", "Responding party answers item 2."]})
    rec = blocks.prove(md, filed, new, tmp_path / "out.docx", {"2"}, index_ok=False, cls="discovery_response", fmt=None)
    assert rec["changed"] == ["2"] and rec["added"] == 1
    got = [p.text for p in docx.Document(str(tmp_path / "out.docx")).paragraphs]
    assert got == [*PARAS[:3], "An objection of its own.", *PARAS[3:]]


def test_a_change_to_a_response_the_plan_does_not_name_is_refused(tmp_path) -> None:
    """FALSIFIER: drop the subset check and a stage drift rewrites response 1 unnoticed."""
    filed = _filed(PARAS, tmp_path)
    md, doc = su.read_base(filed, tmp_path, None)
    new = blocks.build(doc, {"1": ["Changed."], "2": ["Also changed."]})
    with pytest.raises(su.Refused, match="revise_layout_lost"):
        blocks.prove(md, filed, new, tmp_path / "o.docx", {"2"}, index_ok=False, cls="discovery_response", fmt=None)
    with pytest.raises(su.Refused, match="outside the responses"):
        blocks.prove(md, filed, md.replace("DATED", "Signed"), tmp_path / "o.docx", set(), index_ok=False,
                     cls="discovery_response", fmt=None)  # fmt: skip


# ---- scoring per kind, and the $0 rehearsal of this path -------------------------------------
def test_a_change_is_scored_by_its_kind() -> None:
    from jobs.drafting.revise import score

    pr = PRIVACY.replace("{item}", "Request")
    assert score.change_kinds("Answer.", f"{pr} Answer.", BOOK) == {"objection_added"}
    assert score.change_kinds("Some documents exist.", NODOCS, BOOK) == {"disposition", "sentence_removed"}
    assert score.change_kinds("{{ATTORNEY: CANDIDATE OBJECTION: privacy}} Answer.", "Answer.", BOOK) == {
        "marker_cleared"
    }
    base, key = rs.read(INDEX_MD), rs.read(INDEX_MD.replace("at Exhibit A.", f"at Exhibit A. {pr}"))
    got = score.kinds(base, key, key, BOOK)
    assert got["objection_added"] == {"correct": 1, "missed": 0, "extra": 0}
    assert score.kinds(base, base, key, BOOK)["objection_added"] == {"correct": 0, "missed": 1, "extra": 0}


def test_the_rehearsal_runs_the_jobs_own_functions_and_scores_against_the_key(tmp_path, monkeypatch) -> None:
    """FALSIFIER: a rehearsal with its own copy of the logic proves nothing about the job."""
    import json

    from jobs.drafting.revise import book_rehearse, book_run
    from jobs.drafting.rulebook.types import Applied, StageResult

    def stage(job, doc):
        md = doc.md.replace("Responding party answers item 2.", NODOCS, 1)
        return StageResult(md=md, applied=(Applied("rule:wr-x", "2"),))

    monkeypatch.setattr(book_run.rule_stage, "run", stage)
    unit = tmp_path / "units" / "special"
    unit.mkdir(parents=True)
    _filed(PARAS, unit).rename(unit / "base.docx")
    key = [p if p != "Responding party answers item 2." else NODOCS for p in PARAS]
    _filed(key, unit).rename(unit / "key.docx")
    book = tmp_path / "book.json"
    book.write_text(json.dumps([{"key": "no_docs", "kind": "disposition", "text": NODOCS, "names": ["no documents"],
                                 "sets": [], "source": "x"}]))  # fmt: skip
    rules = [{"rule_id": "wr-x", "wording": ["Say no documents plainly."], "doc_kinds": ["discovery_response"]}]
    rep = book_rehearse.rehearse(tmp_path / "units", "Apply the new rules.", rules, {}, book, tmp_path / "w")
    assert rep["request"]["outcome"] == "delivered", rep["request"]
    assert rep["totals"]["responses"]["correct"] == 1 and rep["totals"]["responses"]["extra"] == 0
    assert rep["totals"]["kinds"]["disposition"]["correct"] == 1


def test_every_left_reason_is_worded_from_a_table_never_model_text() -> None:
    """The stage's own codes in its own words; ours from ``notes_book.REASONS``;
    an email instruction is "your request", never "rule email"."""
    from jobs.drafting import wording
    from jobs.drafting.revise import notes_book as nb

    lines = [
        nb.left_line("3", "no_paragraph", "rule:wr-x"),
        nb.left_line("4", "phrase_missing", "rule:email"),
        nb.left_line("5", "ground_not_named", "privacy"),
        nb.left_line("1,2", "index_line_kept", "Exhibit A: 1. Records"),
        nb.source_label("rule:email", {}),
        nb.source_label("rule:wr-x", {"wr-x": {"at": "2026-11-20T10:00Z"}}),
    ]
    assert lines[0].startswith("Response 3: ") and "wr-x" in lines[0] and "{" not in lines[0]
    assert lines[4] == "your request" and lines[5] == "the firm's rule wr-x of 2026-11-20"
    assert all(wording.notes_findings(x) == [] for x in lines) and "rule email" not in " ".join(lines)


def test_an_email_that_opens_with_a_hash_number_is_covered_by_its_own_quote() -> None:
    from jobs.drafting.revise.instructions import uncovered

    text = "#22: always give the full date.\nThanks"
    assert uncovered(text, ["#22: always give the full date."]) == []
    assert uncovered(text, ["22: always give the full date."]) == []
    assert uncovered(text, []) != []


def test_an_emailed_objection_may_name_a_ground_the_book_lacks_but_only_in_his_words() -> None:
    email = "For RFP No. 9 add a cumulative objection."
    ok = _item(quote="For RFP No. 9 add a cumulative objection", numbers=["9"], grounds=["cumulative"])
    got, problems = ins.check({"items": [ok]}, email, DOCS, BOOK)
    assert problems == [] and got.instructions["01"][0].grounds == ("cumulative",)
    assert ins.Read.from_dict(got.to_dict()) == got
    for item, frag in [
        (_item(quote="For RFP No. 9 add a cumulative objection", numbers=["9"], grounds=["harassing"]), "not named"),
        (_item(quote="For RFP No. 9 add a cumulative objection", numbers=["9"]), "key or ground"),
        (_item(quote="For RFP No. 9 add a cumulative objection", numbers=["9"], action="use_phrase",
               grounds=["cumulative"]), "phrase-book key"),
    ]:  # fmt: skip
        _got, problems = ins.check({"items": [item]}, email, DOCS, BOOK)
        assert any(frag in p for p in problems), problems
    old = {"instructions": {"01": [{"source": "email", "quote": "q", "numbers": ["9"], "doc_type": "",
                                    "act": "add_objection", "keys": ["privacy"]}]}}  # fmt: skip
    assert ins.Read.from_dict(old).instructions["01"][0].grounds == ()
