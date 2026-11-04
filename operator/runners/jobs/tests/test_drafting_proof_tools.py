"""The drafting proof tools (``operator/templates/drafting/proof``) and the
authority fetch tool's store format, on synthetic data.

The scorer must locate a key row by what it is about, not by its old words:
a reworded but correct statement is ``fixed``, a reworded but still wrong one
is ``missed``. ``score_draft`` must tell a quote on its cited page from one a
page off and a figure the cited page does not print.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from jobs.drafting.verify import authorities

OPERATOR = Path(__file__).resolve().parents[3]
PROOF = OPERATOR / "templates" / "drafting" / "proof"
sys.path.insert(0, str(PROOF))

import scorer  # noqa: E402 -- the proof dir is put on the path above, as running the tool does
import score_draft  # noqa: E402 -- imported from the proof dir put on the path above


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FIG = {
    "id": "K1",
    "type": "figure",
    "fact": "the hospital total charges for emergency services",
    "correct_value": "$8,300.00",
    "source_file": "Hospital Bill.pdf",
    "source_page": 1,
    "wrong_as_stated": "$83,000.00",
    "wrong_pattern": r"83,000",
    "correct_pattern": "",
}
QUOTE = {
    "id": "K2",
    "type": "quote",
    "fact": "what the patient told the orthopedist about prior neck injury",
    "correct_value": "the patient reports a prior neck strain in 2015, resolved",
    "source_file": "Ortho Notes.pdf",
    "source_page": 3,
    "wrong_as_stated": "the patient denied any prior neck injury whatsoever",
    "wrong_pattern": r"denied (having )?any prior neck injury",
    "correct_pattern": "",
}
PIN = {
    "id": "K3",
    "type": "pin",
    "fact": "epidural steroid injection partial relief radicular symptoms",
    "correct_value": "p. 14",
    "source_file": "Ortho Notes.pdf",
    "source_page": 14,
    "wrong_as_stated": "p. 12",
    "wrong_pattern": "",
    "correct_pattern": "",
}
OMIT = {
    "id": "K4",
    "type": "omission",
    "fact": "the clinic noted a gap in treatment after missed appointments",
    "correct_value": "missed two scheduled appointments gap in treatment",
    "source_file": "Ortho Notes.pdf",
    "source_page": 5,
    "wrong_as_stated": "",
    "wrong_pattern": "",
    "correct_pattern": r"gap in treatment",
}


def _outcomes(draft: str) -> dict[str, str]:
    return {r.row.id: r.outcome for r in scorer.score([FIG, QUOTE, PIN, OMIT], draft).rows}


def test_reworded_correct_is_fixed_and_reworded_wrong_is_missed() -> None:
    right = (
        "Emergency services were charged at $8,300 in total (FILE: Hospital Bill.pdf, p. 1). "
        "She told the orthopedist that the patient reports a prior neck strain in 2015, which resolved "
        "(FILE: Ortho Notes.pdf, p. 3). "
        "The epidural steroid injection gave partial relief of radicular symptoms (FILE: Ortho Notes.pdf, p. 14)."
    )
    wrong = (
        "The hospital billed 83,000.00 for the emergency services (FILE: Hospital Bill.pdf, p. 1). "
        "At the orthopedic visit the patient denied having any prior neck injury whatsoever "
        "(FILE: Ortho Notes.pdf, p. 3). "
        "The epidural steroid injection gave partial relief of radicular symptoms (FILE: Ortho Notes.pdf, p. 12)."
    )
    got_right, got_wrong = _outcomes(right), _outcomes(wrong)
    assert got_right == {"K1": "fixed", "K2": "fixed", "K3": "fixed", "K4": "missed"}, got_right
    assert got_wrong == {"K1": "missed", "K2": "missed", "K3": "missed", "K4": "missed"}, got_wrong


def test_flagged_wrong_is_caught_and_an_omission_in_the_notes_is_disclosed() -> None:
    draft = (
        "The hospital billed $83,000.00 for emergency services (FILE: Hospital Bill.pdf, p. 1).\n\n"
        "## Attorney notes\n\n"
        "Confirm the hospital total charges for emergency services against the bill. "
        "The clinic also recorded a gap in treatment after two missed appointments.\n"
    )
    got = _outcomes(draft)
    assert got["K1"] == "caught" and got["K4"] == "disclosed" and got["K2"] == "absent"


def _job(tmp: Path) -> Path:
    job = tmp / "job"
    (job / "text").mkdir(parents=True)
    pages = {
        "bill.txt": (
            "Hospital Bill.pdf",
            "[p.1]\nStatement. Total charges billed: $8,300.00 for emergency services.\n",
        ),
        "ortho.txt": (
            "Ortho Notes.pdf",
            "[p.1]\nEvaluation of the neck.\n[p.2]\nPatient underwent epidural steroid injection with partial relief.\n",
        ),
    }
    rows = []
    for fname, (name, text) in pages.items():
        (job / "text" / fname).write_text(text)
        rows.append({"name": name, "text_path": f"/seat/data/job/text/{fname}"})  # a copied job: paths do not resolve
    (job / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return job


def test_score_draft_grades_pins_quotes_and_figures(tmp_path: Path) -> None:
    job = _job(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text(
        'He "underwent epidural steroid injection with partial relief" (FILE: Ortho Notes.pdf, p. 2). '
        'Again, "underwent epidural steroid injection with partial relief" (FILE: Ortho Notes.pdf, p. 1). '
        "The hospital billed $9,100.00 (FILE: Hospital Bill.pdf, p. 1). "
        "The bill shows $8,300.00 (FILE: Hospital Bill.pdf, p. 1). "
        "See also (FILE: Missing.pdf, p. 4) and (FILE: Hospital Bill.pdf, p. 9).\n"
    )
    r = score_draft.report(job, draft)
    assert r["documents"] == 2
    assert r["quotes"] == {"ok": 1, "off_by_one": 1}
    assert r["figures"] == {"not_on_page": 1, "ok": 1}
    assert r["pins"] == {"resolves": 4, "unknown_file": 1, "no_such_page": 1}
    lint = score_draft.runner_path.lint()
    if lint is None:
        assert r["lint"] is None
    else:
        assert r["lint"] and r["lint"].get("hand_money", 0) >= 2


def test_score_draft_with_a_key(tmp_path: Path) -> None:
    job = _job(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("The hospital billed $8,300.00 (FILE: Hospital Bill.pdf, p. 1).\n")
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"rows": [FIG]}))
    r = score_draft.report(job, draft, key)
    assert r["key"]["counts"]["fixed"] == 1


def test_assert_spans_glue_passes_a_rendered_brief_and_fails_an_edited_span(tmp_path: Path) -> None:
    glue = _load(PROOF / "assert_spans.py", "assert_spans_glue")
    render = glue.runner_path.render()
    if render is None:
        pytest.skip("evidence.render not in this tree yet")
    from jobs.drafting.evidence.shapes import Fact, Ledger

    led = Ledger(
        (
            Fact(
                "F0001",
                "Hospital Bill.pdf",
                1,
                "figure",
                "Total charges billed: $8,300.00",
                "c",
                role="billed",
                value="8300.00",
                unit="usd",
            ),
        )
    )
    r = render.render("The hospital billed {{N:F0001}} {{F:F0001}}.", led)
    (tmp_path / "ledger.jsonl").write_text(led.to_jsonl())
    (tmp_path / "spans.jsonl").write_text("".join(json.dumps(x.to_json()) + "\n" for x in r.spans))
    (tmp_path / "brief.md").write_text(r.text)
    args = [tmp_path / "ledger.jsonl", tmp_path / "spans.jsonl", tmp_path / "brief.md"]
    assert glue.run(*args)["ok"] is True
    (tmp_path / "brief.md").write_text(r.text.replace("8,300.00", "8,800.00"))
    bad = glue.run(*args)
    assert bad["ok"] is False and bad["problems"]


def test_authority_fetch_writes_what_the_store_reads(tmp_path: Path) -> None:
    af = _load(OPERATOR / "templates" / "drafting" / "authority_fetch.py", "authority_fetch")
    for c in (
        "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541",
        "Civ. Code, § 3333.4",
        "CACI No. 3903A",
    ):
        assert af.slug(c) == authorities.slug(c)
        assert af.render_entry(c, "https://x.test", "2026-11-04", "T.") == authorities.render_entry(
            c, "https://x.test", "2026-11-04", "T."
        )
    af.write_entry(tmp_path, "Civ. Code, § 3333.4", "https://leginfo.legislature.ca.gov/x", "Section text.", now="t")
    st = authorities.Store.from_dir(tmp_path)
    assert st.citation(authorities.slug("Civ. Code, § 3333.4")) == "Civ. Code, § 3333.4"


# ---- defects the $0 proof run found (2026-11-04) --------------------------------------------


def test_the_hand_verification_key_reads_as_jsonl_with_its_own_type_names(tmp_path: Path) -> None:
    rows = [
        {**FIG, "id": "J1", "type": "wrong_number_or_count"},
        {**QUOTE, "id": "J2", "type": "misquote"},
        {**PIN, "id": "J3", "type": "wrong_pin_cite"},
        {**OMIT, "id": "J4", "type": "overstated_causation"},
    ]
    key = "\n".join(json.dumps(r) for r in rows) + "\n"
    got = [scorer.KeyRow.from_json(r).type for r in scorer.load_key(key)]
    assert got == ["figure", "quote", "pin", "characterization"]
    job = _job(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("The hospital billed $8,300.00 (FILE: Hospital Bill.pdf, p. 1).\n")
    (tmp_path / "key.jsonl").write_text(key)
    r = score_draft.report(job, draft, tmp_path / "key.jsonl")
    assert r["key"]["by_type"]["figure"]["fixed"] == 1


def test_the_old_lanes_cite_forms_are_scored() -> None:
    old = (
        "The epidural steroid injection gave partial relief of radicular symptoms (Ortho Notes, p. 12). "
        "The hospital billed $83,000.00 for emergency services (Hospital Bill, p. 1)."
    )
    got = _outcomes(old)
    assert got["K3"] == "missed" and got["K1"] == "missed", got
    assert ("dep jones", 44) in scorer.cites("He conceded it (Jones Dep. 44:15-17).")
    assert scorer.cites("(FILE: Ortho Notes.pdf, p. 3)") == [("ortho notes", 3)]


def test_a_corrected_sentence_is_not_read_as_the_wrong_one() -> None:
    """A row with no wrong_pattern: the corrected sentence shares most of the
    wrong wording's words, and must not be scored as the wrong statement."""
    row = {**QUOTE, "wrong_pattern": ""}
    corrected = (
        "It is not the case that the patient denied any prior neck injury whatsoever: the patient reports a "
        "prior neck strain in 2015, resolved (FILE: Ortho Notes.pdf, p. 3)."
    )
    [r] = scorer.score([row], corrected).rows
    assert r.outcome == "fixed", (r.outcome, r.why)


