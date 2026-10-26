"""Amounts paid on the billing worksheet (medchron/billing_paid.py).

A firm asked for "both total charges and amounts paid"; the worksheet showed
charges only and said nothing. The payments were already transcribed as
printed totals; these tests pin that they reach the worksheet exactly as
printed, with citations, summed only when the sum cannot double-count, and
that a balance or an amount due is never shown as paid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medchron import billing_paid as BP
from medchron.stages import billing_chart, billing_docx, limitations_rules
from test_finish_doc import _doc, _sr
from test_review_billing_findings import TWO_PROVIDERS, _extract, _worksheet_text


@pytest.mark.parametrize(
    "label",
    [
        "insurance payments",
        "patient payments",
        "previous payments",
        "payment received on 03/02/2026 by account end",
        "Alpha Example total - you paid",
        "grand total - you paid",
        "Ins Pmt",
        "29. AMOUNT PAID",
    ],
)
def test_payment_labels_are_paid(label: str) -> None:
    assert BP.classify(label) == BP.PAID


@pytest.mark.parametrize(
    "label",
    ["account balance", "total balance", "payment due", "amount due", "total payable", "unpaid charges", "Balance Due"],
)
def test_owed_labels_are_never_paid(label: str) -> None:
    """The falsifiers: "total payable" and "payment due" carry a payment-like
    word and are what is owed."""
    assert BP.classify(label) == BP.BALANCE_DUE


@pytest.mark.parametrize("label", ["other adjustments", "refund adjustments", "contractual write-off"])
def test_adjustment_labels(label: str) -> None:
    assert BP.classify(label) == BP.ADJUSTMENT


def test_payments_and_adjustments_combined_is_its_own_class() -> None:
    assert BP.classify("Alpha Example total - payments/adjustments") == BP.PAYMENTS_AND_ADJUSTMENTS


@pytest.mark.parametrize("label", ["Total Charges", "28. TOTAL CHARGE", "copay", "", None])
def test_other_labels(label) -> None:
    assert BP.classify(label) == BP.OTHER


def _p(label: str, text: str, amount: float | None, file: str = "Example Clinic statement", page: int = 1) -> dict:
    return {"label": label, "amount_text": text, "amount": amount, "file": file, "page": page}


def test_summarize_sums_clean_payments_on_one_document() -> None:
    s = BP.summarize([_p("insurance payments", "-$300.00", -300.0), _p("patient payments", "$25.00", 25.0)])
    assert s["total"] == 325.0 and s["note"] == "" and len(s["figures"]) == 2


def test_summarize_does_not_sum_an_unreadable_amount() -> None:
    s = BP.summarize([_p("insurance payments", "$3?0.00", None), _p("patient payments", "$25.00", 25.0)])
    assert s["total"] is None and s["note"] == BP.NOT_SUMMED_UNREAD and len(s["figures"]) == 2


def test_summarize_does_not_sum_across_documents_or_total_labels() -> None:
    across = BP.summarize(
        [_p("previous payments", "$50.00", 50.0, "stmt 1"), _p("previous payments", "$50.00", 50.0, "stmt 2")]
    )
    assert across["total"] is None and across["note"] == BP.NOT_SUMMED_OVERLAP
    totals = BP.summarize(
        [_p("Alpha Example total - you paid", "$40.00", 40.0), _p("grand total - you paid", "$40.00", 40.0)]
    )
    assert totals["total"] is None and totals["note"] == BP.NOT_SUMMED_OVERLAP


def test_summarize_never_counts_a_balance_as_paid() -> None:
    s = BP.summarize([_p("amount due", "$900.00", 900.0), _p("total payable", "$900.00", 900.0)])
    assert s["figures"] == [] and s["total"] is None and len(s["balances"]) == 2


# ---- the worksheet -----------------------------------------------------------------
def _statement(provider: str, totals: list[tuple[str, str]]) -> dict:
    return {
        "file": f"{provider} statement",
        "pages": 2,
        "failures": [],
        "chunks": [
            {
                "doc_type": "LEDGER",
                "provider": provider,
                "patient": "Alpha Example",
                "date_first": "01/02/2026",
                "date_last": "01/02/2026",
                "printed_totals": [{"label": lab, "amount": amt, "page": 2} for lab, amt in totals],
            }
        ],
    }


def _build(job_dir: Path, firm: Path, data_root: Path, *records: dict):
    sr = _sr(job_dir, firm, data_root)
    _doc(sr, TWO_PROVIDERS)
    _extract(sr, *records)
    assert billing_chart.run(sr) == 0 and billing_docx.run(sr) == 0
    text = _worksheet_text(sr)
    return sr, text, text[text.index("Amounts Paid (as printed)") :]


def test_worksheet_shows_paid_with_citations_and_no_payment_printed(job_dir, firm_config_path, data_root) -> None:
    sr, _, paid = _build(
        job_dir,
        firm_config_path,
        data_root,
        _statement(
            "Example Clinic",
            [("Total Charges", "$1,000.00"), ("insurance payments", "$600.00"), ("patient payments", "$50.00")],
        ),
        _statement("Other Ortho", [("Total Charges", "$400.00"), ("amount due", "$400.00")]),
    )
    assert '$600.00 "insurance payments" (Example Clinic statement, p. 2)' in paid
    assert '$50.00 "patient payments" (Example Clinic statement, p. 2)' in paid
    assert "$650.00" in paid and BP.NO_PAYMENT in paid
    assert "SUBTOTAL PAID (incomplete)" in paid and "No payment printed: Other Ortho." in paid
    owed = paid[paid.index("Balances and Adjustments (as printed)") :]
    head = paid[: paid.index("Balances and Adjustments (as printed)")]
    assert '"amount due"' in owed and '"amount due"' not in head, "a due amount is never in the paid table"
    lim = limitations_rules._billing_lines(sr.slug_dir, "alpha", sr.log)
    assert BP.limitation_sentence(1) in lim


def test_worksheet_does_not_sum_an_amount_with_a_question_mark(job_dir, firm_config_path, data_root) -> None:
    _, _, paid = _build(
        job_dir,
        firm_config_path,
        data_root,
        _statement("Example Clinic", [("Total Charges", "$1,000.00"), ("insurance payments", "$6?0.00")]),
        _statement("Other Ortho", [("Total Charges", "$400.00"), ("patient payments", "$40.00")]),
    )
    assert '$6?0.00 "insurance payments"' in paid and BP.NOT_SUMMED_UNREAD in paid
    assert "SUBTOTAL PAID (incomplete)" in paid and "$40.00" in paid
    assert "not in the total paid: Example Clinic." in paid


def test_worksheet_total_paid_when_every_provider_prints_one(job_dir, firm_config_path, data_root) -> None:
    sr, _, paid = _build(
        job_dir,
        firm_config_path,
        data_root,
        _statement("Example Clinic", [("Total Charges", "$1,000.00"), ("insurance payments", "$600.00")]),
        _statement(
            "Other Ortho", [("Total Charges", "$400.00"), ("total payable", "$75.00"), ("patient payments", "$40.00")]
        ),
    )
    assert "TOTAL PAID" in paid and "$640.00" in paid and "$715.00" not in paid, "a payable figure is not paid"
    assert BP.NO_PAYMENT not in paid
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    assert [r["paid"]["total"] for r in chart["rows"]] == [600.0, 40.0]
    assert BP.limitation_sentence(1) not in "\n".join(limitations_rules._billing_lines(sr.slug_dir, "alpha", sr.log))
