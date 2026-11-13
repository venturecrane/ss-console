"""The one full-filename rule (``jobs/filing.full_name``) and the copies that
now are it: the read-back's, the update locator's, litigation's and the
negotiation watch's folded form."""

from __future__ import annotations

import pytest

from jobs import filing
from jobs.litigation import manifest
from jobs.medchron.update import locate
from jobs.negotiation import select
from jobs.proof import readback

#: (vendor row, full name): the shapes measured on a live matter (ss#2914).
SHAPES = [
    ({"name": "Chronology 09-24-26", "ext": ".docx"}, "Chronology 09-24-26.docx"),
    ({"name": "Signed Agreement.pdf", "ext": ".pdf"}, "Signed Agreement.pdf"),
    ({"name": "Scan.PDF", "ext": ".pdf"}, "Scan.PDF"),
    ({"name": "Report", "ext": ".PDF"}, "Report.PDF"),
    ({"name": "no-extension", "ext": ""}, "no-extension"),
    ({"name": "no-extension", "ext": None}, "no-extension"),
    ({"name": None, "ext": ".docx"}, ".docx"),
]


@pytest.mark.parametrize("row,want", SHAPES)
def test_every_copy_of_the_rule_is_the_rule(row, want):
    """MUTATION: append the extension unconditionally and an e-signature
    vendor's own filename reads as missing (``.pdf.pdf``)."""
    assert filing.full_name(row) == want
    assert readback.vendor_name(row) == locate.vendor_name(row) == manifest.full_name(row) == want


@pytest.mark.parametrize(
    "row,want",
    [
        ({"name": " Offer letter ", "ext": "PDF"}, "Offer letter.pdf"),
        ({"name": "Offer letter.pdf", "ext": ".PDF"}, "Offer letter.pdf"),
        ({"name": "Offer letter.MSG", "ext": ""}, "Offer letter.MSG"),
        ({"name": "Offer letter", "ext": ""}, "Offer letter"),
    ],
)
def test_the_folded_form_is_what_the_negotiation_watch_matches_on(row, want):
    assert filing.full_name(row, fold=True) == select.full_name(row) == want
    assert select.extension(row) == filing.folded_ext(row)
