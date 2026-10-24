"""The independent review of 2026-10-24, findings B2 and N2 (B1 is in
``test_review_stop_rules``).

* B2. The worksheet's What Is Needed told the firm "the file holds individual
  claim forms but no account ledger" for every gap row, including a provider
  with no bill at all. Each paragraph is now true of every row under it.
* N2. The review's leave-out ran after billing_chart and billing_docx, so a
  left-out provider's row and its bills stayed in the worksheet and the grand
  total. Both rerun when the review leaves an entry out.
"""

from __future__ import annotations

import json
from pathlib import Path

from medchron.stages import billing_chart, billing_docx
from medchron.stages.base import StageRun
from test_final_stages import DOC
from test_finish_doc import _doc, _sr
from test_review_proof import FakeDoorway, _package, _settle_review, _verdict
from test_review_stop_rules import CHECK1, _pages


def _worksheet_text(sr: StageRun) -> str:
    """The worksheet's paragraphs and table cells in document order, one line
    each, every run read once."""
    from docx import Document

    out = list((sr.slug_dir / "out" / "alpha").glob("* - Medical Billing Worksheet *.docx"))
    assert len(out) == 1
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = Document(str(out[0])).element.body
    return "\n".join("".join(t.text or "" for t in p.iter(f"{w}t")) for p in body.iter(f"{w}p"))


CLAIM_FORMS = "the file holds individual claim forms but no account ledger"
NO_BILL = "no bill was found in the file"

TWO_PROVIDERS = (
    "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n"
    "| Medical Provider | Treatment Period | Visits | Reference |\n"
    "| Example Clinic | 01/02/2026 | 1 | Exhibit 1 - p. 3 |\n"
    "| Other Ortho | 01/05/2026 | 1 | Exhibit 1 - p. 4 |\n\n"
    "## Diagnostic Highlights\n\n| ICD Code | Description | First Diagnosed | Reference |\n\n"
    "## Medical Chronology\n\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nCervical strain noted. (Exhibit 1 - p. 3)\n\n"
    "01/05/2026\nOther Ortho | Medical Diagnoses\n\nShoulder strain noted. (Exhibit 1 - p. 4)\n\n"
    "## Exhibit List\n\n| Exhibit No. | Description |\n"
)


def _claim_form(provider: str, amount: str) -> dict:
    return {
        "file": f"{provider} claim form",
        "pages": 1,
        "failures": [],
        "chunks": [
            {
                "doc_type": "MEDICAL_BILL",
                "provider": provider,
                "patient": "Alpha Example",
                "printed_totals": [{"label": "28. TOTAL CHARGE", "amount": amount, "page": 1}],
            }
        ],
    }


def _ledger(provider: str, amount: str) -> dict:
    return {
        "file": f"{provider} ledger",
        "pages": 1,
        "failures": [],
        "chunks": [
            {
                "doc_type": "LEDGER",
                "provider": provider,
                "patient": "Alpha Example",
                "date_first": "03/30/2026",
                "date_last": "03/30/2026",
                "printed_totals": [{"label": "Total Charges", "amount": amount, "page": 1}],
            }
        ],
    }


