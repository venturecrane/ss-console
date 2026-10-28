"""Amounts paid, as printed on the bills. $0, from the existing transcription.

A firm asked for a billing summary showing "both total charges and amounts
paid", and the worksheet showed charges only. The billing read already
transcribes every printed total with its label and page; many of them are
payments ("insurance payments", "you paid", "payment received"). This module
sorts a printed total by its LABEL alone and gathers, per provider, what the
bills print as paid. Nothing is read again and the model computes nothing.

Classes, conservative by design:

* PAID: an unambiguous payment word ("paid", "payment(s)", "pmt"), by anyone.
* PAYMENTS_AND_ADJUSTMENTS: a label naming both, shown with its printed label
  so the attorney sees exactly what it combines; never counted as paid.
* ADJUSTMENT: write-offs, contractual and refund adjustments; not payments.
* BALANCE_DUE: what is owed ("balance", "amount due", "payment due", "total
  payable"); checked FIRST, so a due amount is never read as paid.
* OTHER: everything else (charges, and anything this list cannot place).

A provider's paid figures are summed only when the sum cannot count one
payment twice: every amount parsed cleanly (a "?" digit or an unparseable
amount means no sum), and the figures sit on one document with no label that
is itself a total ("patient total - you paid" and "grand total - you paid"
on one bill can be the same money). Payments printed on several documents
are not summed, because a later statement repeats an earlier payment. When a
sum is not computed the printed figures are shown and the reason is said.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PAID = "PAID"
PAYMENTS_AND_ADJUSTMENTS = "PAYMENTS_AND_ADJUSTMENTS"
ADJUSTMENT = "ADJUSTMENT"
BALANCE_DUE = "BALANCE_DUE"
OTHER = "OTHER"

_DUE = re.compile(r"balance|\bdue\b|payable|\bowed?\b|\bowing\b|responsib|unpaid|not\s+paid", re.I)
_PAY = re.compile(r"\bpaid\b|\bpayments?\b|\bpmts?\b|\bpymts?\b", re.I)
_ADJ = re.compile(r"adjust|write.?off|contractual|disallow|discount|refund", re.I)
_TOTAL = re.compile(r"\btotal\b", re.I)

#: What the worksheet says for a billed provider whose bills print no payment.
NO_PAYMENT = "No payment printed on these bills"
NO_BILL_ON_FILE = "No bill on file"
NOT_SUMMED_UNREAD = "Total not computed: a printed amount could not be read or confirmed cleanly"
NOT_SUMMED_OVERLAP = (
    "Total not computed: these figures may overlap (a later statement can repeat an earlier payment, "
    "and a total can repeat the lines above it)"
)


def classify(label: Any) -> str:
    """One printed total's class, from its label alone."""
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


def _figure(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p.get(k) for k in ("label", "amount_text", "amount", "file", "page")}


def _dedup(figs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same printed line transcribed twice (one page read in two chunks)
    is one figure."""
    seen: set = set()
    out = []
    for f in figs:
        k = (str(f.get("label")), str(f.get("amount_text")), str(f.get("file")), str(f.get("page")))
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def summarize(printed: list[dict[str, Any]]) -> dict[str, Any]:
    """Per provider: its printed totals sorted by class, and the paid total
    when it can be summed without risk of counting a payment twice.

    ``printed`` rows are {label, amount_text, amount, file, page}, ``amount``
    the chart's own parse (None when the text did not parse or carried a
    "?")."""
    by: dict[str, list[dict[str, Any]]] = {PAID: [], PAYMENTS_AND_ADJUSTMENTS: [], ADJUSTMENT: [], BALANCE_DUE: []}
    for p in printed:
        kind = classify(p.get("label"))
        if kind in by:
            by[kind].append(_figure(p))
    paid = _dedup(by[PAID])
    total, note = None, ""
    if paid:
        if any(f["amount"] is None for f in paid):
            note = NOT_SUMMED_UNREAD
        elif len(paid) > 1 and (
            len({f["file"] for f in paid}) > 1 or any(_TOTAL.search(str(f["label"])) for f in paid)
        ):
            note = NOT_SUMMED_OVERLAP
        else:
            total = round(sum(abs(float(f["amount"])) for f in paid), 2)
    return {
        "figures": paid,
        "total": total,
        "note": note,
        "combined": _dedup(by[PAYMENTS_AND_ADJUSTMENTS]),
        "adjustments": _dedup(by[ADJUSTMENT]),
        "balances": _dedup(by[BALANCE_DUE]),
    }


def cite(f: dict[str, Any]) -> str:
    """A printed figure with its label and source, exactly as printed."""
    amt = str(f.get("amount_text") or "").strip()
    return f'{amt} "{str(f.get("label") or "").strip()}" ({str(f.get("file") or "")[:56]}, p. {f.get("page")})'


def unpaid_billed(rows: list[dict[str, Any]]) -> list[str]:
    """Providers with a bill on file whose bills print no payment."""
    return [str(r["provider"]) for r in rows if r.get("docs") and not ((r.get("paid") or {}).get("figures"))]


def limitation_sentence(n: int) -> str:
    return (
        f"Amounts paid are shown only where a bill prints them; for {n} provider(s) the bills print charges "
        f"but no payment."
    )


def limitation_lines(d: Path, unit: str | None) -> list[str]:
    """The Limitations sentence, from the billing chart as it stands (the unit's
    own chart on a joint matter, else the plain one); none when no chart or
    every billed provider's bills print a payment."""
    from .stages.base import read_json

    own = d / f"billing_chart-{unit}.json" if unit is not None else None
    path = own if own is not None and own.is_file() else d / "billing_chart.json"
    chart = read_json(path, None) if path.is_file() else None
    rows = (chart or {}).get("rows") if isinstance(chart, dict) else None
    if not isinstance(rows, list):
        return []
    n = len(unpaid_billed([r for r in rows if isinstance(r, dict) and "paid" in r]))
    return ["", limitation_sentence(n)] if n else []
