"""Evidence render: code writes every cite, quote, figure, date, aggregate and
table last, and proves every rendered span traces to a verified fact.

Synthetic record only (``evidence_testkit``)."""

from __future__ import annotations

import pytest
from evidence_testkit import CHECKER, BILL, CHART, DEPO, candidates, corpus

from jobs.drafting import howell
from jobs.drafting.evidence import lint, render, verify
from jobs.drafting.evidence.shapes import Fact, Ledger


@pytest.fixture(autouse=True)
def _gate_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def led() -> Ledger:
    return verify.verify(candidates(), corpus())


def _id(led: Ledger, claim: str) -> str:
    return next(f.id for f in led if f.claim == claim)


def test_a_quote_renders_the_pages_characters_exactly_with_its_pin(led):
    """The model's excerpt had a semicolon and the wrong page; the brief gets
    the comma and the right page, by construction."""
    q = _id(led, "Knee still swollen")
    out = render.render(
        f"The chart notes {{{{Q:{q}|the patient was doing well; but her knee remained swollen}}}}.", led
    )
    assert (
        out.text
        == f'The chart notes "the patient was doing well, but her knee remained swollen" (FILE: {CHART}, p. 2).'
    )
    whole = render.render(f"{{{{Q:{q}}}}}", led).text
    assert (
        whole
        == f'"Assessment: the patient was doing well, but her knee remained swollen after therapy." (FILE: {CHART}, p. 2)'
    )


def test_a_fragment_the_excerpt_does_not_carry_is_refused(led):
    q = _id(led, "Knee still swollen")
    with pytest.raises(render.RenderError):
        render.render(f"{{{{Q:{q}|her knee was completely healed}}}}", led)


def test_testimony_cites_by_deponent_page_and_line(led):
    t = _id(led, "Neck stiff that night")
    out = render.render(f"She testified {{{{Q:{t}|by that night my neck was stiff}}}}.", led).text
    assert out == 'She testified "by that night my neck was stiff" (Example Dep. 12:4-5).'
    assert render.render(f"{{{{F:{t}}}}}", led, deponents={DEPO: "Exampleson"}).text == "(Exampleson Dep. 12:4-5)"


def test_cites_group_one_files_pages(led):
    a, b, c = _id(led, "Surgery charges"), _id(led, "First clinic visit"), _id(led, "Future care")
    knee = _id(led, "Knee still swollen")
    out = render.render(f"{{{{F:{a},{c},{knee},{b}}}}}", led).text
    assert out == f"(FILE: {BILL}, p. 2; FILE: {CHART}, pp. 1-3)"


def test_figures_dates_and_aggregates_are_computed_in_code(led):
    billed, paid, dos, first = (
        _id(led, c) for c in ("Surgery charges", "Plan paid", "Surgery date", "First clinic visit")
    )
    text = (
        f"Surgery on {{{{D:{dos}}}}} {{{{F:{dos}}}}} was billed at {{{{N:{billed}}}}} {{{{F:{billed}}}}}. "
        f"Treatment ran {{{{SPAN:{first},{dos}}}}} over {{{{COUNT:{first},{dos}}}}} visits {{{{F:{first},{dos}}}}}."
    )
    out = render.render(text, led).text
    assert "Surgery on February 1, 2026" in out and "billed at $8,300.00 (FILE" in out
    assert "Treatment ran 12 days over 2 visits" in out
    assert "$830,000.00" not in out
    assert render.render(f"{{{{SUM:{paid}}}}}", led).text == "$4,100.00"


def test_span_units():
    a = Fact("F0001", "x", 1, "date", "e", "c", value="2024-01-31")
    b = Fact("F0002", "x", 1, "date", "e", "c", value="2026-03-01")
    led = Ledger((a, b))
    got = [render.render("{{SPAN:F0001,F0002" + u + "}}", led).text for u in ("", "|weeks", "|months", "|years")]
    assert got == ["760 days", "108 weeks", "25 months", "2 years"]


def _cell(v: str) -> dict:
    return {"value": v, "sources": [{"id": "b1", "name": "Bill"}]}


def test_the_charges_table_comes_from_howell():
    cell = _cell
    table = [
        {
            "provider": "Exampletown Surgical",
            "dates_of_service": cell("02/01/2026"),
            "billed": cell("$8,300.00"),
            "paid": cell("$4,100.00"),
            "outstanding": cell("$0.00"),
        }
    ]
    assert render.render("{{T:charges}}", Ledger(), charges=table).text == howell.markdown(table)
    with pytest.raises(render.RenderError):
        render.render("{{T:charges}}", Ledger())