def _span_job(tmp: Path) -> Path:
    """A new-lane job: a ledger, a render span map and a check report."""
    from jobs.drafting.evidence import render as ev_render
    from jobs.drafting.evidence.shapes import Fact, Ledger

    job = _job(tmp)
    led = Ledger(
        (
            Fact(
                "F0001",
                "Hospital Bill.pdf",
                1,
                "figure",
                "Total charges billed: $8,300.00 for emergency services.",
                "billed",
                role="billed",
                value="8300.00",
                unit="usd",
            ),
            Fact("F0002", "Ortho Notes.pdf", 2, "fact", "Patient underwent epidural steroid injection.", "esi"),
        )
    )
    r = ev_render.render("The hospital billed {{N:F0001}} {{F:F0001}}. He had an injection {{F:F0002}}.", led)
    (job / "evidence").mkdir()
    (job / "evidence" / "ledger.jsonl").write_text(led.to_jsonl())
    (job / "evidence-spans.json").write_text(json.dumps([s.to_json() for s in r.spans]))
    check = {"items": [{"grade": "framing", "sentence": "the clinic noted a gap in treatment", "control": ""}]}
    (job / "check.json").write_text(json.dumps(check))
    (job / "draft.md").write_text(r.text)
    return job


def test_a_new_lane_draft_is_scored_from_its_render_span_map(tmp_path: Path) -> None:
    job = _span_job(tmp_path)
    pin = {**PIN, "source_file": "Ortho Notes", "source_page": 2, "correct_value": "p. 2", "wrong_as_stated": "p. 1"}
    wrong_fig = {**FIG, "id": "K9", "source_file": "Hospital Bill", "correct_value": "$9,100.00"}
    rows = [{**FIG, "source_file": "Hospital Bill"}, wrong_fig, pin, OMIT, {**QUOTE, "id": "K8"}]
    (tmp_path / "key.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "notes.md").write_text("## Attorney notes\n\nNothing further.\n")
    r = score_draft.report(job, job / "draft.md", tmp_path / "key.jsonl", tmp_path / "notes.md")
    got = {x["id"]: x["outcome"] for x in r["key"]["rows"]}
    assert r["key_method"] == "spans"
    assert got == {"K1": "fixed", "K9": "missed", "K3": "fixed", "K4": "caught", "K8": "absent"}, got


def test_key_v2_rows_match_by_source_id_transcript_page_and_count() -> None:
    from jobs.drafting.evidence import render as ev_render
    from jobs.drafting.evidence.shapes import Fact, Ledger

    led = Ledger(
        (
            Fact("F0001", "Letter (Correspondence)", 4, "fact", "Thank you for your letter.", "c"),
            Fact("F0002", "Letter (Medical)", 2, "fact", "Patient seen for neck pain.", "m"),
            Fact("F0003", "Smith Depo", 40, "testimony", "I had no neck pain before.", "t", tpage=38, lines=(4, 6)),
            Fact("F0004", "ER record", 1, "fact", "Visit one.", "v1"),
            Fact("F0005", "Ortho notes", 1, "fact", "Visit two.", "v2"),
        )
    )
    r = ev_render.render(
        "A {{F:F0001}}. B {{F:F0002}}. C {{F:F0003}}. He made {{COUNT:F0004,F0005}} visits {{F:F0004,F0005}}.", led
    )
    names = {"d1": "Letter (Correspondence)", "d2": "Letter (Medical)"}
    rows = [
        # a pin to the shared name: only the medical copy's page counts
        {
            "id": "V1",
            "type": "wrong_pin_cite",
            "source_file": "Letter",
            "source_id": "d2",
            "source_page": 2,
            "wrong_page": 4,
        },
        {
            "id": "V2",
            "type": "wrong_pin_cite",
            "source_file": "Letter",
            "source_id": "d2",
            "source_page": 4,
            "wrong_page": 9,
        },
        # a transcript pin: compared on the transcript page, never the file page
        {
            "id": "V3",
            "type": "wrong_pin_cite",
            "source_file": "Smith Depo",
            "source_page": 40,
            "source_tpage": 38,
            "wrong_tpage": 40,
        },
        {
            "id": "V4",
            "type": "wrong_pin_cite",
            "source_file": "Smith Depo",
            "source_page": 40,
            "source_tpage": 41,
            "wrong_tpage": 38,
        },
        # a visit count: compared by integer value
        {
            "id": "V5",
            "type": "wrong_number_or_count",
            "source_file": "ER record",
            "correct_value": "2",
            "wrong_as_stated": "5",
        },
        {
            "id": "V6",
            "type": "wrong_number_or_count",
            "source_file": "ER record",
            "correct_value": "3",
            "wrong_as_stated": "2",
        },
    ]
    got = {x.row.id: x.outcome for x in scorer.score_spans(rows, r.spans, led, None, "", names).rows}
    assert got == {"V1": "fixed", "V2": "absent", "V3": "fixed", "V4": "missed", "V5": "fixed", "V6": "missed"}, got


def test_score_draft_keeps_every_row_when_names_repeat(tmp_path: Path) -> None:
    job = _job(tmp_path)
    (job / "text" / "ortho2.txt").write_text("[p.1]\nA second filing under the same name.\n")
    rows = [json.loads(ln) for ln in (job / "extracted.jsonl").read_text().splitlines()]
    rows.append({"name": "Ortho Notes.pdf", "folder": "Pleadings", "text_path": "/seat/data/job/text/ortho2.txt"})
    rows[1]["folder"] = "Medical"
    (job / "extracted.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    corpus = score_draft.Corpus.from_job(job)
    assert len(corpus.docs) == 3, sorted(corpus.docs)
    assert corpus.doc("ortho notes") is not None


def test_score_draft_reads_docx_tables_and_a_name_in_text_and_email(tmp_path: Path) -> None:
    import docx

    job = _job(tmp_path)
    (job / "email").mkdir()
    (job / "email" / "bill.txt").write_text("[p.1]\nAn email about the bill, no figures.\n")
    d = docx.Document()
    d.add_paragraph("Charges follow.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "Exampletown Hospital"
    t.rows[0].cells[1].text = "$8,300.00 (FILE: Hospital Bill.pdf, p. 1)"
    path = tmp_path / "draft.docx"
    d.save(str(path))
    r = score_draft.report(job, path)
    assert r["documents"] == 2, "the bill's text resolves through its stored directory, not dropped as ambiguous"
    assert r["figures"] == {"ok": 1}, r
