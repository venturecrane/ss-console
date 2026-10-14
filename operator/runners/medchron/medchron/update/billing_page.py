"""A billing page inside a new record. $0.

A provider's "Medical and Billing Records" file carries visit notes and,
behind them, its claim forms and its ledger. The base lane never itemizes a
bill as treatment, so an update must not compose one either: a CMS-1500 read
as a visit puts its line items on the chronology as care. Such a page is
excluded from composition with reason BILLING. Its text stays in the
document's transcript (``text/<id>.txt``), which is what the handwritten-date
check reads for the provider's typed dates of service
(``update/handwritten.service_dates``, ``audit/handwritten_evidence``), so it
is still evidence; it is just not a visit.

A page is billing when it is a claim form (CMS-1500 / HCFA, UB-04 / CMS-1450)
or an itemized statement or ledger (charges, payments and balances with
amounts), and states no clinical narrative (no assessment, plan, exam,
subjective or history section). When in doubt it is read.
"""

from __future__ import annotations

import re

BILLING = "billing page"

_CLAIM_FORM = re.compile(r"(?i)\b(?:cms[- ]?1500|hcfa[- ]?1500|ub[- ]?04|cms[- ]?1450|health insurance claim form)\b")
_LEDGER_WORDS = re.compile(
    r"(?i)\b(?:charges?|payments?|balance|adjustments?|amount due|ledger|itemized|statement|"
    r"insurance paid|patient responsibility|write[- ]?off|total due)\b"
)
_AMOUNT = re.compile(r"\$?\b\d{1,3}(?:,\d{3})*\.\d{2}\b")
_NARRATIVE = re.compile(
    r"(?im)^\s*(?:assessment|plan|subjective|objective|exam(?:ination)?|physical exam(?:ination)?|"
    r"chief complaint|cc|hpi|history of present illness|impression|review of systems|ros|"
    r"progress note|treatment notes?|s|o|a|p)\s*[:\-]|"
    r"\b(?:assessment and plan|chief complaint|history of present illness|review of systems|"
    r"physical examination|plan of care)\b"
)


def is_billing(text: str) -> bool:
    """True for a claim form or an itemized statement/ledger page with no
    clinical narrative on it."""
    t = text or ""
    if _NARRATIVE.search(t):
        return False
    if _CLAIM_FORM.search(t):
        return True
    words = {w.lower() for w in _LEDGER_WORDS.findall(t)}
    return len(words) >= 2 and len(_AMOUNT.findall(t)) >= 3
