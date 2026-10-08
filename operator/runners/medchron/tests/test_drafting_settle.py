"""The drafting final pass (drafting/settle.py) and the billing read's
selection and provider spine (drafting/howell.py). Fictional fixtures."""

from __future__ import annotations

import pytest

from medchron.demand import finalpass
from medchron.drafting import howell, settle

DRAFT = """# VIII. STATEMENT OF INSURANCE COVERAGE

The responses to Form Interrogatory No. 4.1 state coverage of "A 1,000,000/1,000,000/1,000,000" for both. The umbrella policy is $3 Million. Defendant has stated no reservation of rights.

# IX. STATEMENT OF PRIOR NEGOTIATIONS

Plaintiff demanded the policy limits. Defense counsel wrote that the carrier would continue evaluating claims.

# XI. CONCLUSION

Plaintiff attends this mediation in good faith and hopes to resolve the case.
"""


def _audit(rows: dict[str, list[str]]) -> str:
    out = ["# Audit v3", ""]
    for head, lines in rows.items():
        out += [f"## AUDIT: {head}", "", *lines, "SUPPORTED=1 DRIFTS=0 INVENTED=1 ARITHMETIC=0", ""]
    return "\n".join(out)


VIII = "VIII. STATEMENT OF INSURANCE COVERAGE"
IX = "IX. STATEMENT OF PRIOR NEGOTIATIONS"
XI = "XI. CONCLUSION"


def test_a_paraphrased_claim_is_placed_by_its_quoted_span():
    au = _audit(
        {VIII: ['- Primary limits "A 1,000,000/1,000,000/1,000,000" disclosed by each defendant | INVENTED | no cite']}
    )
    with pytest.raises(finalpass.Unlocated):  # the demand pass cannot place it (the 2026-10-07 failure)
        finalpass.settle(DRAFT, au)
    md, notes = settle.settle(DRAFT, au)
    assert "A 1,000,000/1,000,000/1,000,000" not in md
    assert "{{NOT IN RECORD: a statement here was removed by the final audit (no cite)" in md
    assert any("removed" in n for n in notes)


def test_a_claim_the_same_audit_supports_elsewhere_is_kept_and_marked():
    au = _audit(
        {
            VIII: ["- Umbrella $3 Million disclosed by each defendant | INVENTED | no cite in digest"],
            IX: ["- Umbrella limits of $3 Million disclosed in verified discovery | SUPPORTED | Form Interrog. 4.1"],
        }
    )
    md, notes = settle.settle(DRAFT, au)
    assert "The umbrella policy is $3 Million." in md  # kept
    assert "the final audit disagrees with itself" in md
    assert any("contradicts itself" in n for n in notes)


def test_an_unplaceable_claim_is_marked_at_the_section_top_not_fatal():
    au = _audit({XI: ["- looks forward to the mediator's assistance | INVENTED | no cite"]})
    md, notes = settle.settle(DRAFT, au)
    head, rest = md.split("# XI. CONCLUSION", 1)
    assert rest.lstrip().startswith("{{ATTORNEY: the final audit flags a statement or figure in this section")
    assert any("not located" in n for n in notes)


def test_too_many_unplaceable_claims_still_fail_the_job():
    lines = [f"- nothing like this {i} | INVENTED | no cite" for i in range(settle.MAX_UNPLACED_PER_SECTION + 1)]
    with pytest.raises(finalpass.Unlocated):
        settle.settle(DRAFT, _audit({XI: lines}))


def test_a_second_finding_on_a_removed_sentence_is_already_settled():
    au = _audit(
        {
            VIII: [
                '- limits "A 1,000,000/1,000,000/1,000,000" | INVENTED | no cite',
                '- primary limits "A 1,000,000/1,000,000/1,000,000" again | INVENTED | no cite',
            ]
        }
    )
    md, notes = settle.settle(DRAFT, au)
    assert md.count("{{NOT IN RECORD") == 1
    assert not any("not located" in n for n in notes)


def test_new_markers_pass_the_reserved_check_shape():
    # Every marker the pass writes is a {{...}} marker with no unbalanced braces.
    au = _audit({XI: ["- looks forward to the mediator's assistance | INVENTED | no cite"]})
    md, _ = settle.settle(DRAFT, au)
    assert md.count("{{") == md.count("}}")


# ---- billing selection --------------------------------------------------------


def _doc(name, folder="/MEDICAL", chars=3000):
    return {"name": name, "folder": folder, "chars": chars}


LEDGER_TEXT = "Date of Service  CPT  Charges  Payments  Adjustments\n" + "\n".join(
    f"02/0{i}/2026 9721{i} ${100 + i}.00 $0.00 $0.00" for i in range(1, 7)
)


@pytest.mark.parametrize(
    "row, text, want",
    [
        (_doc("Northfield PT bill.pdf"), "", True),  # named
        (_doc("pellman 10.5.23--", folder="/LIENS"), "", True),  # in a LIENS folder
        (_doc("records and billing packet"), "", True),  # 'billing' in the name
        (_doc("scan 0042"), LEDGER_TEXT, True),  # reads as a ledger
        (_doc("letter re treatment", chars=2000), "Please note our lien of $365.00 on this account.", True),
        (_doc("FULL DEMAND", folder="/DEMAND", chars=307_669), "a lien ... total charges $41,400.00", False),
        (_doc("Deposition of plaintiff", chars=90_000), "Q. Is there a lien? A. Yes, $5,000.00.", False),
        (_doc("RELEASE & SET SMT --", folder="/(root)"), "Total $38,970.00 lien", False),
        (_doc("Retainer agreement"), "balance due $500.00", False),
    ],
)
def test_billing_selection(row, text, want):
    assert howell.is_billing(row, text) is want


