"""The drafting job's deterministic parts: the firm inputs' closed schema, the
envelope, the Howell table, the caption diff, and the section 2030.050 rule.

Every value is invented. Each rule has a case that must FAIL alongside the
case that passes, so a check that cannot fail is caught here.
"""

from __future__ import annotations


import pytest
import yaml

from drafting_testkit import COMPLAINT, envelope, make_inputs
from medchron.drafting import caption, firm as firm_mod, howell, job as job_mod, render

# ---- firm inputs ---------------------------------------------------------------------


def test_the_inputs_load_and_every_file_is_pinned(tmp_path):
    root = make_inputs(tmp_path / "in")
    firm = firm_mod.load(root)
    assert firm.model("compose") == "claude-sonnet-5"
    assert firm.rehearsal_matters == ("OPS-LIBRARY",)
    (root / "house-style.md").write_text("edited by hand", encoding="utf-8")
    with pytest.raises(firm_mod.DraftingConfigError, match="does not match the pin"):
        firm_mod.load(root)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["budget"].update(per_job_cap=5), "unknown key"),
        (lambda d: d.update(levers={}), "unknown section"),
        (lambda d: d["classes"].pop("depo_outline"), "classes.depo_outline: required"),
        (lambda d: d["format"]["mediation_brief_sections"].pop(), "exactly 11"),
        (lambda d: d["format"].update(discovery_label_style="italic"), "discovery_label_style"),
        (lambda d: d["classes"]["memo"]["prompts"].pop("audit"), "prompts: expected exactly"),
        (lambda d: d["inputs"].pop("pos.md"), "attachments.pos: pos.md is not pinned"),
        (lambda d: d["selection"].pop("exclude_name_patterns"), "exclude_name_patterns: required"),
    ],
)
def test_a_schema_miss_refuses_rather_than_defaulting(tmp_path, mutate, match):
    root = make_inputs(tmp_path / "in")
    data = yaml.safe_load((root / "drafting-firm.yaml").read_text())
    mutate(data)
    (root / "drafting-firm.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(firm_mod.DraftingConfigError, match=match):
        firm_mod.load(root)
    assert firm_mod.main([str(root)]) == 1


def test_delivery_is_optional_and_absent_means_no_rehearsal_matter(tmp_path):
    root = make_inputs(tmp_path / "in")
    data = yaml.safe_load((root / "drafting-firm.yaml").read_text())
    data.pop("delivery")
    (root / "drafting-firm.yaml").write_text(yaml.safe_dump(data))
    assert firm_mod.load(root).rehearsal_matters == ()


# ---- the envelope ------------------------------------------------------------------------


def _stamped(**over):
    return {**envelope(), "slug": "example", "month_cents_used": 0, **over}


def test_the_envelope_parses_and_files_to_its_own_matter_by_default(tmp_path):
    j = job_mod.parse(_stamped(), tmp_path)
    assert (j.document_class, j.file_to_id, j.file_to_number) == ("mediation_brief", j.matter_id, "100001")


@pytest.mark.parametrize(
    "over, match",
    [
        ({"kind": "demand"}, "not a drafting job"),
        ({"document_class": "letter"}, "document_class"),
        ({"file_to_matter_id": "0f0f0f0f-0000-4000-8000-000000000002"}, "come together"),
        ({"requester": "nobody"}, "email"),
        ({"month_cents_used": None}, "unmetered is refused"),
        ({"job_id": "not-a-ulid"}, "ULID"),
    ],
)
def test_a_bad_envelope_is_refused(tmp_path, over, match):
    with pytest.raises(job_mod.DraftingJobError, match=match):
        job_mod.parse(_stamped(**over), tmp_path)


# ---- the Howell table --------------------------------------------------------------------

BILL = {"id": "b1", "name": "Northfield PT bill.pdf"}
EOB = {"id": "e1", "name": "Northfield PT EOB.pdf"}
LIEN = {"id": "l1", "name": "Northfield PT lien letter.pdf"}


def test_a_figure_the_document_does_not_print_is_dropped():
    rows, notes = howell.verify(
        [{"provider": "Northfield PT", "kind": "bill", "date_of_service": "02/01/2026", "billed": "$900.00", "paid": "$450.00"}],
        "Northfield PT statement. 02/01/2026 therapy $900.00",
        BILL,
    )
    assert rows[0]["billed"] == "$900.00" and rows[0]["paid"] is None
    assert notes and "450.00" in notes[0]


def _row(doc, kind, dos, **f):
    return {"provider": "Northfield PT", "kind": kind, "doc": doc, "date_of_service": dos, "billed": None, "paid": None, "outstanding": None, **f}


def test_paid_comes_only_from_payment_documents_and_each_cell_cites_its_source():
    rows = [
        _row(BILL, "bill", "02/01/2026", billed="$900.00"),
        _row(BILL, "bill", "02/08/2026", billed="$300.00"),
        _row(EOB, "eob", "02/01/2026", billed="$900.00", paid="$410.25"),
        _row(LIEN, "lien", None, outstanding="$789.75"),
    ]
    [t] = howell.build(rows, [])
    assert t["billed"] == {"value": "$1,200.00", "sources": [BILL]}  # bills win; the EOB's billed is not added
    assert t["paid"] == {"value": "$410.25", "sources": [EOB]}
    assert t["outstanding"] == {"value": "$789.75", "sources": [LIEN]}
    assert t["dates_of_service"]["value"] == "02/01/2026 to 02/08/2026"


def test_a_missing_paid_figure_is_the_marker_never_billed():
    [t] = howell.build([_row(BILL, "bill", "02/01/2026", billed="$900.00")], [])
    assert t["paid"] == {"value": howell.NOT_IN_RECORD, "sources": []}
    assert t["outstanding"]["value"] == howell.NOT_IN_RECORD  # never billed minus paid
    assert howell.totals([t]) == {"billed": "$900.00", "paid": howell.NOT_IN_RECORD, "outstanding": howell.NOT_IN_RECORD}
    md = howell.markdown([t])
    assert "$900.00 (source: Northfield PT bill.pdf)" in md and "| Total | | $900.00 | {{NOT IN RECORD}} |" in md


def test_the_medicals_tab_supplies_billed_only_when_no_document_does():
    tab = [{"provider": "Southfield Imaging, Inc.", "charges": [{"amount": "1500.00", "start": "2026-03-02"}]}]
    rows = [_row(BILL, "bill", "02/01/2026", billed="$900.00")]
    table = howell.build(rows, tab)
    imaging = next(t for t in table if t["provider"].startswith("Southfield"))
    assert imaging["billed"] == {"value": "$1,500.00", "sources": [howell.MEDICALS_TAB]}
    assert imaging["paid"]["value"] == howell.NOT_IN_RECORD
    assert [t["provider"] for t in table][0] == "Northfield PT"  # ordered by first date of service


def test_conflicting_stated_balances_go_to_the_attorney():
    rows = [_row(LIEN, "lien", None, outstanding="$789.75"), _row(EOB, "ledger", None, outstanding="$700.00")]
    [t] = howell.build(rows, [])
    assert t["outstanding"]["value"].startswith("{{ATTORNEY: the record states different balances")


# ---- the caption ----------------------------------------------------------------------------


def _doc(text: str = COMPLAINT, name: str = "Complaint 2-1-26.pdf") -> dict:
    return {"name": name, "head": text}


def test_caption_fields_are_read_verbatim_from_the_court_paper():
    got = caption.extract(_doc(), ("firm.example",))
    assert got["case_number"]["value"] == "CV-0001"
    assert got["court"]["value"] == "SUPERIOR COURT OF THE STATE OF CALIFORNIA, COUNTY OF EXAMPLETOWN"
    assert got["plaintiff"]["value"] == "GAMMA EXAMPLE" and got["defendant"]["value"] == "DELTA EXAMPLE"
    assert got["attorney_email"]["value"] == "alpha@firm.example"


def test_discrepancies_quote_the_document_and_low_confidence_is_not_reported():
    fields = caption.extract(_doc(), ("firm.example",))
    record = {
        "case_number": "CV-0010",
        "court": "SUPERIOR COURT OF THE STATE OF CALIFORNIA, COUNTY OF EXAMPLETOWN",
        "plaintiffs": ["Gamma Exampel"],
        "defendants": ["Epsilon Unrelated Holdings"],  # a different name, not a typo: not reported
        "attorney_email": None,
    }
    out = {d["field"]: d for d in caption.compare(fields, record, "Complaint 2-1-26.pdf")}
    assert set(out) == {"case_number", "plaintiff", "attorney_email"}
    assert out["case_number"]["quote"] == "Case No. CV-0001" and out["case_number"]["source"] == "Complaint 2-1-26.pdf"
    assert out["plaintiff"]["record_value"] == "Gamma Exampel"
    assert "no email" in out["attorney_email"]["why"]


def test_a_matching_record_reports_nothing_and_an_ambiguous_field_is_not_read():
    fields = caption.extract(_doc(COMPLAINT + "\nCase No. CV-0002"), ("firm.example",))
    assert "case_number" not in fields  # two case numbers in one paper: not read, so never compared
    record = {
        "case_number": "CV-0002",
        "court": "Superior Court of the State of California, County of Exampletown",
        "plaintiffs": ["Gamma Example"],
        "defendants": ["Delta Example"],
        "attorney_email": "alpha@firm.example",
    }
    assert caption.compare(fields, record, "x") == []


# ---- section 2030.050: cumulative ----------------------------------------------------------


def _set(n: int, responding: str = "DELTA EXAMPLE") -> str:
    items = "\n\n".join(f"SPECIAL INTERROGATORY NO. {i}\n\nState a fact." for i in range(1, n + 1))
    return (
        "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | GAMMA EXAMPLE |\n"
        f"| RESPONDING PARTY: | {responding} |\n\n# DEFINITIONS\n\n{items}\n\n**PROOF OF SERVICE**\n\nServed.\n"
    )


def _digest(*lines: str) -> str:
    return "## MEDICAL | x\nnothing\n\n## DISCOVERY-SETS\n" + "\n".join(lines) + "\n\n## FILES-SEEN\n- x\n"


PRIOR20 = "Special Interrogatories, Set One | One | Gamma Example -> Delta Example | special interrogatories | 20 | 03/01/2026 | SROG set one"
PRIOR10 = PRIOR20.replace("| 20 |", "| 10 |")
UNREADABLE = PRIOR20.replace("| 20 |", "| not readable |")


@pytest.mark.parametrize(
    "n, lines, attach",
    [
        (20, [PRIOR20], True),  # (a) 20 + 20 prior
        (36, [], True),  # (b) 36, no prior
        (20, [PRIOR10], False),  # (c) 20 + 10
        (20, [UNREADABLE], True),  # (d) prior count unreadable
        (20, [PRIOR20.replace("Delta Example", "Someone Else")], False),  # prior to another party
        (35, [], False),
    ],
)
def test_the_declaration_rule_is_cumulative_per_responding_party(n, lines, attach):
    d = render.decl_decision(_set(n), _digest(*lines))
    assert d.attach is attach


class _Firm:
    def attachment(self, key: str) -> str:
        from drafting_testkit import DECL

        return DECL


def test_the_declaration_is_attached_before_the_proof_of_service_with_its_counts_filled():
    doc, notes = render.attach_decl(_set(20), "discovery_set", _Firm(), _digest(PRIOR20))
    assert doc.index("DECLARATION FOR ADDITIONAL DISCOVERY") < doc.index("PROOF OF SERVICE")
    assert "authoring comment" not in doc
    assert "a total of 20 interrogatories" in doc and "contains 20 specially" in doc
    assert any("40 special interrogatories to this party" in n for n in notes)


def test_an_unreadable_prior_count_leaves_paragraph_4_to_the_attorney():
    doc, notes = render.attach_decl(_set(20), "discovery_set", _Firm(), _digest(UNREADABLE))
    assert "{{ATTORNEY: a prior set's count is not readable" in doc and "{{FILL: number of interrogatories" not in doc
    assert any("paragraph 4 is left to the attorney" in n for n in notes)


def test_a_model_written_declaration_is_removed_and_none_attached_under_the_limit():
    model = _set(5).replace("**PROOF OF SERVICE**", "**DECLARATION FOR ADDITIONAL DISCOVERY**\n\nI declare.\n\n**PROOF OF SERVICE**")
    doc, notes = render.attach_decl(model, "discovery_set", _Firm(), _digest())
    assert "DECLARATION" not in doc and "PROOF OF SERVICE" in doc
    assert notes[0].startswith("removed a model-written")


def test_the_drafters_refusal_and_the_notes_split():
    assert render.needs_attorney("=== NEEDS THE ATTORNEY ===\nThe request does not name the deponent.") == (
        "The request does not name the deponent."
    )
    assert render.needs_attorney("# A draft") is None
    doc, notes = render.split_notes("# Draft\n\nBody.\n\n=== ATTORNEY NOTES ===\n\n## NOT IN RECORD\n")
    assert "ATTORNEY NOTES" not in doc and notes.startswith("## NOT IN RECORD")


def test_reserved_judgment_removes_settlement_figures_and_restores_the_marker():
    md = "# X. SUMMARY OF CASE VALUE\n\nOur settlement target figure is $250,000 and the bracket $200,000.\n\n# XI. CONCLUSION\n\nEnd."
    out, notes = render.reserve_judgment(md, "mediation_brief")
    assert "$250,000" not in out and "$200,000" not in out
    assert out.count("{{ATTORNEY") >= 2
    assert render.reserve_judgment(md, "memo") == (md, [])


def test_a_digest_without_discovery_sets_reads_no_prior():
    assert render.prior_sets("## MEDICAL\nx\n") == []
    assert len(render.prior_sets(_digest(PRIOR20, PRIOR20))) == 1  # deduplicated across chunks


def test_a_court_name_typo_is_reported_but_its_wording_variants_are_not():
    fields = caption.extract(_doc(), ("firm.example",))
    base = {"case_number": None, "plaintiffs": [], "defendants": [], "attorney_email": "alpha@firm.example"}
    same = caption.compare(fields, {**base, "court": "Superior Court of California, County of Exampletown"}, "c")
    assert same == []
    typo = caption.compare(fields, {**base, "court": "Superior Court of California, County of Exampletowm"}, "c")
    assert [d["field"] for d in typo] == ["court"]
