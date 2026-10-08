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
    assert rest.lstrip().startswith("{{ATTORNEY: the final audit flags a statement in this section")
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
