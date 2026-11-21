"""The form interrogatory answer path: the firm's policy, one call per section,
verified cites, code-filled numbers, markers, and the paper.
Every name, file, figure and sentence here is invented."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from jobs.drafting.evidence.shapes import DocPages
from jobs.drafting.forms import answer_render, answer_stage, catalog, policy

NO_INFO = "Responding party does not know at this time and will supplement this response."
MARK = "{{CLIENT: confirm with the client}}"

POLICY: dict[str, Any] = {
    "sentences": {"none": "No.", "no_information": NO_INFO, "not_applicable": "Not applicable."},
    "per_number": {},
    "responding_role": "plaintiff",
    "not_applicable_numbers": {"plaintiff": ["2.9"]},
    "client_confirm_numbers": ["2.3"],
    "no_admissions_17_1": "No requests for admission were served with these interrogatories.",
    "preamble": ["Responding party answers the checked interrogatories as follows."],
    "closing": ["Responding party reserves the right to supplement these responses."],
    "label": "FORM INTERROGATORY NO. {n}:",
    "response_label": "RESPONSE TO FORM INTERROGATORY NO. {n}:",
    "client_marker": "confirm with the client",
    "skeleton": "drafting/form-shell.md",
    "rules": {"apply": ["r-plain"], "not_for_form_sets": ["r-compilation"]},
}

PAGES = {
    1: "Example Clinic, 100 Sample Way, saw the patient for a neck exam on March 3, 2025.",
    2: "Sample Imaging, 22 Test Road, took an MRI of the neck on April 9, 2025. Charges $1,250.00.",
    3: "Demo Physical Therapy, 5 Placeholder Lane, gave twelve therapy visits in May 2025.",
    4: "The patient was born on June 1, 1980 in Exampletown.",
}
CORPUS = {"records-a.pdf": DocPages("records-a.pdf", PAGES)}


class Door:
    """A fake doorway: fixed replies in order, every call recorded."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def call(self, stage: str, **kw: Any) -> Any:
        self.calls.append({"stage": stage, **kw})
        reply = self.replies.pop(0)

        class R:
            text = reply if isinstance(reply, str) else json.dumps(reply)

        return R()


def _cite(page: int, excerpt: str) -> dict[str, Any]:
    return {"file": "records-a.pdf", "page": page, "excerpt": excerpt}


def _v(text: str, page: int, excerpt: str) -> dict[str, Any]:
    return {"text": text, "cites": [_cite(page, excerpt)]}


def _pol(**over: Any) -> policy.ResponsePolicy:
    raw = copy.deepcopy(POLICY)
    raw.update(over)
    return policy.load_policy({policy.KEY: raw})


def _inp(checked: tuple[str, ...], admissions: bool = False) -> answer_stage.Inputs:
    return answer_stage.Inputs(
        checked=checked,
        responding_party="Pat Example",
        admissions_served=admissions,
        matter_record=("- client: Pat Example",),
        medicals=[{"provider": "Example Clinic"}],
        rules_block="",
    )


def _run(checked: tuple[str, ...], door: Door, admissions: bool = False, **over: Any) -> tuple[str, Any]:
    cat = catalog.load()
    pol = _pol(**over)
    inp = _inp(checked, admissions)
    res = answer_stage.answer(inp, pol, CORPUS, door, "model-x", cat)
    return answer_render.body(res.answers, checked, pol, answer_stage.effective_form(pol, inp), cat), res


def _lines(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if ln.strip()]


def test_policy_section_is_required_and_closed() -> None:
    assert policy.validate(POLICY) == []
    with pytest.raises(policy.PolicyMissing, match="not authored"):
        policy.load_policy({})
    bad = {k: v for k, v in POLICY.items() if k != "no_admissions_17_1"}
    assert any("no_admissions_17_1: required" in p for p in policy.validate(bad))
    bad = {**POLICY, "client_confirm_numbers": ["99.9"], "extra": 1}
    problems = policy.validate(bad)
    assert any("extra: unknown key" in p for p in problems)
    bad = {**POLICY, "rules": {"apply": ["r1"], "not_for_form_sets": ["r1"]}}
    assert any("both to apply and not to" in p for p in policy.validate(bad))
    bad = {**POLICY, "sentences": {"none": "No."}}
    assert policy.validate(bad)


def test_six_four_three_providers_each_subpart_on_its_own_line() -> None:
    reply = {
        "answers": [
            {
                "number": "6.4",
                "kind": "answered",
                "items": [
                    {
                        "name": "Example Clinic",
                        "parts": {
                            "a": _v("Example Clinic, 100 Sample Way", 1, "Example Clinic, 100 Sample Way"),
                            "b": _v("a neck exam", 1, "for a neck exam"),
                            "c": _v("March 3, 2025", 1, "on March 3, 2025"),
                        },
                    },
                    {
                        "name": "Sample Imaging",
                        "parts": {
                            "a": _v("Sample Imaging, 22 Test Road", 2, "Sample Imaging, 22 Test Road"),
                            "b": _v("an MRI of the neck", 2, "took an MRI of the neck"),
                            "c": _v("April 9, 2025", 2, "on April 9, 2025"),
                            "d": _v("$1,250.00", 2, "Charges $1,250.00"),
                        },
                    },
                    {
                        "name": "Demo Physical Therapy",
                        "parts": {
                            "a": _v(
                                "Demo Physical Therapy, 5 Placeholder Lane",
                                3,
                                "Demo Physical Therapy, 5 Placeholder Lane",
                            ),
                            "b": _v("twelve therapy visits", 3, "gave twelve therapy visits"),
                            "c": _v("May 2025", 3, "in May 2025"),
                        },
                    },
                ],
            }
        ]
    }
    door = Door(reply)
    md, res = _run(("6.4",), door)
    assert res.calls == 1 and len(door.calls) == 1
    lines = _lines(md)
    for name in ("Example Clinic", "Sample Imaging", "Demo Physical Therapy"):
        i = lines.index(name)
        assert [ln[:3] for ln in lines[i + 1 : i + 5]] == ["(a)", "(b)", "(c)", "(d)"]
    assert not any(" (b) " in ln for ln in lines)  # never run together
    assert ("6.4", "d", "records-a.pdf", 2) in res.cites
    assert "records-a.pdf" not in md  # page cites go to the attorney notes, never the served paper
    assert "| 6.4 | (d) | records-a.pdf | 2 |" in answer_render.cite_table("Responses", res.cites)


