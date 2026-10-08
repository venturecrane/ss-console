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
        (lambda d: d["models"].update(compose="claude-unknown-9"), "no known output maximum"),
        (lambda d: d["format"]["layout"]["memo"].update(spacing=2), "layout.memo.spacing: unknown key"),
        (lambda d: d["format"]["layout"]["mediation_brief"].update(heading_underline=[True]), "expected bool3"),
        (lambda d: d["format"]["layout"].update(letter={}), "layout.letter: unknown class"),
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
BILL2 = {"id": "b2", "name": "Northfield PT second bill.pdf"}
EOB = {"id": "e1", "name": "Northfield PT EOB.pdf"}
LEDGER = {"id": "g1", "name": "Northfield PT payment ledger.pdf"}
LIEN = {"id": "l1", "name": "Northfield PT lien letter.pdf"}


def test_a_figure_the_document_does_not_print_is_dropped():
    rows, notes = howell.verify(
        [
            {
                "provider": "Northfield PT",
                "kind": "bill",
                "row_type": "line_item",
                "date_of_service": "02/01/2026",
                "billed": "$900.00",
                "paid": "$450.00",
            }
        ],
        "Northfield PT statement. 02/01/2026 therapy $900.00",
        BILL,
    )
    assert rows[0]["billed"] == "$900.00" and rows[0]["paid"] is None and rows[0]["row_type"] == "line_item"
    assert notes and "450.00" in notes[0]


def _row(doc, kind, dos, row_type="line_item", payer=None, **f):
    return {
        "provider": "Northfield PT",
        "kind": kind,
        "row_type": row_type,
        "payer": payer,
        "doc": doc,
        "date_of_service": dos,
        "billed": None,
        "paid": None,
        "outstanding": None,
        **f,
    }


def test_line_items_and_the_bills_own_total_count_once():
    rows = [
        _row(BILL, "bill", "02/01/2026", billed="$900.00"),
        _row(BILL, "bill", "02/08/2026", billed="$300.00"),
        _row(BILL, "bill", None, row_type="stated_total", billed="$1,200.00"),
    ]
    [t] = howell.build(rows, [])
    assert t["billed"] == {"value": "$1,200.00", "sources": [BILL]}


def test_two_real_same_day_charges_on_one_bill_both_count():
    rows = [_row(BILL, "bill", "02/01/2026", billed="$150.00"), _row(BILL, "bill", "02/01/2026", billed="$150.00")]
    [t] = howell.build(rows, [])
    assert t["billed"]["value"] == "$300.00"


def test_the_same_payment_on_an_eob_and_a_ledger_counts_once():
    rows = [
        _row(BILL, "bill", "02/01/2026", billed="$900.00"),
        _row(EOB, "eob", "02/01/2026", payer="Example Health", paid="$410.25"),
        _row(LEDGER, "ledger", "02/01/2026", payer="Example Health", paid="$410.25"),
        _row(LIEN, "lien", None, outstanding="$489.75"),
    ]
    [t] = howell.build(rows, [])
    assert t["paid"]["value"] == "$410.25" and {s["id"] for s in t["paid"]["sources"]} == {"e1", "g1"}
    assert t["outstanding"] == {"value": "$489.75", "sources": [LIEN]}


def test_documents_that_disagree_on_paid_go_to_the_attorney_never_added():
    rows = [
        _row(EOB, "eob", "02/01/2026", paid="$410.25"),
        _row(LEDGER, "ledger", None, paid="$400.00"),  # undated beside a dated figure
    ]
    [t] = howell.build(rows, [])
    v = t["paid"]["value"]
    assert v.startswith("{{ATTORNEY: confirm paid amount, sources disagree:") and "$810.25" not in v
    assert "$410.25 (Northfield PT EOB.pdf)" in v and "$400.00 (Northfield PT payment ledger.pdf)" in v


def test_bills_for_different_dates_add_across_documents():
    rows = [_row(BILL, "bill", "02/01/2026", billed="$900.00"), _row(BILL2, "bill", "03/01/2026", billed="$300.00")]
    [t] = howell.build(rows, [])
    assert t["billed"]["value"] == "$1,200.00"