# ---- provider spine -----------------------------------------------------------

TAB = [
    {"provider": "Dr. Okonkwo", "charges": [{"amount": "87950.0"}]},
    {"provider": "Mara Halvorsen Massage Therapist", "charges": [{"amount": "640.0"}]},
    {"provider": "Northgate Orthopedic Consultants Inc", "charges": [{"amount": "3550.0"}]},
]


def _brow(provider, billed, name="bill"):
    return {
        "provider": provider,
        "kind": "bill",
        "row_type": "stated_total",
        "payer": None,
        "doc": {"id": name, "name": name},
        "date_of_service": None,
        "billed": billed,
        "paid": None,
        "outstanding": None,
    }


def test_billing_rows_join_their_medicals_tab_provider():
    rows = [
        _brow("Dana K. Okonkwo, M.D.", "$87,950.00", "a"),
        _brow("Mara Halverson", "$640.00", "b"),  # misspelled on the bill
        _brow("Northgate Orthopaedic Consultants, Inc. ($3,550.00 reduced/Staff)", "$3,550.00", "c"),
    ]
    table = howell.build(rows, TAB, [])
    assert sorted(t["provider"] for t in table) == sorted(m["provider"] for m in TAB)


def test_vendors_stay_out_and_off_tab_treaters_are_marked():
    out: list[str] = []
    rows = [
        _brow("Summit Litigation Solutions", "$563.75", "v1"),
        _brow("Lena Ortiz & Associates, LLC", "$4,387.50", "v2"),
        _brow("Ridgeview Interventional Pain", "$1,100.00", "t1"),
    ]
    table = howell.build(rows, TAB, out)
    names = [t["provider"] for t in table]
    assert not any("Summit" in n or "Ortiz" in n for n in names)
    assert len(out) == 2
    ridge = [n for n in names if n.startswith("Ridgeview")]
    assert ridge and howell.OFF_TAB in ridge[0]


def test_without_a_medicals_tab_every_provider_is_its_own_row():
    table = howell.build([_brow("Summit Litigation Solutions", "$563.75")], [], [])
    assert [t["provider"] for t in table] == ["Summit Litigation Solutions"]


# ---- review falsifiers (PR #3098) ------------------------------------------------

TWO = """# VI. DAMAGES

Northgate billed $1,200.00 for the first visit. The plan paid $1,200.00 toward a later balance. Treatment ended in 2025.
"""


def test_two_different_claims_sharing_a_figure_are_both_settled():
    au = _audit(
        {
            "VI. DAMAGES": [
                "- Northgate billed $1,200.00 for the first visit | INVENTED | no bill",
                "- The plan paid $1,200.00 toward a later balance | INVENTED | no EOB",
            ]
        }
    )
    md, _ = settle.settle(TWO, au)
    assert md.count("{{NOT IN RECORD") == 2
    assert "first visit" not in md and "later balance" not in md


def test_a_figure_matches_only_as_a_whole_number():
    assert settle._has("billed $13,550.00", "3,550") is False
    assert settle._has("billed $3,550.00 in all", "$3,550.00") is True


def test_two_sections_with_one_heading_both_survive():
    md = "# A. Treatment\n\nFirst provider text.\n\n# A. Treatment\n\nSecond provider text.\n"
    out, _ = settle.settle(md, "# Audit v1\n\n## AUDIT: A. Treatment\n\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0\n")
    assert "First provider text." in out and "Second provider text." in out


def test_a_finding_for_a_missing_section_still_reaches_the_draft():
    au = _audit({"XII. NO SUCH SECTION": ["- something | INVENTED | no cite"]})
    md, _ = settle.settle(DRAFT, au)
    assert "in a section the draft lacks (XII. NO SUCH SECTION)" in md


def test_an_arithmetic_flag_never_lands_inside_a_date():
    au = _audit({"VI. DAMAGES": ["- total of 2 visits | ARITHMETIC | should be 3"]})
    md, _ = settle.settle(TWO, au)
    assert "2025" in md


def test_a_truncated_answer_is_not_read_as_its_inner_array():
    assert howell._parse_json_array('[{"provider": "x", "lines": [{"a": 1}]') is None


def test_a_lien_agreement_is_a_billing_document():
    assert howell.is_billing({"name": "Medical Lien Agreement", "folder": "/MISC", "chars": 9000}, "") is True


def test_a_treater_named_associates_llc_is_not_a_vendor():
    rows = [_brow("Valley Pain Associates, LLC", "$900.00", "p1")]
    table = howell.build(rows, TAB, [])
    assert any(t["provider"].startswith("Valley Pain") for t in table)


def test_a_provider_listed_twice_on_the_tab_still_matches():
    tab = [*TAB, {"provider": "Dr. Okonkwo", "charges": [{"amount": "1.0"}]}]
    rows = [_brow("Dana K. Okonkwo, M.D.", "$87,950.00", "a")]
    out: list[str] = []
    howell.build(rows, tab, out)
    assert out == []