def test_a_failed_cite_gets_one_scoped_retry_then_is_no_information_with_a_marker() -> None:
    bad = {"answers": [{"number": "2.2", "kind": "answered", "items": [_v("June 1, 1980", 4, "born in March")]}]}
    door = Door(bad, bad)
    md, res = _run(("2.2",), door)
    assert len(door.calls) == 2 and res.failed_values == 1
    assert "2.2" in door.calls[1]["messages"][0]["content"] and "ASKED AGAIN" in door.calls[1]["messages"][0]["content"]
    assert f"{NO_INFO} {MARK}" in _lines(md)
    assert "June 1, 1980" not in md


def test_a_retry_that_verifies_is_kept_and_only_the_failed_number_is_asked_again() -> None:
    first = {
        "answers": [
            {"number": "2.2", "kind": "answered", "items": [_v("June 1, 1980, Exampletown", 4, "born in March")]},
            {"number": "2.3", "kind": "none"},
        ]
    }
    again = {
        "answers": [
            {
                "number": "2.2",
                "kind": "answered",
                "items": [
                    {
                        "text": "June 1, 1980, Exampletown",
                        "cites": [_cite(4, "born on June 1, 1980"), _cite(1, "Example Clinic")],
                    }
                ],
            }
        ]
    }
    door = Door(first, again)
    md, res = _run(("2.2", "2.3"), door)
    assert res.failed_values == 0
    assert (
        "SECTION" in door.calls[1]["messages"][0]["content"] and "2.3 [" not in door.calls[1]["messages"][0]["content"]
    )
    assert "June 1, 1980, Exampletown" in _lines(md)  # two pages: no single-page marker
    assert f"No. {MARK}" in _lines(md)  # 2.3 is a number the policy always confirms


def test_a_dollar_figure_no_excerpt_prints_is_never_stated() -> None:
    reply = {
        "answers": [{"number": "2.2", "kind": "answered", "items": [_v("$2,500.00 total", 2, "Charges $1,250.00")]}]
    }
    door = Door(reply, reply)
    md, res = _run(("2.2",), door)
    assert res.failed_values == 1 and "$2,500.00" not in md


def test_a_fixed_not_applicable_number_makes_no_model_call() -> None:
    door = Door()
    md, res = _run(("2.9",), door)
    assert door.calls == [] and res.calls == 0
    assert "Not applicable." in _lines(md)


def test_17_1_is_the_policy_sentence_or_an_attorney_marker_by_code() -> None:
    door = Door()
    md, _ = _run(("17.1",), door)
    assert door.calls == []
    assert "No requests for admission were served with these interrogatories." in _lines(md)
    md, _ = _run(("17.1",), Door(), admissions=True)
    assert answer_stage.ADMISSIONS_MARKER in md


def test_markers_land_on_every_no_information_value() -> None:
    reply = {
        "answers": [
            {
                "number": "6.4",
                "kind": "answered",
                "items": [
                    {
                        "name": "Example Clinic",
                        "parts": {"a": _v("Example Clinic, 100 Sample Way", 1, "Example Clinic, 100 Sample Way")},
                    }
                ],
            },
            {"number": "6.5", "kind": "no_information"},
        ]
    }
    md, _ = _run(("6.4", "6.5"), Door(reply))
    lines = _lines(md)
    for k in "bcd":
        assert f"({k}) {NO_INFO} {MARK}" in lines
    assert f"{NO_INFO} {MARK}" in lines  # 6.5, the whole number
    assert f"(a) Example Clinic, 100 Sample Way {MARK}" in lines  # one page is its only support


def test_model_text_cannot_carry_a_marker() -> None:
    reply = {
        "answers": [{"number": "2.2", "kind": "answered", "items": [_v("{{CLIENT: x}} June", 4, "born on June 1")]}]
    }
    with pytest.raises(Exception, match="not in the shape"):
        _run(("2.2",), Door(reply, reply))


def test_paper_wraps_the_body_in_the_firms_shell() -> None:
    shell = "# {{FILL: CAPTION case_number | THE CAPTION}}\n\n<!-- guidance -->\n{{FORM RESPONSES}}\n\nSignature\n"
    cap = {"fields": {"case_number": {"value": "CV-0000"}}}
    out = answer_render.paper(shell, cap, _pol(), "BODY\n")
    assert out.startswith("# CV-0000")
    assert "guidance" not in out
    assert (
        out.index("Responding party answers")
        < out.index("BODY")
        < out.index("reserves the right")
        < out.index("Signature")
    )
    with pytest.raises(ValueError):
        answer_render.paper("no slot", cap, _pol(), "BODY")
