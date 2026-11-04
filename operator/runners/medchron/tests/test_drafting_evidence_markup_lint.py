"""Evidence markup and lint: the drafter writes tokens; anything a reader
could check against a page, typed by hand, is refused.

Synthetic record only (``evidence_testkit``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from evidence_testkit import CHECKER, CHART, candidates, corpus

from medchron.drafting.evidence import lint, markup, verify
from medchron.drafting.evidence.shapes import Ledger


@pytest.fixture(autouse=True)
def _gate_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def led() -> Ledger:
    return verify.verify(candidates(), corpus())


def _id(led: Ledger, claim: str) -> str:
    return next(f.id for f in led if f.claim == claim)


def _codes(findings) -> list[str]:
    return sorted({f.code for f in findings})


def test_tokens_parse_and_the_lanes_own_markers_pass_through():
    text = "A {{F:F0001,F0002}} b {{Q:F0003|some words}} {{SPAN:F0001,F0004|months}} {{T:charges}} {{A:?|ask her}} {{FILL: date | at service}} {{ATTORNEY: check}}"
    toks, bad = markup.parse(text)
    assert bad == []
    assert [t.kind for t in toks] == ["F", "Q", "SPAN", "T", "A"]
    assert toks[0].ids == ("F0001", "F0002") and toks[1].text == "some words" and toks[2].text == "months"
    assert toks[3].name == "charges" and toks[4].text == "ask her"


@pytest.mark.parametrize(
    "bad",
    [
        "{{X:F0001}}",
        "{{N:F0001,F0002}}",
        "{{SPAN:F0001}}",
        "{{SPAN:F0001,F0002|fortnights}}",
        "{{T:costs}}",
        "{{F:}}",
        "{{F:12}}",
        "{{A:?}}",
        "{{N:F0001|x}}",
        "{{ F0001 }}",
    ],
)
def test_malformed_tokens_are_refused(bad):
    _toks, findings = markup.parse(f"Text {bad} here.")
    assert findings and findings[0].code == "malformed_token"


def test_unknown_rejected_and_mismatched_ids_are_refused(led):
    paid, dos, swollen, rejected = (
        _id(led, c) for c in ("Plan paid", "Surgery date", "Knee still swollen", "No prior knee injury")
    )
    text = f"{{{{F:F9999}}}} {{{{F:{rejected}}}}} {{{{N:{dos}}}}} {{{{D:{paid}}}}} {{{{SUM:{paid},{swollen}}}}}"
    codes = [f.code for f in lint.lint(text, led)]
    assert "unknown_id" in codes and "rejected_id" in codes and codes.count("kind_mismatch") >= 3


def test_a_sum_of_billed_and_paid_is_refused(led):
    billed, paid = _id(led, "Surgery charges"), _id(led, "Plan paid")
    f = lint.lint(f"Together {{{{SUM:{billed},{paid}}}}} {{{{F:{billed},{paid}}}}}.", led)
    assert "role_mixed" in _codes(f)


def test_a_hand_typed_cite_the_gate_passes_today_is_refused():
    """Today's gate passes a correct quote with a hand-typed pin to the wrong
    page; lint refuses both the quote and the pin."""
    chk = verify.checker()
    body = f'The chart says "the patient was doing well, but her knee remained swollen" (FILE: {CHART}, p. 1).'
    text = "\n".join(f"[p.{p}]\n{t}" for p, t in corpus()[CHART].pages.items())
    findings, _ = chk.gate_quote_contiguity(chk.Draft(Path("d.md"), body), [chk.SourceDoc(Path(CHART), text)])
    assert findings == []
    assert {"hand_quote", "hand_cite"} <= set(_codes(lint.lint(body, Ledger())))


@pytest.mark.parametrize(
    "sentence, code",
    [
        ("The surgery cost $8,300.00 in all.", "hand_money"),
        ("She lost 4,100 dollars in wages.", "hand_money"),
        ("Surgery was on February 1, 2026.", "hand_date"),
        ("She was seen in March 2026.", "hand_date"),
        ("She was seen on 02/01/2026.", "hand_date"),
        ("She attended 14 physical therapy visits.", "hand_count"),
        ("She attended twelve sessions.", "hand_count"),
        ("She is a 42-year-old baker.", "hand_count"),
        ("Bills totaling 3 providers were sent.", "hand_count"),
        ("Her impairment is 12% of the whole person.", "hand_count"),
        ("She testified at 12:4-5 that her neck hurt.", "hand_cite"),
        ("Example Dep. said so.", "hand_cite"),
        ("See Example v. Example (2020) 45 Cal.App.5th 123.", "hand_cite"),
        ("Id. at 4.", "hand_cite"),
        ("The chart is clear (pp. 2-3).", "hand_cite"),
        ("> the patient was doing well but swollen", "hand_quote"),
    ],
)
def test_hand_typed_figures_dates_counts_and_cites_are_refused(sentence, code):
    assert code in _codes(lint.lint(sentence, Ledger()))


def test_plain_prose_and_times_of_day_pass():
    assert lint.lint("She left for work at 7:30 a.m. and the crash followed. Her neck hurt.", Ledger()) == []


def test_skeleton_boilerplate_is_the_firms_words_and_is_exempt():
    skeleton = (
        "Pursuant to the court's order of March 3, 2026, plaintiff submits this brief.\n\n{{FILL: date | at service}}"
    )
    text = (
        "Pursuant to the court's order of March 3, 2026, plaintiff submits this brief. The crash was on March 4, 2026."
    )
    got = lint.lint(text, Ledger(), exempt=[skeleton])
    assert [f.snippet for f in got] == ["March 4, 2026"]


def test_a_figure_or_date_with_no_cite_in_its_sentence_is_refused(led):
    billed = _id(led, "Surgery charges")
    assert "uncited" in _codes(lint.lint(f"The surgery was billed at {{{{N:{billed}}}}}.", led))
    assert lint.lint(f"The surgery was billed at {{{{N:{billed}}}}} {{{{F:{billed}}}}}.", led) == []


def test_opposing_counsels_number_stated_as_fact_is_refused(led):
    """Taxonomy item 1: the drafter took a figure from defense counsel's
    question and stated it as the bills."""
    q = _id(led, "Defense figure")
    assert "role_contradiction" in _codes(lint.lint(f"Her bills were {{{{N:{q}}}}} {{{{F:{q}}}}}.", led))
    assert lint.lint(f"Defense counsel asked whether her bills were only {{{{N:{q}}}}} {{{{F:{q}}}}}.", led) == []


def test_a_billed_amount_called_paid_is_refused(led):
    billed, paid = _id(led, "Surgery charges"), _id(led, "Plan paid")
    assert "role_contradiction" in _codes(lint.lint(f"The plan paid {{{{N:{billed}}}}} {{{{F:{billed}}}}}.", led))
    ok = f"The surgery was billed at {{{{N:{billed}}}}} {{{{F:{billed}}}}}, and the plan paid {{{{N:{paid}}}}} {{{{F:{paid}}}}}."
    assert lint.lint(ok, led) == []


def test_an_experts_number_needs_someone_stating_it(led):
    x = _id(led, "Future care")
    assert "role_contradiction" in _codes(lint.lint(f"Future care will cost {{{{N:{x}}}}} {{{{F:{x}}}}}.", led))
    assert lint.lint(f"Dr. Example estimated future care at {{{{N:{x}}}}} {{{{F:{x}}}}}.", led) == []


def test_an_unconfirmed_fact_cannot_be_cited_as_fact(led):
    inf = next(f for f in led if f.claim == "Plaintiff verified the responses").with_(kind="inference")
    led2 = Ledger(tuple(inf if f.id == inf.id else f for f in led))
    got = lint.lint(f"Plaintiff verified her responses {{{{F:{inf.id}}}}}.", led2)
    assert "kind_mismatch" in _codes(got) and "{{A:id}}" in got[0].message
    assert lint.lint(f"Plaintiff's verification {{{{A:{inf.id}}}}} is attached.", led2) == []


def test_the_repair_brief_names_each_sentence_and_its_findings():
    text = "Surgery was on February 1, 2026 (FILE: x, p. 2). Her neck hurt."
    brief = lint.repair_brief(lint.lint(text, Ledger()))
    assert (
        brief.startswith("- SENTENCE: Surgery was on February 1, 2026")
        and "[hand_date]" in brief
        and "[hand_cite]" in brief
    )
    assert "Her neck hurt" not in brief