def _extract(sr: StageRun, *records: dict) -> None:
    (sr.slug_dir / "billing_extract.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


# ---- B2 --------------------------------------------------------------------------
def test_what_is_needed_says_claim_forms_only_of_rows_that_have_them(
    job_dir: Path, firm_config_path: Path, data_root: Path
):
    """One provider with a claim form and no ledger, one with no bill at all:
    each gets the sentence true of it, under its own paragraph."""
    sr = _sr(job_dir, firm_config_path, data_root)
    _doc(sr, TWO_PROVIDERS)
    _extract(sr, _claim_form("Example Clinic", "$465.00"))
    assert billing_chart.run(sr) == 0
    rows = {r["provider"]: r["basis"] for r in json.loads((sr.slug_dir / "billing_chart.json").read_text())["rows"]}
    assert rows["Example Clinic"].startswith("NOT DERIVABLE - no ledger") and rows["Other Ortho"] == "no bill located"
    assert billing_docx.run(sr) == 0
    text = _worksheet_text(sr)
    needed = text[text.index("What Is Needed") :]
    assert needed.count(CLAIM_FORMS) == 1 and needed.count(NO_BILL) == 1, needed
    split = max(needed.index(CLAIM_FORMS), needed.index(NO_BILL))
    head, tail = needed[:split], needed[split:]
    forms, nobill = (head, tail) if CLAIM_FORMS in head else (tail, head)
    assert "Example Clinic" in forms and "Other Ortho" not in forms
    assert "Other Ortho" in nobill and "Example Clinic" not in nobill


def test_no_bill_rows_alone_never_mention_claim_forms(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    _doc(sr, TWO_PROVIDERS)
    _extract(sr)
    assert billing_chart.run(sr) == 0
    assert billing_docx.run(sr) == 0
    needed = _worksheet_text(sr)
    needed = needed[needed.index("What Is Needed") :]
    assert "claim form" not in needed, needed
    assert NO_BILL in needed and "Example Clinic" in needed and "Other Ortho" in needed


# ---- N2 --------------------------------------------------------------------------
LEFT_DOC = DOC.replace(
    "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 | Exhibit 1 - p. 2 |",
    "| Example Clinic | 01/20/2026 | 1 | Exhibit 1 - p. 2 |\n| Other Ortho | 03/30/2026 | 1 | Exhibit 1 - p. 3 |",
).replace(
    "03/30/2026\nExample Clinic | Treatment Recommendations", "03/30/2026\nOther Ortho | Treatment Recommendations"
)


def test_an_entry_left_out_takes_its_providers_row_out_of_the_worksheet(job_dir, firm_config_path, data_root):
    """The 03/30 entry's page names another person, so the review leaves it
    out. Its provider had no other entry: the worksheet is rebuilt, the row
    goes, and its bill (matching no chronology provider now) is shown as
    printed under its own subtotal, outside the grand total."""
    sr, log = _package(job_dir, firm_config_path, data_root, LEFT_DOC)
    assert "| Other Ortho | 03/30/2026 | 1 |" in LEFT_DOC
    _extract(sr, _ledger("Example Clinic", "$1,800.00"), _ledger("Other Ortho", "$900.00"))
    assert billing_chart.run(sr) == 0 and billing_docx.run(sr) == 0
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    assert [r["provider"] for r in chart["rows"]] == ["Example Clinic", "Other Ortho"] and chart[
        "grand_total"
    ] == 2700.0
    _pages(sr, "Patient: Beta Roe DOB: 02/02/1980. Continue therapy.")
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    assert [r["provider"] for r in chart["rows"]] == ["Example Clinic"], chart["rows"]
    assert chart["grand_total"] == 1800.0, "the left-out provider's bill is not in the grand total"
    assert [r["provider"] for r in chart["as_printed_rows"]] == ["Other Ortho"]
    assert chart["as_printed_subtotal"] == 900.0
    text = _worksheet_text(sr)
    assert "$1,800.00" in text and "$2,700.00" not in text
    assert any("billing chart and worksheet rebuilt" in ln for ln in log)


# ---- the re-review's N2 gap: a timeline row deleted under check 4 ----------------
ORPHAN_DOC = DOC.replace(
    "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 | Exhibit 1 - p. 2 |",
    "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 | Exhibit 1 - p. 2 |\n| Other Ortho | 02/14/2026 | 1 | Exhibit 1 - p. 3 |",
)
CHECK4 = (
    '| MUST-FIX | 4 summary with no entry | the timeline row "Other Ortho" has no entry behind it '
    "| Treatment Timeline |"
)


def test_a_timeline_row_deleted_under_check4_takes_its_provider_out_of_the_worksheet(
    job_dir, firm_config_path, data_root
):
    """The review deletes a Treatment Timeline row no entry supports (check 4).
    That provider leaves the billing spine too: the worksheet is rebuilt, the
    row goes, and its bill is shown as printed, outside the grand total."""
    sr, log = _package(job_dir, firm_config_path, data_root, ORPHAN_DOC)
    _extract(sr, _ledger("Example Clinic", "$1,800.00"), _ledger("Other Ortho", "$900.00"))
    assert billing_chart.run(sr) == 0 and billing_docx.run(sr) == 0
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    assert [r["provider"] for r in chart["rows"]] == ["Example Clinic", "Other Ortho"] and chart[
        "grand_total"
    ] == 2700.0
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK4))) == 0, log
    md = (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()
    assert "| Other Ortho |" not in md, "the review deleted the row"
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    assert [r["provider"] for r in chart["rows"]] == ["Example Clinic"], chart["rows"]
    assert chart["grand_total"] == 1800.0
    assert [r["provider"] for r in chart["as_printed_rows"]] == ["Other Ortho"]
    assert "$2,700.00" not in _worksheet_text(sr)


# ---- the re-review's N5: a one-word label does not merge a system's facilities ----
BANNER_DOC = (
    "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n"
    "| Medical Provider | Treatment Period | Visits | Reference |\n"
    "| Banner Desert Medical Center | 01/02/2026 | 1 | Exhibit 1 - p. 3 |\n"
    "| Banner Thunderbird Medical Center | 01/05/2026 | 1 | Exhibit 1 - p. 4 |\n\n"
    "## Diagnostic Highlights\n\n| ICD Code | Description | First Diagnosed | Reference |\n\n"
    "## Medical Chronology\n\n01/02/2026\nBanner Desert Medical Center | Medical Diagnoses\n\n"
    "Cervical strain noted. (Exhibit 1 - p. 3)\n\n"
    "01/05/2026\nBanner Thunderbird Medical Center | Medical Diagnoses\n\nShoulder strain noted. (Exhibit 1 - p. 4)\n\n"
    "## Exhibit List\n\n| Exhibit No. | Description |\n"
)


def test_a_one_word_label_keeps_a_systems_facilities_separate(job_dir: Path, firm_config_path: Path, data_root: Path):
    """A ledger labelled only "Banner" once merged both Banner hospitals into
    one row whose total was that single ledger, which reads as both
    facilities' specials. The rows stay separate, neither claims the ledger,
    each names the label, and the ledger is shown as printed, outside the
    grand total."""
    sr = _sr(job_dir, firm_config_path, data_root)
    _doc(sr, BANNER_DOC)
    _extract(sr, _ledger("Banner", "$5,000.00"))
    assert billing_chart.run(sr) == 0
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    by = {r["provider"]: r for r in chart["rows"]}
    assert set(by) == {"Banner Desert Medical Center", "Banner Thunderbird Medical Center"}, by
    for r in by.values():
        assert r["total"] is None and '"Banner"' in r["basis"] and "more than one provider" in r["basis"]
    assert chart["grand_total"] == 0.0
    assert [(r["provider"], r["total"]) for r in chart["as_printed_rows"]] == [("Banner", 5000.0)]
    assert billing_docx.run(sr) == 0
    text = _worksheet_text(sr)
    assert "INCOMPLETE" in text and "claim form" not in text[text.index("What Is Needed") :]


def test_a_two_word_label_still_merges_and_a_one_word_label_does_not():
    from medchron.stages import billing_match as BM

    assert not BM.merges("Banner") and not BM.merges("Banner Health")
    assert BM.merges("NV Ridge Health") and BM.merges("Banner Desert")