def test_a_missing_paid_figure_is_the_marker_never_billed():
    [t] = howell.build([_row(BILL, "bill", "02/01/2026", billed="$900.00")], [])
    assert t["paid"] == {"value": howell.NOT_IN_RECORD, "sources": []}
    assert t["outstanding"]["value"] == howell.NOT_IN_RECORD  # never billed minus paid
    assert howell.totals([t]) == {
        "billed": "$900.00",
        "paid": howell.NOT_IN_RECORD,
        "outstanding": howell.NOT_IN_RECORD,
    }
    md = howell.markdown([t])
    assert "$900.00 (source: Northfield PT bill.pdf)" in md and "| Total | | $900.00 | {{NOT IN RECORD}} |" in md


def test_the_medicals_tab_supplies_billed_only_when_no_document_does():
    tab = [{"provider": "Southfield Imaging, Inc.", "charges": [{"amount": "1500.00", "start": "2026-03-02"}]}]
    table = howell.build([_row(BILL, "bill", "02/01/2026", billed="$900.00")], tab)
    imaging = next(t for t in table if t["provider"].startswith("Southfield"))
    assert imaging["billed"] == {"value": "$1,500.00", "sources": [howell.MEDICALS_TAB]}
    assert imaging["paid"]["value"] == howell.NOT_IN_RECORD
    first = [t["provider"] for t in table][0]
    assert first.startswith("Northfield PT") and howell.OFF_TAB in first  # not on the tab: marked


def test_conflicting_stated_balances_go_to_the_attorney():
    rows = [_row(LIEN, "lien", None, outstanding="$789.75"), _row(LEDGER, "ledger", None, outstanding="$700.00")]
    [t] = howell.build(rows, [])
    assert t["outstanding"]["value"].startswith("{{ATTORNEY: the record states different balances")


class _Doorway:
    def __init__(self, text: str, stop: str = "end_turn") -> None:
        self.text, self.stop, self.calls = text, stop, []

    def call(self, stage, **kw):
        from types import SimpleNamespace

        self.calls.append(kw)
        return SimpleNamespace(text=self.text, stop_reason=self.stop)


def _billing_corpus(tmp_path):
    import json as _json

    data = tmp_path / "data"
    (data / "text").mkdir(parents=True)
    tp = data / "text" / "b1.txt"
    tp.write_text("Northfield PT itemized statement 02/01/2026 $900.00", encoding="utf-8")
    row = {"id": "b1", "name": "Northfield PT bill", "text_path": str(tp)}
    (data / "extracted.jsonl").write_text(_json.dumps(row) + "\n", encoding="utf-8")
    return data


@pytest.mark.parametrize("text, stop", [('[{"provider": "x"', "max_tokens"), ("I could not read it.", "end_turn")])
def test_an_unfinished_or_unreadable_billing_read_fails_and_caches_nothing(tmp_path, text, stop):
    data = _billing_corpus(tmp_path)
    with pytest.raises(howell.ExtractionError):
        howell.extract(data, _Doorway(text, stop), "claude-sonnet-5", 1, lambda _m: None)
    assert not (data / "howell" / "b1.json").exists()


class _SeqDoorway:
    """Answers in order, one per call."""

    def __init__(self, *texts: str) -> None:
        self.texts, self.calls = list(texts), []

    def call(self, stage, **kw):
        from types import SimpleNamespace

        self.calls.append(kw)
        return SimpleNamespace(text=self.texts[len(self.calls) - 1], stop_reason="end_turn")


ROW = '{"provider": "Northfield PT", "kind": "bill", "row_type": "line_item", "billed": "$900.00"}'


@pytest.mark.parametrize(
    "answer",
    [
        f"Here is the array:\n[{ROW}]\nNote: [see attached] for detail.",  # brackets after the array broke the greedy match
        f"```json\n[{ROW}]\n```",
        f"The visit [02/01/2026] is billed below.\n[{ROW}]",  # a bracketed word before the array
    ],
)
def test_the_array_is_read_from_an_answer_with_words_around_it(tmp_path, answer):
    data = _billing_corpus(tmp_path)
    rows, _ = howell.extract(data, _SeqDoorway(answer), "claude-sonnet-5", 1, lambda _m: None)
    assert [r["billed"] for r in rows] == ["$900.00"]


