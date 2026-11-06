"""Amounts paid, as printed on the bills, gathered for the worksheet. $0.

Every payment the transcription copied (``printed_payments``) is one row, in
file order, never merged with another: two bills that each print three
identical zero-dollar lines give six rows, as printed. The amount is the
string the transcription copied, character for character; the parsed value is
used only for the suspect-threshold rule, never to re-render the text.

A printed total (``printed_totals``) is sorted by its LABEL alone, in code
(ported from PR #3515's classifier): a balance-due label is checked FIRST and
never shown here; a label naming payments and adjustments together
("Payments/Adjustments") is not a payment and is kept apart under its own
words; a label naming a payment alone ("You Paid", "Amount Paid", "Total
Payments") is shown as its own row, marked as a printed total, unless an
itemized payment on the same document and page prints the same label and the
same amount string, in which case it is that row and is not shown twice.
Nothing here is summed.
"""

from __future__ import annotations

import re
from typing import Any

from .base import StageRun, read_jsonl
from .billing_chart import NON_PROVIDER, PageText, quarantined
from .billing_chart import money as parse_money

PAID = "PAID"
PAYMENTS_AND_ADJUSTMENTS = "PAYMENTS_AND_ADJUSTMENTS"
ADJUSTMENT = "ADJUSTMENT"
BALANCE_DUE = "BALANCE_DUE"
OTHER = "OTHER"

_DUE = re.compile(r"balance|\bdue\b|payable|\bowed?\b|\bowing\b|responsib|unpaid|not\s+paid", re.I)
_PAY = re.compile(r"\bpaid\b|\bpayments?\b|\bpmts?\b|\bpymts?\b", re.I)
_ADJ = re.compile(r"adjust|write.?off|contractual|disallow|discount|refund", re.I)

#: Bills whose payments are not this worksheet's: a vendor's invoice to the
#: firm, a page with no charges, and a health plan's claim (its own section).
PAID_SKIP = NON_PROVIDER | {"LIEN_SUBROGATION"}
NO_PROVIDER = "(no provider printed on the bill)"
PAYMENT_LINE, PRINTED_TOTAL = "Payment", "Printed total"


def classify(label: Any) -> str:
    """One printed total's class, from its label alone (PR #3515)."""
    lab = str(label or "").strip()
    if not lab:
        return OTHER
    if _DUE.search(lab):
        return BALANCE_DUE
    pay, adj = bool(_PAY.search(lab)), bool(_ADJ.search(lab))
    if pay and adj:
        return PAYMENTS_AND_ADJUSTMENTS
    if pay:
        return PAID
    return ADJUSTMENT if adj else OTHER


def _page(v: Any) -> int:
    return int(v) if str(v or "").isdigit() else 0


class _Reader:
    """The suspect-threshold rule for one matter's figures: the amount text
    must parse (no "?" digit), and one at the suspect line stays only when its
    page prints it with its decimal point. Brackets and a minus sign do not
    stop a figure being read; the value decides nothing but this rule."""

    def __init__(self, sr: StageRun) -> None:
        self.suspect_at = int(sr.cfg.get("billing", "suspect_amount_cents") or 50_000_000) / 100.0
        self.pages = PageText(sr.slug_dir)

    def readable(self, file: str, page: int, amount: Any) -> bool:
        text = str(amount or "").strip()
        bare = text.replace("(", "").replace(")", "").replace("-", "")
        flagged: list[tuple[str, float]] = []
        v = parse_money(bare, flagged, self.suspect_at)
        return v is not None and (not flagged or self.pages.prints(file, page, v)[0])


def _row(c: dict[str, Any], file: str, t: dict[str, Any], kind: str) -> dict[str, Any]:
    page = _page(t.get("page"))
    return {
        "provider": str(c.get("provider") or NO_PROVIDER),
        "file": file,
        "page": f"p. {page}" if page else "-",
        "label": str(t.get("label") or ""),
        "amount": str(t.get("amount") or ""),
        "date": str(t.get("date") or "-"),
        "kind": kind,
    }


def _key(t: dict[str, Any]) -> tuple[int, str, str]:
    return _page(t.get("page")), str(t.get("amount") or ""), str(t.get("label") or "")


def _chunk(rd: _Reader, file: str, c: dict[str, Any], itemized: set, paid: list, combined: list) -> None:
    """One chunk's payments, then its printed totals that name a payment;
    ``itemized`` holds every payment line the document prints."""
    for t in c.get("printed_payments") or []:
        if isinstance(t, dict) and rd.readable(file, _page(t.get("page")), t.get("amount")):
            paid.append(_row(c, file, t, PAYMENT_LINE))
    for t in c.get("printed_totals") or []:
        if not isinstance(t, dict) or not rd.readable(file, _page(t.get("page")), t.get("amount")):
            continue
        kind = classify(t.get("label"))
        if kind == PAYMENTS_AND_ADJUSTMENTS:
            combined.append(_row(c, file, t, PRINTED_TOTAL))
        elif kind == PAID and _key(t) not in itemized:
            paid.append(_row(c, file, t, PRINTED_TOTAL))


def gather(sr: StageRun, patient: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(payments, totals printed as payments and adjustments together), each a
    row {provider, file, page, label, amount, date, kind}, in file order."""
    rd = _Reader(sr)
    paid: list[dict[str, Any]] = []
    combined: list[dict[str, Any]] = []
    for r in read_jsonl(sr.slug_dir / "billing_extract.jsonl"):
        chunks = [c for c in r.get("chunks") or [] if isinstance(c, dict)]
        keep = [c for c in chunks if c.get("doc_type") not in PAID_SKIP | {None} and not quarantined(c, patient)]
        itemized = {_key(t) for c in keep for t in c.get("printed_payments") or [] if isinstance(t, dict)}
        for c in keep:
            _chunk(rd, str(r.get("file")), c, itemized, paid, combined)
    return paid, combined
