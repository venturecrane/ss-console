"""The firm's CRABTREE demand layout (house_crabtree.py), the demand terms code
reads off the request (terms.py: the figure and the signer), and the format
check that refuses anything else (format_check ``layout: crabtree``).

On 2026-11-10 the firm asked that every demand the Operator writes look like
Craig Ashton's S. Crabtree letter. Every fixture here is synthetic
(demand_crabtree_testkit.py); the real letter is the $0 rehearsal's answer key
and never leaves the firm's workspace."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from demand_crabtree_testkit import (
    BAR,
    CRABTREE_DRAFT,
    INITIALS,
    SF_OFFICE,
    SIGNER,
    TITLE,
    edit_config,
    make_crabtree_inputs,
    make_crabtree_reference,
    variant,
)
from demand_numbered_testkit import NUMBERED_DRAFT, make_numbered_reference, variant as numbered_variant
from demand_testkit import SIGNATURE, ScriptedClient, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs.demand import firm as firm_mod, format_check, house_crabtree, house_numbered, signers, terms
from jobs.demand.house import RenderError
from jobs.demand.run import DemandRun
from operator_connector_sdk.library import roster as roster_mod

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
ATTORNEY = "Gamma Counsel"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


# ---- the request's terms -------------------------------------------------------------
@pytest.mark.parametrize(
    "text,cents",
    [
        ("Please write a demand for Fasulo.", None),
        ("Craig wants a $75,000 demand on this one.", 7_500_000),
        ("75K DEMAND, policy limits unknown", 7_500_000),
        ("Demand type: $75,000", 7_500_000),
        ("make a demand of $100,000.00 to the carrier", 10_000_000),
        ("a $75K sum-certain demand", 7_500_000),
        ("Medicals are $5,000. Please draft the policy limits demand.", None),
        ("Demand type: [Pre-lit time-limited demand / policy-limits demand / UIM / post-filing demand]", None),
        ("demand 2 of 3 for this client", None),
    ],
)
def test_the_demand_figure_is_read_from_the_request_only_when_tied_to_the_word_demand(text, cents):
    assert terms.demand_figure(text) == cents


def test_two_different_demand_figures_are_unclear():
    with pytest.raises(terms.FigureUnclear, match=r"\$50,000, \$75,000"):
        terms.demand_figure("a $75,000 demand; or maybe a $50,000 demand")


@pytest.mark.parametrize(
    "cents,words",
    [
        (7_500_000, "SEVENTY-FIVE THOUSAND DOLLARS"),
        (10_000_000, "ONE HUNDRED THOUSAND DOLLARS"),
        (125_050_000, "ONE MILLION TWO HUNDRED FIFTY THOUSAND FIVE HUNDRED DOLLARS"),
        (1_500_050, "FIFTEEN THOUSAND DOLLARS AND 50/100"),
    ],
)
def test_the_figure_in_words(cents, words):
    assert terms.words(cents) == words


NAMES = {
    "Chris Price": ["Chris Price", "Christopher A. Price"],
    "Craig Ashton": ["Craig Ashton", "Craig Fontaine Ashton", "Craig F. Ashton"],
}


@pytest.mark.parametrize(
    "text,who",
    [
        ("Requesting attorney: Christopher Price\nPlease draft.", None),  # not a form the firm writes for him
        ("Requesting attorney: Christopher A. Price\nmentions Craig Ashton too", "Chris Price"),
        ("Please have Craig Ashton sign this demand.", "Craig Ashton"),
        ("Draft the demand. Craig F. Ashton will sign.", "Craig Ashton"),
        ("Chris Price and Craig Ashton discussed it.", None),
        ("Chris's paralegal asked for a demand.", None),
    ],
)
def test_the_signer_is_the_one_attorney_the_request_names(text, who):
    assert terms.named_attorney(text, NAMES) == who


# ---- the config ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "edit,want",
    [
        (lambda d: d["variants"]["pre_suit"].pop("box_title"), "box_title: required for the crabtree layout"),
        (lambda d: d["variants"]["pre_suit"].pop("sf_office"), "sf_office: required for the crabtree layout"),
        (lambda d: d["variants"]["pre_suit"].update(response_days="thirty"), "response_days: expected int"),
        (lambda d: d["attorneys"][ATTORNEY].update(bar_number="12 34"), "bar_number: expected letters"),
    ],
)
def test_the_crabtree_keys_are_checked(tmp_path, edit, want):
    root = make_crabtree_inputs(tmp_path / "in")
    edit_config(root, lambda d: (edit(d), d)[1])
    with pytest.raises(firm_mod.DemandConfigError) as exc:
        firm_mod.load(root)
    assert want in str(exc.value)


def test_crabtree_is_the_default_and_the_old_house_layout_stays_as_an_unselected_format(tmp_path):
    firm = firm_mod.load(make_crabtree_inputs(tmp_path / "in"))
    row = firm.attorney_for(ATTORNEY)
    assert "format" not in row and firm.variants_for(row)["pre_suit"]["layout"] == "crabtree"
    assert firm.variants_for(firm.attorney_for("Example Lawyer"))["pre_suit"]["layout"] == "crabtree"
    assert firm.data["formats"]["house"]["pre_suit"].get("layout", "house") == "house"
    picked = firm.select("pre_suit", row, 7_500_000)
    assert picked.figure == 7_500_000 and picked.input_path("house_reference").name == "house-reference-crabtree.docx"


def test_the_signature_row_carries_the_bar_number_from_the_roster_or_the_inputs(tmp_path):
    firm = firm_mod.load(make_crabtree_inputs(tmp_path / "in"))
    absent = roster_mod.Roster(status=roster_mod.ABSENT)
    assert signers.signer_for(firm, ATTORNEY, absent)["bar_number"] == BAR
    person = roster_mod.Person(
        name=ATTORNEY,
        attorney=True,
        bar_number="123456",
        signs={
            "demand": {"signer": SIGNER, "title": TITLE, "initials": INITIALS},
            "letters": {"name": "G. Q. Counsel"},
        },
    )
    ok = roster_mod.Roster(status=roster_mod.OK, people=(person,))
    row = signers.signer_for(firm, ATTORNEY, ok)
    assert row["bar_number"] == "123456" and row["script"] == "G. Q. Counsel"
    who, named = signers.who_signs(firm, "Example Lawyer", f"Please have {SIGNER} sign.", ok)
    assert (who, named) == (ATTORNEY, True)
    assert signers.who_signs(firm, "Example Lawyer", "Draft the demand.", ok) == ("Example Lawyer", False)


# ---- render and format check, directly -------------------------------------------------
def _render(tmp_path: Path, md: str = CRABTREE_DRAFT, figure: int | None = None, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ref = tmp_path / "ref.docx"
    make_crabtree_reference(ref)
    md, notes = house_crabtree.settle_expiry(md)
    path, _n = house_crabtree.render(
        md,
        ref,
        tmp_path / "out",
        variant=kw.pop("variant", variant()),
        signature=SIGNATURE,
        signer=SIGNER,
        signer_title=TITLE,
        author="Example & Example, LLP",
        client_label="Alpha Example",
        initials=INITIALS,
        bar_number=BAR,
        figure=figure,
        script="G. Q. Counsel",
    )
    return path, notes


def _check(path: Path, v: dict | None = None):
    v = v or variant()
    return format_check.check(
        path,
        signature=SIGNATURE,
        signer_title=TITLE,
        footer_markers=v["footer_markers"],
        variant=format_check.Variant.from_config(v),
        signer=SIGNER,
        initials=INITIALS,
    )


def _texts(path: Path) -> list[str]:
    import docx

    return [p.text for p in docx.Document(str(path)).paragraphs if p.text.strip()]


def _boxes(path: Path) -> list[list[str]]:
    import docx

    return [[p.text for p in t.cell(0, 0).paragraphs] for t in docx.Document(str(path)).tables if len(t.rows) == 1]


def test_a_policy_limits_render_round_trips_through_its_format_check(tmp_path):
    path, notes = _render(tmp_path)
    res = _check(path)
    assert res.ok, res.fails
    assert notes == []
    texts = _texts(path)
    assert texts[:3] == [
        "October 6, 2026",
        "VIA ELECTRONIC MAIL AND CERTIFIED MAIL, RETURN RECEIPT REQUESTED",
        "Attn: Pat Adjuster",
    ]
    re_lines = [t for t in texts if "\t:\t" in t]
    assert re_lines[0] == "RE:\tOur Client\t:\tAlpha Example"
    assert [t.split("\t")[-3] for t in re_lines] == [
        "Our Client",
        "Claim Number",
        "Date of Loss",
        "Place of Loss",
        "Your Insured",
    ]
    banner, box = _boxes(path)
    assert banner == [
        "TIME-LIMITED POLICY LIMITS DEMAND",
        "Example Code Civ. Proc. §§ 999-999.5",
        "THIS DEMAND EXPIRES AT 5:00 P.M. PACIFIC TIME",
        "ON THURSDAY, NOVEMBER 5, 2026",
    ]
    assert box == [
        "TIME-LIMITED DEMAND - EXAMPLE CODE CIV. PROC. § 999.1",
        "Date of Demand: October 6, 2026",
        "Response Period: 30 days",
        "Deadline: 5:00 P.M. Pacific Time, Thursday, November 5, 2026",
    ]
    assert texts[texts.index("Dear Pat Adjuster:") + 1] == "PREAMBLE - SUMMARY OF CLAIM"
    assert "A.  Medical Providers and Billings to Date" in texts and "B.  Liability in Brief" in texts
    assert "A.  The Insured" in texts  # lettering restarts in each section
    heads = [t for t in texts if t.split(". ")[0] in ("I", "II", "III", "IV", "V", "VI", "VII")]
    assert heads == [
        f"{n}. {h.upper()}" for n, h in zip(("I", "II", "III", "IV", "V", "VI", "VII"), variant()["headings"])
    ]
    assert any("FULL AVAILABLE POLICY LIMITS" in t for t in texts) and not any("[DEMAND FIGURE]" in t for t in texts)
    i = texts.index("Very truly yours,")
    assert texts[i + 1 : i + 8] == [
        SIGNATURE,
        "G. Q. Counsel",
        f"{SIGNER}, (SBN {BAR})",
        TITLE,
        "Attorneys for Claimant Alpha Example",
        INITIALS,
        "Enclosures: Exhibits 1\u20132 as indexed",
    ]
    assert texts[i + 8] == "INDEX OF EXHIBITS" and texts[i + 9] == "Exhibit 1:\tER record 1-15-26, Exampletown ER."
    import docx

    head = docx.Document(str(path)).sections[0].first_page_header.paragraphs[0].text
    assert SF_OFFICE in head and "{" not in head


def test_a_sum_certain_render_names_the_figure_and_drops_the_statute_line(tmp_path):
    path, _ = _render(tmp_path, figure=7_500_000)
    assert _check(path).ok, _check(path).fails
    banner = _boxes(path)[0]
    assert banner == [
        "$75,000 DEMAND - SEVENTY-FIVE THOUSAND DOLLARS",
        "THIS DEMAND EXPIRES AT 5:00 P.M. PACIFIC TIME",
        "ON THURSDAY, NOVEMBER 5, 2026",
    ]
    texts = _texts(path)
    assert any("$75,000.00" in t for t in texts) and not any("POLICY LIMITS" in t for t in texts)


def test_the_deadline_is_codes_and_a_drafted_one_is_replaced_through_the_letter():
    md = CRABTREE_DRAFT.replace("Thursday, November 5, 2026", "Friday, November 6, 2026")
    out, notes = house_crabtree.settle_expiry(md)
    assert "November 6" not in out and out.count("Thursday, November 5, 2026") == 2
    assert notes and "Friday, November 6, 2026" in notes[0]
    # a weekend deadline moves to Monday and says so
    assert house_crabtree.deadline(date(2026, 10, 9)) == date(2026, 11, 9)
    _out, wk = house_crabtree.settle_expiry(CRABTREE_DRAFT.replace("October 6, 2026", "October 9, 2026"))
    assert any("weekend" in n for n in wk)


def test_a_letter_that_writes_its_own_amount_is_refused(tmp_path):
    with pytest.raises(RenderError, match="DEMAND FIGURE"):
        _render(tmp_path, CRABTREE_DRAFT.replace("[DEMAND FIGURE]", "$1,000,000"))


@pytest.mark.parametrize(
    "edit,want",
    [
        (lambda m: m.replace("## Special Damages", "## Specials"), "'SPECIAL DAMAGES' missing"),
        (lambda m: m.replace("## Index of Exhibits", "## Exhibits"), "INDEX OF EXHIBITS"),
        (lambda m: m.replace("## Demand\n", "## The Duty to Accept\n\nx.\n\n## Demand\n"), "Duty To Accept"),
    ],
)
def test_the_crabtree_check_refuses_an_off_format_letter(tmp_path, edit, want):
    path, _ = _render(tmp_path, edit(CRABTREE_DRAFT))
    res = _check(path)
    assert not res.ok and any(want in f for f in res.fails), res.fails


def test_falsifier_the_old_numbered_format_fails_the_crabtree_check(tmp_path):
    """The format the seat rendered for this attorney before (CASE DISTILLED,
    no Attn:, a RE table, Cordially) must not pass as the new one."""
    ref = tmp_path / "ref.docx"
    make_numbered_reference(ref)
    path, _ = house_numbered.render(
        NUMBERED_DRAFT,
        ref,
        tmp_path / "out",
        variant=numbered_variant(),
        signature=SIGNATURE,
        signer=SIGNER,
        signer_title="Attorney for Alpha Example",
        author="Example & Example, LLP",
        client_label="Alpha Example",
    )
    res = _check(path)
    wants = (
        "no 'Attn:' line",
        "no banner box",
        "PREAMBLE",
        "'Cordially,' belongs",
        "Case Distilled",
        "no 'Very truly yours,'",
    )
    assert all(any(w in f for f in res.fails) for w in wants), res.fails


# ---- end to end -------------------------------------------------------------------------
@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, inputs: Path, responsible: str, request: str):
    seat = seat_with(standard_docs())
    seat.facts = {**seat.facts, "responsible_attorney": responsible}
    client = ScriptedClient(draft=CRABTREE_DRAFT)
    jd = make_job(tmp_path / "job")
    job = json.loads((jd / "job.json").read_text())
    job["request_text"] = request
    (jd / "job.json").write_text(json.dumps(job))
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _m: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r.run(), seat, client, tmp_path / "job" / "data" / "out" / "demand"


def test_a_request_naming_the_signer_and_a_figure_is_rendered_in_the_crabtree_layout(tmp_path, pricing):
    """Another attorney is responsible for the matter; the request names the
    signer and a figure (the Fasulo case, 2026-11-10)."""
    request = f"DELIVERABLE 1: gap audit. DELIVERABLE 2: a $75,000 demand. {SIGNER} will sign."
    v, seat, client, out = _run(tmp_path, pricing, make_crabtree_inputs(tmp_path / "in"), "Example Lawyer", request)
    assert v.outcome == "delivered", v.reason
    compose = next(c for c in client.calls if "COMPOSE-PROMPT" in json.dumps(c.get("system")))
    sent = json.dumps(compose)
    assert (
        "a sum-certain demand of $75,000.00" in sent and f"the signing attorney (decided by code): {ATTORNEY}" in sent
    )
    path = out / "Demand.Alpha Example.docx"
    texts = _texts(path)
    assert f"{SIGNER}, (SBN {BAR})" in texts and _boxes(path)[0][0] == "$75,000 DEMAND - SEVENTY-FIVE THOUSAND DOLLARS"
    assert {s["name"] for s in seat.sent} >= {"Demand.Alpha Example.docx"}


def test_a_request_naming_two_figures_holds_before_anything_is_spent(tmp_path, pricing):
    request = "Draft a $75,000 demand. Actually a $50,000 demand."
    v, seat, client, _ = _run(tmp_path, pricing, make_crabtree_inputs(tmp_path / "in"), ATTORNEY, request)
    assert v.outcome == "held" and "more than one demand figure" in v.reason
    assert client.calls == [] and seat.sent == []


def test_the_figure_is_not_read_for_a_layout_that_does_not_write_it(tmp_path, pricing):
    """The house layout's demand is worded from its own skeleton; two figures in
    its request are not a reason to hold it."""
    from demand_testkit import DRAFT, make_inputs

    seat = seat_with(standard_docs())
    client = ScriptedClient(draft=DRAFT)
    jd = make_job(tmp_path / "job")
    job = json.loads((jd / "job.json").read_text())
    job["request_text"] = "Draft a $75,000 demand. Actually a $50,000 demand."
    (jd / "job.json").write_text(json.dumps(job))
    v = DemandRun(
        jd,
        inputs_dir=str(make_inputs(tmp_path / "in")),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _m: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    ).run()
    assert v.outcome == "delivered", v.reason


def test_the_firms_marks_and_kinds_render(tmp_path):
    """Underlined key terms, a PROJECTED total row, banded body rows, a study
    head inside a section, and a physician's quotation; the last two fall back
    to the region head and the italic block when the reference has no own kind."""
    import docx
    from docx.oxml.ns import qn

    md = CRABTREE_DRAFT.replace(
        "- A cervical strain (ER record 1-15-26, p. 1).",
        "- A **__cervical strain__** and __spasm__ (ER record 1-15-26, p. 1).\n\n>> The physician's words.",
    )
    md = md.replace(
        "| {{FILL: future care}} | {{FILL: cost}} |",
        "| {{FILL: future care}} | {{FILL: cost}} |\n| PROJECTED FUTURE MEDICAL EXPENSES | {{FILL: sum}} |",
    )
    md = md.replace(
        "| Exampletown ER | Emergency care, January 15, 2026 | $1,200.00 |",
        "| Exampletown ER | Emergency care, January 15, 2026 | $1,200.00 |\n| Exampletown ER | Follow-up | $0.00 |",
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    ref = tmp_path / "ref.docx"
    make_crabtree_reference(ref, banded=True)
    md, _ = house_crabtree.settle_expiry(md)
    path, _ = house_crabtree.render(
        md,
        ref,
        tmp_path / "out",
        variant=variant(),
        signature=SIGNATURE,
        signer=SIGNER,
        signer_title=TITLE,
        author="Example",
        client_label="Alpha Example",
        initials=INITIALS,
        bar_number=BAR,
    )
    d = docx.Document(str(path))
    runs = [r for p in d.paragraphs for r in p.runs]
    term = next(r for r in runs if r.text == "cervical strain")
    assert term.bold and term.underline and next(r for r in runs if r.text == "spasm").underline
    assert any(p.text == "The physician's words." for p in d.paragraphs)
    assert _check(path).ok, _check(path).fails
    providers = next(t for t in d.tables if t.cell(0, 0).text == "Provider")
    assert len(providers.rows) == 4 and providers.cell(3, 0).text == "TOTAL"
    future = next(t for t in d.tables if t.cell(0, 0).text.startswith("Anticipated"))
    assert future.cell(len(future.rows) - 1, 0).text == "PROJECTED FUTURE MEDICAL EXPENSES"
    # the reference's banded rows: the second body row is the second prototype body row
    ref_doc = docx.Document(str(ref))
    proto_rows = next(t for t in ref_doc.tables if t.cell(0, 0).text == "PROTO:providers").rows
    assert len(proto_rows) == 4 and providers.rows[2]._tr.find(qn("w:trPr")) == proto_rows[2]._tr.find(qn("w:trPr"))
