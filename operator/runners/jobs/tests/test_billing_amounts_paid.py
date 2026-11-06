"""Amounts paid on the billing worksheet, only where a bill prints them.

The billing transcription copied charges and printed totals and nothing a bill
says was paid, so a firm asking for the amounts paid got a worksheet with none
(the answer key's O1). The transcription now asks for every printed payment,
and the worksheet lists each one by the bill's provider, document and page. A
bill that prints no payment adds nothing; no payment is ever added up, taken
from a total, or invented.

A test on real billing pages (2026-11-06) found the transcription exact and the
worksheet not: it re-rendered amounts, cut the printed words at 28 characters,
collapsed identical payment lines, and never showed a payment-labelled printed
total. The tests below pin each of those (jobs/stages/billing_paid.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs.stages import billing, billing_chart, billing_docx
from test_finish_doc import _doc, _sr
from test_review_billing_findings import TWO_PROVIDERS, _extract, _ledger, _worksheet_text

HEADING = "Amounts Paid, as Printed on the Bills"


def _paid_ledger(provider: str, amount: str, payments: list[dict]) -> dict:
    rec = _ledger(provider, amount)
    rec["chunks"][0]["printed_payments"] = payments
    return rec


def _run(sr) -> str:
    assert billing_chart.run(sr) == 0
    assert billing_docx.run(sr) == 0
    return _worksheet_text(sr)


def test_the_transcription_asks_for_printed_payments():
    assert '"printed_payments"' in billing.SCHEMA_NOTE
    assert "If\n  no payment is printed, printed_payments is []." in billing.SCHEMA_NOTE
    assert "printed payments" in billing.TOTALS_ONLY


def test_a_payment_the_bill_prints_is_on_the_worksheet(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    _doc(sr, TWO_PROVIDERS)
    pay = [
        {"label": "Insurance payment", "amount": "$612.40", "date": "02/10/2026", "page": 1},
        {"label": "Patient payment", "amount": "$25.00", "date": None, "page": 1},
    ]
    _extract(sr, _paid_ledger("Example Clinic", "$1,800.00", pay))
    text = _run(sr)
    assert HEADING in text
    tail = text[text.index(HEADING) :]
    for want in ("Example Clinic", "Example Clinic ledger", "p. 1", "Insurance payment", "$612.40", "02/10/2026"):
        assert want in tail, want
    assert "Patient payment" in tail and "$25.00" in tail
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    assert chart["grand_total"] == 1800.0, "a payment is never taken from the billed total"
    assert "$637.40" not in text and "$1,187.60" not in text, "payments are never added up or subtracted"


def test_a_bill_that_prints_no_payment_renders_nothing(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    _doc(sr, TWO_PROVIDERS)
    _extract(sr, _ledger("Example Clinic", "$1,800.00"), _paid_ledger("Other Ortho", "$900.00", []))
    text = _run(sr)
    assert HEADING not in text and "Amount Paid" not in text


def test_an_unreadable_payment_or_a_vendors_is_left_out(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    _doc(sr, TWO_PROVIDERS)
    vendor = {
        "file": "records vendor invoice",
        "pages": 1,
        "failures": [],
        "chunks": [
            {
                "doc_type": "VENDOR_INVOICE",
                "provider": "Example Copy Service",
                "printed_totals": [],
                "printed_payments": [{"label": "Paid by firm", "amount": "$75.00", "page": 1}],
            }
        ],
    }
    blurred = [{"label": "Payment", "amount": "$1,?00.00", "page": 1}]
    _extract(sr, _paid_ledger("Example Clinic", "$1,800.00", blurred), vendor)
    text = _run(sr)
    assert HEADING not in text
    assert "$75.00" not in text and "Paid by firm" not in text


# ---- the worksheet prints each payment exactly as the bill prints it ---------------
def _tail(sr, *payments: dict, totals: tuple[dict, ...] = ()) -> list[str]:
    """The worksheet's lines from the payments heading on, after one Example
    Clinic ledger carrying these payments and extra printed totals."""
    _doc(sr, TWO_PROVIDERS)
    rec = _paid_ledger("Example Clinic", "$1,800.00", list(payments))
    rec["chunks"][0]["printed_totals"] += list(totals)
    _extract(sr, rec)
    text = _run(sr)
    assert HEADING in text
    return text[text.index(HEADING) :].splitlines()


def test_each_amount_is_printed_character_for_character(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    amounts = ("-$25.00", "$-25.00", "25.00", "(25.00)", "$0.00")
    pay = [{"label": f"Payment line {i}", "amount": a, "date": "02/10/2026", "page": 1} for i, a in enumerate(amounts)]
    lines = _tail(sr, *pay)
    for a in amounts:
        assert a in lines, f"{a!r} is not on the worksheet as printed"


def test_the_printed_words_are_never_cut(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    label = "Insurance payment received from Example Mutual Casualty Company, claim reference"
    assert len(label) > 60
    lines = _tail(sr, {"label": label, "amount": "$40.00", "date": None, "page": 1})
    assert label in lines


def test_three_identical_payment_lines_are_three_rows(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    same = {"label": "Patient payment", "amount": "$0.00", "date": "02/10/2026", "page": 1}
    lines = _tail(sr, dict(same), dict(same), dict(same))
    assert lines.count("Patient payment") == 3


def test_a_you_paid_printed_total_is_one_printed_total_row(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    lines = _tail(sr, totals=({"label": "You Paid", "amount": "$0.00", "page": 1},))
    assert lines.count("You Paid") == 1
    i = lines.index("You Paid")
    assert "Printed total" in lines[i : i + 4] and "$0.00" in lines[i : i + 4]


def test_a_printed_total_matching_a_payment_line_is_not_shown_twice(
    job_dir: Path, firm_config_path: Path, data_root: Path
):
    sr = _sr(job_dir, firm_config_path, data_root)
    pay = {"label": "Amount paid by patient", "amount": "$75.00", "date": "02/10/2026", "page": 1}
    lines = _tail(sr, pay, totals=({"label": "Amount paid by patient", "amount": "$75.00", "page": 1},))
    assert lines.count("Amount paid by patient") == 1 and "Printed total" not in lines


def test_payments_and_adjustments_together_have_their_own_sub_heading(
    job_dir: Path, firm_config_path: Path, data_root: Path
):
    sr = _sr(job_dir, firm_config_path, data_root)
    pay = {"label": "Patient payment", "amount": "$25.00", "date": "02/10/2026", "page": 1}
    lines = _tail(sr, pay, totals=({"label": "Payments/Adjustments", "amount": "$140.00", "page": 1},))
    sub = 'Printed as "Payments/Adjustments"'
    assert sub in lines
    assert lines.index("$140.00") > lines.index(sub) > lines.index("$25.00")
    assert lines.count("$140.00") == 1 and "Printed total" not in lines


def test_a_balance_due_total_is_not_in_the_section(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    pay = {"label": "Patient payment", "amount": "$25.00", "date": "02/10/2026", "page": 1}
    due = (
        {"label": "Balance Due", "amount": "$333.00", "page": 1},
        {"label": "Payment Due", "amount": "$444.00", "page": 1},
    )
    lines = _tail(sr, pay, totals=due)
    for gone in ("Balance Due", "$333.00", "Payment Due", "$444.00"):
        assert gone not in lines


# ---- the label rule reads a bill's abbreviations, and applies to payment lines too --
def test_the_label_rule_reads_the_abbreviations_bills_print():
    from jobs.stages import billing_paid as BP

    want = {
        BP.PAYMENTS_AND_ADJUSTMENTS: (
            "Insurance Pymts/Adjstmts",
            "Patient Pymts/Adjstmts",
            "Payments/Adjustments",
            "Total: Payments/Adjustments",
            "Pmts & Adjs",
            "Payment Adj.",
            "Ins Pmt/Adjmt",
            "Payments and w/o",
            "Pymts/Writeoff",
            "Pmt Contr Adj",
        ),
        BP.PAID: ("Grand Total: You paid", "Amount Paid", "Pmt", "Ins Pymt", "Adjacent Clinic payment"),
        BP.ADJUSTMENT: ("Adjustment", "Adj.", "Adjs", "Adjmt", "W/O", "Writeoff", "Contr Adj"),
        BP.BALANCE_DUE: ("Balance Due", "Payment Due", "Patient Responsibility"),
        BP.OTHER: ("Total Charges", "Adjacent services", ""),
    }
    for kind, labels in want.items():
        for label in labels:
            assert BP.classify(label) == kind, (label, BP.classify(label), kind)


def test_a_payment_line_naming_payments_and_adjustments_is_under_its_own_words(
    job_dir: Path, firm_config_path: Path, data_root: Path
):
    sr = _sr(job_dir, firm_config_path, data_root)
    pay = {"label": "Patient payment", "amount": "$25.00", "date": "02/10/2026", "page": 1}
    both = {"label": "Insurance Pymts/Adjstmts", "amount": "$410.00", "date": "02/12/2026", "page": 1}
    lines = _tail(sr, pay, both)
    sub = 'Printed as "Insurance Pymts/Adjstmts"'
    assert sub in lines
    assert lines.count("$410.00") == 1 and lines.index("$410.00") > lines.index(sub) > lines.index("$25.00")
    assert lines.count("Insurance Pymts/Adjstmts") == 0, "the label is the sub-heading, not a payment row"


# ---- a figure the same page prints as payments-and-adjustments is not a payment ----
def _combined_page(bare_page: int = 1, bare_amount: str = "1,200.00") -> list[dict]:
    return [
        {"label": "Payments", "amount": bare_amount, "date": None, "page": bare_page},
        {"label": "Insurance Pymts/Adjstmts", "amount": "1,200.00", "date": None, "page": 1},
        {"label": "Patient Pymts/Adjstmts", "amount": "50.00", "date": None, "page": 1},
    ]


def _payment_rows(lines: list[str]) -> list[str]:
    """The payments table's lines: from its header to the first sub-heading."""
    start = lines.index("Printed As")
    end = next((i for i, x in enumerate(lines) if x.startswith('Printed as "')), len(lines))
    return lines[start:end] if start < end else []