def test_an_unreadable_answer_is_retried_once_and_kept_for_diagnosis(tmp_path):
    data = _billing_corpus(tmp_path)
    door = _SeqDoorway("This part of the demand restates the bills.", f"[{ROW}]")
    rows, _ = howell.extract(data, door, "claude-sonnet-5", 1, lambda _m: None)
    assert [r["billed"] for r in rows] == ["$900.00"] and len(door.calls) == 2
    assert door.calls[1]["messages"][-1]["content"] == howell.RETRY
    kept = data / "howell" / ".b1.0.0.unparsed.txt"
    assert kept.read_text(encoding="utf-8") == "This part of the demand restates the bills."


def test_two_unreadable_answers_fail_the_read_and_keep_both(tmp_path):
    data = _billing_corpus(tmp_path)
    with pytest.raises(howell.ExtractionError):
        howell.extract(data, _SeqDoorway("no.", "still no."), "claude-sonnet-5", 1, lambda _m: None)
    assert not (data / "howell" / "b1.json").exists()
    assert (data / "howell" / ".b1.0.1.unparsed.txt").read_text(encoding="utf-8") == "still no."


def test_a_billing_read_asks_for_the_models_output_maximum(tmp_path):
    from medchron.demand.gapaudit import OUTPUT_MAX

    data, door = _billing_corpus(tmp_path), _Doorway("[]")
    rows, _ = howell.extract(data, door, "claude-sonnet-5", 1, lambda _m: None)
    assert rows == [] and door.calls[0]["max_tokens"] == OUTPUT_MAX["claude-sonnet-5"]
    assert (data / "howell" / "b1.json").is_file()  # an honest empty answer is cached


# ---- the caption ----------------------------------------------------------------------------


def _doc(text: str = COMPLAINT, name: str = "Complaint 2-1-26.pdf") -> dict:
    return {"name": name, "head": text}


def test_caption_fields_are_read_verbatim_from_the_court_paper():
    got = caption.extract(_doc(), ("firm.example",))
    assert got["case_number"]["value"] == "CV-0001"
    assert got["court"]["value"] == "SUPERIOR COURT OF THE STATE OF CALIFORNIA, COUNTY OF EXAMPLETOWN"
    assert got["plaintiff"]["value"] == "GAMMA EXAMPLE" and got["defendant"]["value"] == "DELTA EXAMPLE"
    assert got["attorney_email"]["value"] == "alpha@firm.example"


def test_a_case_number_must_carry_a_digit():
    got = caption.extract(_doc(COMPLAINT.replace("Case No. CV-0001", "Case No. pending")), ("firm.example",))
    assert "case_number" not in got
    got = caption.extract(_doc(COMPLAINT.replace("Case No. CV-0001", "Case No. PENDING")), ("firm.example",))
    assert "case_number" not in got


def test_discrepancies_quote_the_document_and_low_confidence_is_not_reported():
    fields = caption.extract(_doc(), ("firm.example",))
    record = {
        "case_number": "CV-0010",
        "court": "SUPERIOR COURT OF THE STATE OF CALIFORNIA, COUNTY OF EXAMPLETOWN",
        "plaintiffs": ["Gamma Exampel"],
        "defendants": ["Epsilon Unrelated Holdings"],
        "attorney_email": "",  # read, and empty
    }
    diffs, compared = caption.compare(fields, record, "Complaint 2-1-26.pdf")
    out = {d["field"]: d for d in diffs}
    assert set(out) == {"case_number", "plaintiff", "attorney_email"}
    assert out["case_number"]["quote"] == "Case No. CV-0001" and out["case_number"]["source"] == "Complaint 2-1-26.pdf"
    assert "no email" in out["attorney_email"]["why"]
    assert set(compared) == {"case_number", "court", "attorney_email", "plaintiff", "defendant"}


def test_a_case_number_far_from_the_record_is_another_matter_not_a_typo():
    fields = caption.extract(_doc(), ("firm.example",))
    diffs, _ = caption.compare(fields, {"case_number": "ZZ-998877-QQ"}, "c")
    assert diffs == []


