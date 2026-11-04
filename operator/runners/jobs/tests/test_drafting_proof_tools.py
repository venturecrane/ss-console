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
    "wrong_pattern": "",
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
    "wrong_pattern": "",
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
