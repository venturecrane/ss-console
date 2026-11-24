"""A served form interrogatory set names its form, revision and checked numbers,
and the review checks a form body against the official form (every value
invented).

The falsifiers:

1. Before this change a form set had no place to say which boxes were
   checked: ``served.json`` refused every key past the numbered-set shape, and
   the review only counted dotted numbers (``request_count`` 4 passed any four
   numbers, a number never checked included).
2. The checked numbers are checked against the catalogue: a count that is not
   theirs, a number the form does not carry, the wrong order, a box that is
   not on the form, a revision the catalogue does not carry.
3. An Operator form set is still refused until the switch is on, and with it
   on, still refused when extra interrogatories are attached.
4. A plan frozen before these keys existed still loads.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from jobs.drafting import outcome, review as review_mod, served, served_cli
from jobs.drafting.forms import answers, catalog, render_body

CHECKED = ["1.1", "2.1", "2.2", "6.4", "17.1"]


def _form_set(by: str = "smd", **over):
    base = {
        "set_type": "form_interrogatories",
        "set_number": 1,
        "propounding_party": "Delta Example Corporation",
        "responding_party": "Gamma Example",
        "request_count": len(CHECKED),
        "request_text": "The served form, as its pages read.",
        "drafted_by": by,
        "form": "DISC-001",
        "form_revision": "Rev. January 1, 2024",
        "checked": list(CHECKED),
        "definition_boxes": ["4(a)(1)"],
        "additions": False,
    }
    return {**base, **over}


def _srog():
    return {
        "set_type": "special_interrogatories",
        "set_number": 1,
        "propounding_party": "Delta Example Corporation",
        "responding_party": "Gamma Example",
        "request_count": 1,
        "request_text": "SPECIAL INTERROGATORY NO. 1: IDENTIFY the driver.",
        "drafted_by": "operator",
    }


def _parse(*sets_, tmp_path, form=False):
    return served.parse(json.dumps({"sets": list(sets_)}).encode(), tmp_path, ["Gamma Example"], form)


# ---- 1. the keys ---------------------------------------------------------------------------


def test_an_smd_form_set_names_its_form_revision_checked_numbers_and_boxes(tmp_path):
    both = _parse(_srog(), _form_set(), tmp_path=tmp_path)
    got = both[1]
    assert (got.form, got.form_revision) == ("DISC-001", "Rev. January 1, 2024")
    assert got.checked == tuple(CHECKED) and got.definition_boxes == ("4(a)(1)",) and got.additions is False
    back = served.parse(served.to_file(both).encode(), tmp_path)  # the CLI's file reads back the same
    assert back == both


def test_an_smd_form_set_without_the_keys_still_reads_as_before(tmp_path):
    raw = {k: v for k, v in _form_set().items() if k not in ("form", "form_revision", "checked", "definition_boxes",
                                                                "additions")}  # fmt: skip
    raw["request_count"] = 62
    got = _parse(_srog(), raw, tmp_path=tmp_path)[1]
    assert got.checked == () and got.form == ""
    assert "checked" not in served.to_file([got])  # a numbered or bare form set's file has no form keys


@pytest.mark.parametrize(
    "over, said",
    [
        ({"request_count": 6}, "5 numbers are checked but the set's count is 6"),
        ({"checked": ["1.1", "2.1", "2.2", "5.1", "17.1"]}, "5.1 is not an interrogatory on DISC-001"),
        ({"checked": ["2.1", "1.1", "2.2", "6.4", "17.1"]}, "not in the form's order"),
        ({"checked": ["1.1", "2.1", "2.1", "6.4", "17.1"]}, "2.1 is listed more than once"),
        ({"checked": "1.1-2.2"}, "checked must be a list"),
        ({"definition_boxes": ["4(a)(3)"]}, "'4(a)(3)' is not a definition box"),
        ({"form_revision": "Rev. January 1, 2008"}, "is not a form and revision the catalogue carries"),
        ({"form": "DISC-002"}, "is not a form and revision the catalogue carries"),
        ({"additions": "no"}, "additions must be true or false"),
    ],
)
def test_the_checked_numbers_and_boxes_are_checked_against_the_catalogue(tmp_path, over, said):
    with pytest.raises(outcome.DraftingFailed, match="served_invalid") as e:
        _parse(_srog(), _form_set(**over), tmp_path=tmp_path)
    assert said in str(e.value)


def test_some_form_keys_without_the_rest_and_form_keys_on_another_set_are_refused(tmp_path):
    part = {k: v for k, v in _form_set().items() if k != "definition_boxes"}
    with pytest.raises(outcome.DraftingFailed, match=r"missing \['definition_boxes'\]"):
        _parse(_srog(), part, tmp_path=tmp_path)
    with pytest.raises(outcome.DraftingFailed, match='"checked" is for a form interrogatory set only'):
        _parse({**_srog(), "checked": ["1.1"]}, tmp_path=tmp_path)


# ---- 3. the Operator's form sets, still refused ---------------------------------------------


def test_an_operator_form_set_is_refused_for_a_firm_without_a_form_policy_however_valid(tmp_path):
    with pytest.raises(outcome.DraftingFailed, match="a form interrogatory set is drafted by SMD for now"):
        _parse(_form_set("operator"), tmp_path=tmp_path)


def test_with_the_firms_policy_an_operator_form_set_reads_and_is_not_numbered_one_to_n(tmp_path):
    got = _parse(_form_set("operator"), tmp_path=tmp_path, form=True)
    assert got[0].drafted_by == "operator" and got[0].checked == tuple(CHECKED)
    with pytest.raises(outcome.DraftingFailed, match="with additions attached is drafted by SMD"):
        _parse(_form_set("operator", additions=True), tmp_path=tmp_path, form=True)
    with pytest.raises(outcome.DraftingFailed, match="not a form and revision the catalogue carries"):
        _parse(_form_set("operator", form_revision="Rev. July 1, 1999"), tmp_path=tmp_path, form=True)
    with pytest.raises(outcome.DraftingFailed, match="names all of"):
        _parse({k: v for k, v in _form_set("operator").items() if k != "additions"}, tmp_path=tmp_path, form=True)
    with pytest.raises(outcome.DraftingFailed, match="is not our client"):
        _parse(_form_set("operator", responding_party="Someone Else"), tmp_path=tmp_path, form=True)


# ---- 4. a frozen plan -----------------------------------------------------------------------


def test_a_plan_frozen_before_the_form_keys_still_loads_and_a_new_one_round_trips(tmp_path):
    old = {"sets": [{k: v for k, v in asdict(_parse(_srog(), tmp_path=tmp_path)[0]).items()
                     if k not in ("form", "form_revision", "checked", "definition_boxes", "additions")}]}  # fmt: skip
    (tmp_path / served.PLAN).write_text(json.dumps(old))
    assert served.frozen(tmp_path, tmp_path)[0].checked == ()
    got = _parse(_srog(), _form_set(), tmp_path=tmp_path)
    (tmp_path / served.PLAN).write_text(json.dumps({"sets": [asdict(s) for s in got]}))
    assert served.frozen(tmp_path, tmp_path) == got


def test_the_review_map_carries_a_form_sets_checked_numbers(tmp_path):
    got = _parse(_srog(), _form_set(), tmp_path=tmp_path)
    plan = [{"key": "set-1", "served": 0}, {"key": "set-2", "served": 1}]
    m = served.review_map(got, plan)
    assert "checked" not in m["set-1"]
    assert m["set-2"]["checked"] == CHECKED and m["set-2"]["form_revision"] == catalog.CURRENT


# ---- the CLI ---------------------------------------------------------------------------------


def test_the_cli_template_and_summary_name_a_form_sets_keys(tmp_path):
    t = served_cli.template()["sets"]
    assert t[1]["set_type"] == "form_interrogatories" and t[1]["form"] == "DISC-001" and "checked" in t[1]
    line = served_cli.summary(_parse(_srog(), _form_set(), tmp_path=tmp_path))[1]
    assert line == (
        "Responses to Form Interrogatories, Set One: DISC-001 [Rev. January 1, 2024], 5 numbers checked "
        "(1.1, 2.1-2.2, 6.4, 17.1), definition boxes 4(a)(1), SMD drafts it"
    )


# ---- 1. the review's form check ---------------------------------------------------------------

POLICY = render_body.policy_from_mapping(
    {"sentences": {"none": "Test: no.", "no_information": "Test: not known.", "not_applicable": "Test: n/a."}}
)
CITE = {"file": "f1.pdf", "page": 2, "excerpt": "an invented excerpt"}


def _body(numbers: list[str]) -> str:
    raw = []
    for n in numbers:
        if n == "6.4":
            items = [{"name": f"Provider {i}", "parts": {k: {"text": f"v{i}{k}", "cites": [CITE]} for k in "abcd"}}
                     for i in (1, 2)]  # fmt: skip
            raw.append({"number": n, "kind": "answered", "items": items})
        else:
            raw.append({"number": n, "kind": "no_information"})
    ans = answers.parse_section({"answers": raw}, numbers)
    return render_body.to_plain(render_body.render(numbers, ans, POLICY))


def _form_map(checked=CHECKED):
    return {"set-1": {"set_type": "form_interrogatories", "count": len(checked), "checked": list(checked),
                      "form": "DISC-001", "form_revision": catalog.CURRENT}}  # fmt: skip


def _paper(text: str) -> review_mod.Paper:
    return review_mod.Paper("set-1", "Responses to Form Interrogatories, Set One", text, "plain")


def test_a_clean_code_rendered_form_body_has_no_finding():
    assert review_mod.response_findings([_paper(_body(CHECKED))], _form_map()) == []


@pytest.mark.parametrize(
    "mutate, said",
    [
        (lambda t: t, "6.5: appears but was not checked"),  # a body with 6.5 answered for a list without it
        (lambda t: t.replace("State the date and place of your birth.", "State your birth date."),
         "2.2: the restated text is not the official text"),
        (lambda t: t.replace("(c) v2c\n", ""), "6.4: item 'Provider 2' does not carry ['a', 'b', 'c', 'd']"),
        (lambda t: t.replace("(b) v1b\n(c) v1c", "(b) v1b (c) v1c"), "6.4: a subpart label inside a line"),
    ],
)  # fmt: skip
def test_an_unchecked_number_reworded_text_or_a_missing_subpart_holds_the_job(mutate, said):
    numbers = [*CHECKED[:4], "6.5", "17.1"] if "6.5" in said else CHECKED
    found = review_mod.response_findings([_paper(mutate(_body(numbers)))], _form_map())
    assert any(said in f.finding for f in found), [f.finding for f in found]
    assert all(f.grade == review_mod.MUST for f in found)


def test_the_same_count_of_the_wrong_numbers_now_holds_though_the_count_alone_passed():
    """FALSIFIER: five dotted numbers, one not checked, passed the count-only check."""
    wrong = ["1.1", "2.1", "2.2", "6.4", "8.1"]
    body = _body(wrong)
    count_only = {"set-1": {"set_type": "form_interrogatories", "count": 5}}
    assert review_mod.response_findings([_paper(body)], count_only) == []  # the old branch, kept for no checked
    found = [f.finding for f in review_mod.response_findings([_paper(body)], _form_map())]
    assert any("8.1: appears but was not checked" in f for f in found)
    assert any("17.1: checked but not answered" in f for f in found)