def test_a_field_the_record_does_not_carry_is_never_compared_or_reported():
    fields = caption.extract(_doc(), ("firm.example",))
    diffs, compared = caption.compare(fields, {"case_number": None, "court": None, "attorney_email": None}, "c")
    assert diffs == [] and compared == []


def test_a_matching_record_reports_nothing_and_an_ambiguous_field_is_not_read():
    fields = caption.extract(_doc(COMPLAINT + "\nCase No. CV-0002"), ("firm.example",))
    assert "case_number" not in fields
    record = {
        "case_number": "CV-0002",
        "court": "Superior Court of the State of California, County of Exampletown",
        "plaintiffs": ["Gamma Example"],
        "defendants": ["Delta Example"],
        "attorney_email": "alpha@firm.example",
    }
    diffs, compared = caption.compare(fields, record, "x")
    assert diffs == [] and "court" in compared


def test_an_unreadable_record_raises_never_reads_as_agreement():
    class NoClient:
        pass

    with pytest.raises(caption.RecordUnreadable):
        caption.read_record(NoClient(), "m", {})

    class Broken:
        def get(self, path):
            raise TimeoutError("timed out")

    class Seat:
        client = Broken()

    with pytest.raises(caption.RecordUnreadable):
        caption.read_record(Seat(), "m", {})


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
        from drafting_testkit import DECL, POS

        return {"decl_2030_050": DECL, "pos": POS}[key]


def test_the_declaration_is_attached_before_the_proof_of_service_with_its_counts_filled():
    doc, notes = render.attach(_set(20), "discovery_set", _Firm(), _digest(PRIOR20))
    assert doc.index("DECLARATION FOR ADDITIONAL DISCOVERY") < doc.index("PROOF OF SERVICE")
    assert "authoring comment" not in doc
    assert "a total of 20 interrogatories" in doc and "contains 20 specially" in doc
    assert any("40 special interrogatories to this party" in n for n in notes)


def test_an_unreadable_prior_count_leaves_paragraph_4_to_the_attorney():
    doc, notes = render.attach(_set(20), "discovery_set", _Firm(), _digest(UNREADABLE))
    assert "{{ATTORNEY: a prior set's count is not readable" in doc and "{{FILL: number of interrogatories" not in doc
    assert any("paragraph 4 is left to the attorney" in n for n in notes)


def test_a_model_written_declaration_and_proof_of_service_are_replaced_by_the_firms():
    model = _set(5).replace(
        "**PROOF OF SERVICE**", "**DECLARATION FOR ADDITIONAL DISCOVERY**\n\nI declare.\n\n**PROOF OF SERVICE**"
    )
    doc, notes = render.attach(model, "discovery_set", _Firm(), _digest())
    assert "DECLARATION" not in doc and "I declare." not in doc and "Served." not in doc
    assert doc.count("PROOF OF SERVICE") == 1 and "# PROOF OF SERVICE" in doc
    assert notes[0].startswith("removed a model-written")


@pytest.mark.parametrize("cls", ["discovery_set", "discovery_response"])
def test_the_job_appends_the_proof_of_service_last_with_at_service_slots_verbatim(cls):
    body = "| | |\n| --- | --- |\n| RESPONDING PARTY: | DELTA EXAMPLE |\n\nSignature block.\n"
    doc, notes = render.attach(body, cls, _Firm(), _digest())
    assert doc.rstrip().endswith("I served the foregoing document.")
    assert doc.index("Signature block.") < doc.index("# PROOF OF SERVICE")
    assert "`{{FILL: date of service | at service}}`" in doc and "authoring comment" not in doc
    assert render.attach(body, "memo", _Firm(), "") == (body, [])


def test_order_is_document_then_declaration_then_proof_of_service():
    doc, _ = render.attach(_set(36).split("**PROOF OF SERVICE**")[0], "discovery_set", _Firm(), _digest())
    assert doc.index("SPECIAL INTERROGATORY NO. 36") < doc.index("# DECLARATION") < doc.index("# PROOF OF SERVICE")


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
    same, _ = caption.compare(fields, {**base, "court": "Superior Court of California, County of Exampletown"}, "c")
    assert same == []
    typo, _ = caption.compare(fields, {**base, "court": "Superior Court of California, County of Exampletowm"}, "c")
    assert [d["field"] for d in typo] == ["court"]