def test_a_payment_line_repeating_a_combined_figure_on_its_page_is_not_a_payment(
    job_dir: Path, firm_config_path: Path, data_root: Path
):
    sr = _sr(job_dir, firm_config_path, data_root)
    pay = {"label": "Patient payment", "amount": "$25.00", "date": "02/10/2026", "page": 1}
    lines = _tail(sr, pay, *_combined_page())
    assert "Payments" not in _payment_rows(lines) and "Patient payment" in _payment_rows(lines)
    assert lines.count("1,200.00") == 1 and lines.index("1,200.00") > lines.index(
        'Printed as "Insurance Pymts/Adjstmts"'
    )
    assert lines.index("50.00") > lines.index('Printed as "Patient Pymts/Adjstmts"')


def test_the_same_figure_on_another_page_stays_a_payment(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    lines = _tail(sr, *_combined_page(bare_page=2))
    rows = _payment_rows(lines)
    assert "Payments" in rows and "1,200.00" in rows and lines.count("1,200.00") == 2


def test_a_differently_printed_figure_stays_a_payment(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    lines = _tail(sr, *_combined_page(bare_amount="1200.00"))
    rows = _payment_rows(lines)
    assert "Payments" in rows and "1200.00" in rows and lines.count("1,200.00") == 1


def test_a_document_with_no_combined_line_keeps_its_payments(job_dir: Path, firm_config_path: Path, data_root: Path):
    sr = _sr(job_dir, firm_config_path, data_root)
    lines = _tail(sr, {"label": "Payments", "amount": "1,200.00", "date": None, "page": 1})
    assert (
        "Payments" in _payment_rows(lines)
        and "1,200.00" in lines
        and not any(x.startswith('Printed as "') for x in lines)
    )
