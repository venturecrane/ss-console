#!/usr/bin/env python3
"""Tests for the California attorney-advertising gate (ADR 0098).

Every rule the gate implements has a failing item that fails on THAT rule and a
corrected item that no longer carries it, so no rule here is a check that
cannot fail. The registers are the synthetic example firm in
``fixtures/marketing-registers.example.yaml``.

    python3 -m pytest operator/templates/drafting/tests/test_advertising_gate_check.py
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("advertising_gate_check", _HERE / "advertising_gate_check.py")
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

EXAMPLE = _HERE / "fixtures" / "marketing-registers.example.yaml"
_LOADED, _ERR = gate.load_registers(EXAMPLE)
assert _ERR is None, _ERR
REGS: dict = _LOADED  # type: ignore[assignment]

D = REGS["disclosures"]
IDENT = "Rivera & Cole Injury Lawyers, Riverside."


def regs(**changes) -> dict:
    out = copy.deepcopy(REGS)
    for key, value in changes.items():
        out[key] = value
    return out


def social(body: str) -> str:
    return f"{body}\n\n{IDENT}"


def blog(body: str, disclaimer: str | None = None) -> str:
    return f"A post title\n\n{body}\n\n{IDENT}\n\n{disclaimer or D['blog']['en']}"


def run(text: str, channel: str = "social", registers=None, lang: str = "auto"):
    return gate.check(text, channel, REGS if registers is None else registers, lang)


def has(findings, rule: str, severity: str | None = None) -> bool:
    return any(f.rule == rule and (severity is None or f.severity == severity) for f in findings)


# ---------------------------------------------------------------------------
# One clean item per channel
# ---------------------------------------------------------------------------

CLEAN = {
    "blog": blog(
        "After a crash, call the police and see a doctor. In general, California gives injured "
        "people two years to file, but exceptions exist and the dates depend on the facts."
    ),
    "social": social("Driving in the rain this week? Slow down and leave extra space."),
    "youtube": social("New episode: how an insurance claim works after a crash. Listen to the full show."),
    "gbp": "The office is open on Saturday mornings in March.",
    "review-reply": "Thank you for the kind words. We appreciate you taking the time to write.",
    "press": (
        "Rivera & Cole Injury Lawyers of Riverside will host a bike safety class on May 3 at the community center."
    ),
    "note": "Hi, here are this week's drafts for your review.",
}


@pytest.mark.parametrize("channel", list(CLEAN))
def test_a_clean_item_passes_on_every_channel(channel: str) -> None:
    findings = run(CLEAN[channel], channel)
    assert gate.verdict(findings) == "pass", findings
    blocking = [f for f in findings if f.severity == gate.BLOCK]
    assert blocking == []


def test_a_blog_post_always_carries_the_reviewer_flag() -> None:
    findings = run(CLEAN["blog"], "blog")
    assert [f.rule for f in findings] == ["R-43"]
    assert findings[0].severity == gate.FLAG


# ---------------------------------------------------------------------------
# Per rule: fails on the rule, passes once fixed
# ---------------------------------------------------------------------------

#: (rule, severity, channel, failing item, corrected item, registers or None)
CASES = [
    (
        "R-01",
        "BLOCK",
        "social",
        social("We guarantee you a great result."),
        social("We work hard on every case."),
        None,
    ),
    (
        "R-01",
        "BLOCK",
        "social",
        social("Con nosotros, vas a ganar tu caso en Riverside."),
        social("Con nosotros, tu caso recibe atención en Riverside."),
        None,
    ),
    (
        "R-02",
        "BLOCK",
        "social",
        social("Get money fast after your crash."),
        social("Get answers after your crash."),
        None,
    ),
    ("R-02", "BLOCK", "social", social("Dinero rápido para usted."), social("Respuestas claras para usted."), None),
    (
        "R-03",
        "BLOCK",
        "social",
        social("We fight for maximum compensation."),
        social("We fight for fair treatment."),
        None,
    ),
    (
        "R-05",
        "BLOCK",
        "social",
        social("Our client recovered $1.2 million."),
        social(f"Our client recovered $1.2 million. {D['results']['en']}"),
        None,
    ),
    (
        "R-06",
        "BLOCK",
        "social",
        social("We recovered $500,000 for a client."),
        social(f"We recovered $1.2 million for a client. {D['results']['en']}"),
        None,
    ),
    ("R-06", "BLOCK", "social", social("Over 30 years of experience."), social("Over 25 years of experience."), None),
    ("R-06", "BLOCK", "social", social("Hundreds of cases won."), social("Cases handled with care."), None),
    (
        "R-07",
        "BLOCK",
        "social",
        social('A client wrote: "They returned every call and explained every step."'),
        social(f'A client wrote: "They returned every call and explained every step." {D["testimonial"]["en"]}'),
        None,
    ),
    (
        "R-08",
        "BLOCK",
        "social",
        social(f'A client wrote: "They are the nicest people in town." {D["testimonial"]["en"]}'),
        social(f'A client wrote: "They returned every call and explained every step." {D["testimonial"]["en"]}'),
        None,
    ),
    (
        "R-08",
        "BLOCK",
        "social",
        social(f"Clients say we are wonderful. {D['testimonial']['en']}"),
        social("We are glad to serve Riverside."),
        None,
    ),
    (
        "R-09",
        "BLOCK",
        "social",
        social("We are the best injury lawyers in California."),
        social("We are injury lawyers in California."),
        None,
    ),
    (
        "R-10",
        "BLOCK",
        "social",
        social("Dana Rivera received the Lawyer Excellence Award."),
        social("Dana Rivera received the Riverside County Trial Lawyer of the Year award."),
        None,
    ),
    ("R-11", "FLAG", "social", social("Call for a free consultation."), social("Call us to talk."), None),
    (
        "R-12",
        "BLOCK",
        "social",
        social("No fee unless we recover for you."),
        social(f"No fee unless we recover for you. {D['costs']['en']}"),
        None,
    ),
    (
        "R-13",
        "BLOCK",
        "social",
        social("Our fee is 40% of the recovery."),
        social("Our fee is 33% of the recovery."),
        None,
    ),
    ("R-14", "BLOCK", "social", "Drive safe this weekend.", social("Drive safe this weekend."), None),
    (
        "R-15",
        "BLOCK",
        "social",
        social("Call Smith & Jones Law Group today."),
        social("Call Rivera & Cole Injury Lawyers today."),
        None,
    ),
    (
        "R-15",
        "FLAG",
        "social",
        social("We work with legal aid groups."),
        social("We work with community groups."),
        None,
    ),
    (
        "R-16",
        "BLOCK",
        "social",
        social("Dana Rivera is a certified specialist."),
        social("Dana Rivera is a partner."),
        None,
    ),
    (
        "R-17",
        "FLAG",
        "social",
        social("Our lawyers are experts in truck crashes."),
        social("Our lawyers handle truck crash cases."),
        None,
    ),
    (
        "R-18",
        "BLOCK",
        "social",
        social("Attorney John Smith can help."),
        social("Attorney Dana Rivera can help."),
        None,
    ),
    (
        "R-18",
        "BLOCK",
        "social",
        social("Attorney Lucia Ortega can help."),
        social("Our legal assistant Lucia Ortega can help."),
        None,
    ),
    (
        "R-19",
        "BLOCK",
        "social",
        social("Image: our client recovering at home"),
        social(f"Image: our client recovering at home\n{D['dramatization']['en']}"),
        None,
    ),
    (
        "R-19",
        "BLOCK",
        "social",
        social("Image: AI-generated photo of our attorney at a desk"),
        social("Image: the firm's front door"),
        None,
    ),
    (
        "R-20",
        "FLAG",
        "social",
        social("Photo: a wrecked car on the freeway"),
        social("Photo: a quiet street at dusk"),
        None,
    ),
    (
        "R-20",
        "BLOCK",
        "social",
        social("Photo: a graphic close up of the scene"),
        social("Photo: a quiet street at dusk"),
        None,
    ),
    ("R-21", "BLOCK", "review-reply", "We would be glad to take your case.", "Thank you for the kind words.", None),
    (
        "R-22",
        "BLOCK",
        "social",
        social("Leave us a review and get a $25 gift card."),
        social("We read every review."),
        None,
    ),
    ("R-23", "FLAG", "social", social("Ask your chiropractor about us."), social("Ask your neighbors about us."), None),
    (
        "R-24",
        "BLOCK",
        "social",
        social("Our client Maria Lopez is home now."),
        social("A family we know is home now."),
        None,
    ),
    (
        "R-24",
        "BLOCK",
        "note",
        "Here is the post about case no. 24CV01234.",
        "Here is the post about the safety class.",
        None,
    ),
    (
        "R-25",
        "FLAG",
        "social",
        social("We are currently litigating a case against a trucking company."),
        social("Trucking crashes raise hard questions."),
        None,
    ),
    (
        "R-25",
        "BLOCK",
        "social",
        social("We are currently litigating against a trucking company that lied."),
        social("Trucking crashes raise hard questions."),
        None,
    ),
    (
        "R-26",
        "FLAG",
        "social",
        social("Insurers like Acme Mutual lowball injured people."),
        social("Insurers often offer less than a claim is worth."),
        None,
    ),
    (
        "R-27",
        "BLOCK",
        "review-reply",
        "Thank you for trusting us with your case.",
        "Thank you for the kind words.",
        None,
    ),
    ("R-28", "FLAG", "review-reply", "This reviewer is not a client of the firm.", "Thank you for writing.", None),
    (
        "R-29",
        "BLOCK",
        "review-reply",
        "We returned your calls within a day and our fees were fair.",
        "Thank you for writing.",
        None,
    ),
    (
        "R-30",
        "FLAG",
        "review-reply",
        "We are sorry you are unhappy. The traffic was bad that day.",
        "We are sorry you are unhappy. Professional obligations do not allow us to respond as we would wish. Please contact Dana Rivera at (951) 555-0142 so that we can discuss your concerns.",
        None,
    ),
    ("R-31", "BLOCK", "review-reply", "This is a fake review.", "Thank you for writing.", None),
    (
        "R-32",
        "FLAG",
        "review-reply",
        "We are sorry to hear this. We disagree with this description but are not at liberty to discuss confidential matters.",
        "We are sorry to hear this. Professional obligations do not allow us to respond as we would wish.",
        None,
    ),
    ("R-34", "BLOCK", "review-reply", "Thanks. Visit www.example.com for more.", "Thanks for writing.", None),
    (
        "R-35",
        "BLOCK",
        "review-reply",
        "Please contact Dana Rivera at (951) 555-9999.",
        "Please contact Dana Rivera at (951) 555-0142.",
        None,
    ),
    ("R-35", "BLOCK", "review-reply", "We hope the surgery went well.", "We hope you are well.", None),
    ("R-36", "BLOCK", "review-reply", "I highly recommend this firm.", "Thank you for writing.", None),
    ("R-37", "FLAG", "gbp", "Call (951) 555-0142 for directions.", "Use the call button for directions.", None),
    ("R-39", "BLOCK", "social", social("Ask about pre-settlement funding."), social("Ask about your rights."), None),
    (
        "R-40",
        "BLOCK",
        "blog",
        blog("You asked whether to see a doctor. Exceptions exist."),
        blog("Many people ask whether to see a doctor."),
        None,
    ),
    (
        "R-40",
        "FLAG",
        "blog",
        blog("You should sign the release."),
        blog("Many people wonder whether they should sign a release."),
        None,
    ),
    (
        "R-41",
        "BLOCK",
        "blog",
        f"A title\n\nSee a doctor after a crash.\n\n{IDENT}",
        blog("See a doctor after a crash."),
        None,
    ),
    (
        "R-42",
        "FLAG",
        "blog",
        blog("You have two years to file."),
        blog("You often have two years to file, but exceptions exist."),
        None,
    ),
    ("R-46", "BLOCK", "social", social("Somos notarios para su caso."), social("Somos abogados para su caso."), None),
    ("BLOCKLIST", "BLOCK", "social", social("We always get results."), social("We work for every client."), None),
]


@pytest.mark.parametrize(
    "rule,severity,channel,bad,good,registers",
    CASES,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)],
)
def test_each_rule_fails_on_its_item_and_passes_once_fixed(rule, severity, channel, bad, good, registers) -> None:
    bad_findings = run(bad, channel, registers)
    assert has(bad_findings, rule, severity), (rule, bad_findings)
    good_findings = run(good, channel, registers)
    assert not has(good_findings, rule), (rule, good_findings)


def test_every_finding_quotes_its_span() -> None:
    findings = run(social("We guarantee a quick settlement."))
    assert {f.span for f in findings if f.rule in ("R-01", "R-02")} == {"guarantee", "quick settlement"}


def test_a_registered_result_with_its_facts_is_a_flag_not_a_block() -> None:
    text = social(
        "Our client recovered $1.2 million after a commercial truck driver ran a red light and "
        "left them with a fractured spine."
    )
    findings = run(text)
    assert has(findings, "R-03", "FLAG") and has(findings, "R-04", "FLAG")
    assert gate.verdict(findings) == "pass"


def test_a_result_without_consent_blocks() -> None:
    r = regs()
    r["results"][0]["consent"] = False
    findings = run(social(f"Our client recovered $1.2 million. {D['results']['en']}"), registers=r)
    assert has(findings, "R-24", "BLOCK")


def test_a_sourced_superlative_is_a_flag() -> None:
    text = social("We are rated 4.9 on Google with 312 reviews as of March 2026, the highest-rated firm we know.")
    findings = run(text)
    assert has(findings, "R-09", "FLAG") and not has(findings, "R-09", "BLOCK")


def test_a_superlative_not_about_the_firm_is_not_a_claim() -> None:
    findings = run(social("The best thing to do after a crash is to see a doctor."))
    assert not has(findings, "R-09")


def test_no_fee_is_refused_when_the_firm_has_not_allowed_it() -> None:
    r = regs()
    r["fee_terms"]["no_fee_phrases_allowed"] = False
    findings = run(social(f"No fee unless we recover for you. {D['costs']['en']}"), registers=r)
    assert has(findings, "R-12", "BLOCK")


def test_a_current_certification_naming_its_body_passes() -> None:
    r = regs(
        certifications=[
            {
                "lawyer": "Dana Rivera",
                "field": "Appellate Law",
                "body": "State Bar of California Board of Legal Specialization",
                "expires": "2099-01-01",
            }
        ]
    )
    text = social(
        "Dana Rivera is a certified specialist in appellate law, certified by the State Bar of "
        "California Board of Legal Specialization."
    )
    assert not has(run(text, registers=r), "R-16")
    r["certifications"][0]["expires"] = "2001-01-01"
    assert has(run(text, registers=r), "R-16", "BLOCK")


def test_identity_carried_by_the_channel_template_satisfies_r14() -> None:
    r = regs(channels={"social": {"identity_in_template": True}})
    assert not has(run("Drive safe this weekend.", registers=r), "R-14")
    assert not has(run("The office is open late on Fridays.", "gbp"), "R-14")


def test_review_reply_rules_do_not_run_on_other_channels() -> None:
    assert not has(run(social("Thank you for trusting us with your case.")), "R-27")
    assert has(run("Thank you for trusting us with your case.", "review-reply"), "R-27")


def test_gbp_and_blog_rules_are_channel_scoped() -> None:
    assert not has(run(social("Call (951) 555-0142 today.")), "R-37")
    assert not has(run(social("See a doctor.")), "R-41")


def test_disclosure_text_never_trips_a_pattern_rule() -> None:
    # The testimonial sentence contains "guarantee"; it is the firm's own words.
    text = social(f'A client wrote: "They returned every call and explained every step." {D["testimonial"]["en"]}')
    assert not has(run(text), "R-01")


# ---------------------------------------------------------------------------
# Language: disclosures in the item's own language (R-45), Spanish patterns
# ---------------------------------------------------------------------------

ES_ITEM = (
    "Si usted tuvo un accidente, llame a nuestro equipo. Usted no paga a menos que haya recuperación para su caso."
)


def test_spanish_no_fee_needs_the_spanish_costs_sentence() -> None:
    english_only = run(social(f"{ES_ITEM} {D['costs']['en']}"))
    assert has(english_only, "R-45", "BLOCK"), english_only
    fixed = run(social(f"{ES_ITEM} {D['costs']['es']}"))
    assert not has(fixed, "R-45") and not has(fixed, "R-12"), fixed


def test_spanish_blog_needs_the_spanish_disclaimer() -> None:
    body = "Después de un accidente, usted debe ver a un médico y llamar a la policía en el lugar."
    english = blog(body)
    assert has(run(english, "blog"), "R-45", "BLOCK")
    spanish = blog(body, D["blog"]["es"])
    assert not has(run(spanish, "blog"), "R-45") and not has(run(spanish, "blog"), "R-41")


def test_a_spanish_result_needs_the_spanish_results_sentence() -> None:
    text = social("Nuestro cliente recibió $1.2 million de la aseguradora en su caso.")
    assert has(run(text), "R-45", "BLOCK")
    assert not has(run(text + " " + D["results"]["es"]), "R-45")


def test_spanish_language_claim_needs_a_spanish_speaking_lawyer_or_a_titled_speaker() -> None:
    assert not has(run(social("Hablamos español.")), "R-44")
    r = regs()
    r["roster"][0]["languages"] = ["en"]
    assert has(run(social("Hablamos español."), registers=r), "R-44", "BLOCK")
    titled = social("Hablamos español. Nuestra Asistente legal, Lucia Ortega, habla español.")
    assert not has(run(titled, registers=r), "R-44")


def test_lang_override_requires_that_language() -> None:
    text = social(f"Our client recovered $1.2 million. {D['results']['en']}")
    assert not has(run(text, lang="en"), "R-05")
    assert has(run(text, lang="es"), "R-45", "BLOCK")


# ---------------------------------------------------------------------------
# Fail closed: empty and malformed registers
# ---------------------------------------------------------------------------


def test_empty_registers_block_every_record_claim() -> None:
    empty = {"schema": 1, "firm": {"names": [], "office_cities": []}}
    for text in (
        social(f"Our client recovered $1.2 million. {D['results']['en']}"),
        social("Over 25 years of experience."),
        social("Dana Rivera received the Riverside County Trial Lawyer of the Year award."),
        social("Attorney Dana Rivera can help."),
        social("Hablamos español."),
        social("Our fee is 33% of the recovery."),
    ):
        assert gate.verdict(run(text, registers=empty)) == "block", text


def test_a_null_disclosure_blocks_with_the_firm_has_not_authored_it() -> None:
    r = regs()
    r["disclosures"]["costs"] = None
    findings = run(social("No fee unless we recover for you."), registers=r)
    assert has(findings, "R-12", "BLOCK")
    assert any("has not authored the case-costs sentence yet" in f.message for f in findings)


@pytest.mark.parametrize(
    "broken",
    [
        None,
        "not a mapping",
        {"schema": 2, "firm": {}},
        {"schema": 1},
        {"schema": 1, "firm": {"names": "Rivera"}},
        {"schema": 1, "firm": {}, "roster": [{"name": "X", "lawyer": "yes"}]},
        {"schema": 1, "firm": {}, "disclosures": {"results": "a sentence"}},
        {"schema": 1, "firm": {}, "disclosures": {"surprise": None}},
        {"schema": 1, "firm": {}, "results": [{"id": "a", "text_match": ["$1"]}]},
        {"schema": 1, "firm": {}, "channels": {"tiktok": {"identity_in_template": True}}},
        {"schema": 1, "firm": {}, "fee_terms": {"percentages": ["a third"]}},
    ],
)
def test_malformed_registers_fail_closed(broken) -> None:
    findings = gate.check(CLEAN["note"], "note", broken)
    assert [f.rule for f in findings] == ["REGISTERS"]
    assert findings[0].severity == gate.BLOCK
    assert findings[0].message.startswith("registers unavailable")


def test_unknown_channel_blocks() -> None:
    assert gate.verdict(gate.check("hello", "tiktok", REGS)) == "block"


# ---------------------------------------------------------------------------
# Draft blocks inside one email
# ---------------------------------------------------------------------------


def test_cover_note_is_checked_with_general_rules_only() -> None:
    email = (
        "Hi, here is the reply for the review that mentions your case, and a social post.\n\n"
        "--- DRAFT social ---\n"
        f"{CLEAN['social']}\n"
        "--- END DRAFT ---\n\n"
        "--- DRAFT review-reply ---\n"
        f"{CLEAN['review-reply']}\n"
        "--- END DRAFT ---\n"
    )
    findings = gate.check_document(email, "note", REGS)
    assert gate.verdict(findings) == "pass", findings


def test_each_draft_block_is_checked_on_its_own_channel() -> None:
    email = (
        "Two drafts below.\n"
        "--- DRAFT social ---\nDrive safe this weekend.\n--- END DRAFT ---\n"
        "--- DRAFT review-reply ---\nThank you for trusting us with your case.\n--- END DRAFT ---\n"
    )
    findings = gate.check_document(email, "note", REGS)
    assert has(findings, "R-14", "BLOCK") and has(findings, "R-27", "BLOCK")
    assert any(f.message.startswith("In the review-reply draft") for f in findings)


def test_a_disclosure_in_the_cover_does_not_cure_a_draft() -> None:
    email = (
        f"{D['costs']['en']}\n--- DRAFT social ---\n{social('No fee unless we recover for you.')}\n--- END DRAFT ---\n"
    )
    assert has(gate.check_document(email, "note", REGS), "R-12", "BLOCK")


def test_cover_note_still_meets_the_general_rules() -> None:
    email = "We guarantee these will win.\n--- DRAFT social ---\n" + CLEAN["social"] + "\n--- END DRAFT ---\n"
    assert has(gate.check_document(email, "note", REGS), "R-01", "BLOCK")


@pytest.mark.parametrize(
    "email",
    [
        "--- DRAFT tiktok ---\nhello\n--- END DRAFT ---\n",
        "--- DRAFT social ---\nhello\n",
        "hello\n--- END DRAFT ---\n",
        "--- DRAFT social ---\n--- DRAFT blog ---\nhello\n--- END DRAFT ---\n",
        "--- DRAFT note ---\nhello\n--- END DRAFT ---\n",
    ],
)
def test_malformed_markers_block(email: str) -> None:
    assert has(gate.check_document(email, "note", REGS), "MARKERS", "BLOCK")


def test_text_without_markers_is_one_item_on_the_callers_channel() -> None:
    assert gate.check_document(CLEAN["social"], "social", REGS) == gate.check(CLEAN["social"], "social", REGS)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = gate.main(list(argv))
    return code, out.getvalue()


def _item(tmp: Path, text: str) -> str:
    path = tmp / "item.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_cli_pass_block_and_json_shape() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        code, out = _cli(
            "--item", _item(tmp, CLEAN["social"]), "--channel", "social", "--registers", str(EXAMPLE), "--json"
        )
        assert code == 0
        assert json.loads(out) == {"verdict": "pass", "findings": []}
        code, out = _cli(
            "--item",
            _item(tmp, social("We guarantee it.")),
            "--channel",
            "social",
            "--registers",
            str(EXAMPLE),
            "--json",
        )
        assert code == 1
        doc = json.loads(out)
        assert doc["verdict"] == "block"
        assert set(doc["findings"][0]) == {"rule", "severity", "span", "message"}
        code, out = _cli(
            "--item", _item(tmp, social("We guarantee it.")), "--channel", "social", "--registers", str(EXAMPLE)
        )
        assert code == 1 and "R-01" in out


def test_cli_reads_json_registers() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        reg = tmp / "r.json"
        reg.write_text(json.dumps(REGS), encoding="utf-8")
        code, _ = _cli("--item", _item(tmp, CLEAN["social"]), "--channel", "social", "--registers", str(reg))
        assert code == 0


def test_cli_missing_or_unparseable_registers_fail_closed_with_exit_1() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        bad = tmp / "bad.yaml"
        bad.write_text("schema: [unclosed", encoding="utf-8")
        for registers in (str(tmp / "absent.yaml"), str(bad)):
            code, out = _cli(
                "--item", _item(tmp, CLEAN["social"]), "--channel", "social", "--registers", registers, "--json"
            )
            assert code == 1
            doc = json.loads(out)
            assert doc["verdict"] == "block"
            assert doc["findings"][0]["rule"] == "REGISTERS"


def test_cli_usage_and_item_errors_exit_2() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        assert _cli("--item", str(tmp / "nope.txt"), "--channel", "social", "--registers", str(EXAMPLE))[0] == 2
        assert _cli("--item", _item(tmp, "x"), "--channel", "tiktok", "--registers", str(EXAMPLE))[0] == 2
        assert _cli("--channel", "social")[0] == 2
