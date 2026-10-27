"""The File Status print-out carries the insurance the reader recorded."""

from __future__ import annotations

from medchron.casefile.book import render_html

RANK = {"priority": 4, "reason": "Nothing due."}


def _rec(insurance: object) -> dict:
    return {"number": "900001", "clients": ["Test Client"], "headline": "Synthetic.", "insurance": insurance}


def test_insurance_policies_print_with_their_source() -> None:
    page = render_html(
        _rec(
            {
                "first_party": [
                    {
                        "carrier": "Carrier A",
                        "claim_no": "C-1",
                        "adjuster": "Adj A",
                        "limits": "30/60",
                        "layout_only": False,
                    }
                ],
                "third_party": [
                    {"carrier": "Carrier B", "claim_no": None, "adjuster": None, "limits": None, "layout_only": True}
                ],
            }
        ),
        "2026-10-27",
        {},
        RANK,
        {},
    )
    assert "<h2>Insurance</h2>" in page
    assert "Client&#x27;s own policy" in page and "Carrier A" in page and "30/60" in page
    assert "Other side" in page and "Carrier B" in page
    assert "Layout only, no document in the file" in page and "From the file" in page


def test_no_insurance_section_when_none_recorded() -> None:
    assert "<h2>Insurance</h2>" not in render_html(_rec({}), "2026-10-27", {}, RANK, {})
    assert "<h2>Insurance</h2>" not in render_html(
        _rec({"first_party": [], "third_party": []}), "2026-10-27", {}, RANK, {}
    )