def test_an_attorney_marker_carries_the_claim_and_its_cite(led):
    f = next(x for x in led if x.claim == "Plaintiff verified the responses")
    inf = f.with_(kind="inference", image_reading="signature line blank")
    led2 = Ledger(tuple(inf if x.id == f.id else x for x in led))
    out = render.render(f"{{{{A:{f.id}}}}} {{{{A:?|Is the verification signed?}}}}", led2).text
    assert out.startswith(
        "{{ATTORNEY: confirm before relying on it: Plaintiff verified the responses (FILE: Plaintiff verification.pdf, p. 1); signature line blank}}"
    )
    assert out.endswith("{{ATTORNEY: Is the verification signed?}}")


def test_render_refuses_any_token_lint_would_refuse(led):
    with pytest.raises(render.RenderError) as e:
        render.render("{{F:F9999}}", led)
    assert e.value.findings[0].code == "unknown_id"


def _brief(led: Ledger) -> str:
    billed, paid, q = _id(led, "Surgery charges"), _id(led, "Plan paid"), _id(led, "Knee still swollen")
    return (
        f"The surgery was billed at {{{{N:{billed}}}}} {{{{F:{billed}}}}}, and the plan paid {{{{N:{paid}}}}} {{{{F:{paid}}}}}. "
        f"The chart notes {{{{Q:{q}|the patient was doing well, but her knee remained swollen}}}}."
    )


def test_every_rendered_span_traces_to_a_verified_fact(led):
    text = _brief(led)
    assert lint.lint(text, led) == []
    out = render.render(text, led)
    rep = render.assert_spans(out.text, out.spans, led)
    assert rep.ok and rep.spans == rep.traced == 5


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace("$8,300.00", "$830,000.00"),  # a figure changed after render
        lambda t: t.replace("doing well, but", "doing well; but"),  # a quote's punctuation changed (taxonomy item 16)
        lambda t: t.replace("p. 2)", "p. 3)", 1),  # a pin changed
    ],
)
def test_any_post_render_change_to_a_span_fails(led, edit):
    out = render.render(_brief(led), led)
    rep = render.assert_spans(edit(out.text), out.spans, led)
    assert not rep.ok and any(p.code == "span_edited" for p in rep.problems)


def test_prose_edited_around_the_spans_still_passes(led):
    out = render.render(_brief(led), led)
    edited = "In short, " + out.text.replace("The chart notes", "Her treating chart notes")
    assert render.assert_spans(edited, out.spans, led).ok


def test_a_cite_or_figure_added_after_render_outside_any_span_fails(led):
    out = render.render(_brief(led), led)
    rep = render.assert_spans(out.text + f" She paid $50.00 herself (FILE: {BILL}, p. 1).", out.spans, led)
    assert not rep.ok and {"untraced_hand_money", "untraced_hand_cite"} <= {p.code for p in rep.problems}


def test_a_span_whose_fact_was_later_rejected_no_longer_traces(led):
    out = render.render(_brief(led), led)
    paid = _id(led, "Plan paid")
    led2 = Ledger(tuple(f.with_(status="rejected") if f.id == paid else f for f in led))
    rep = render.assert_spans(out.text, out.spans, led2)
    assert not rep.ok and any(p.code == "span_untraced" for p in rep.problems)


STORE = {"example-v-example": "Example v. Example (2020) 45 Cal.App.5th 123"}


def test_an_authority_renders_from_the_store_and_one_not_on_file_goes_to_the_attorney(led):
    text = "The rule is settled. {{L:example-v-example}} {{L:?|Other v. Other (1999) 1 Cal.4th 2}}"
    out = render.render(text, led, authorities=STORE)
    assert out.text.startswith(
        "The rule is settled. Example v. Example (2020) 45 Cal.App.5th 123 Other v. Other (1999) 1 Cal.4th 2 "
        "{{ATTORNEY: no primary text"
    )
    assert render.assert_spans(out.text, out.spans, led, authorities=STORE).ok
    with pytest.raises(render.RenderError):
        render.render("{{L:no-such-case}}", led, authorities=STORE)
    assert [f.code for f in lint.lint("{{L:no-such-case}}", led, authorities=STORE)] == ["unknown_authority"]
    assert lint.lint("{{L:no-such-case}}", led) == []  # no store given: render decides
    assert lint.lint("See Example v. Example (2020) 45 Cal.App.5th 123.", led)[0].code == "hand_cite"
