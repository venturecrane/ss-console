"""The answers shape a model returns for one section, validated against the form.
Every name, file and value here is invented."""

from __future__ import annotations

from typing import Any

import pytest

from jobs.drafting.forms import answers

CITE = {"file": "records-a.pdf", "page": 3, "excerpt": "seen in clinic on the stated date"}


def _v(text: str) -> dict[str, Any]:
    return {"text": text, "cites": [CITE]}


def _good() -> dict[str, Any]:
    return {
        "answers": [
            {"number": "6.5", "kind": "none"},
            {
                "number": "6.4",
                "kind": "answered",
                "items": [
                    {"name": "Example Clinic", "parts": {"a": _v("100 Sample Way"), "c": _v("March 2025")}},
                    {"name": "Sample Imaging", "parts": {"b": _v("an MRI")}},
                ],
            },
            {"number": "6.2", "kind": "answered", "items": [{"text": "neck and back", "cites": [CITE]}]},
        ]
    }


def test_parse_section_accepts_the_shape_in_form_order() -> None:
    got = answers.parse_section(_good(), ["6.2", "6.4", "6.5"])
    assert [a.number for a in got] == ["6.2", "6.4", "6.5"]
    assert got[1].items[0].parts["a"].text == "100 Sample Way"
    assert got[0].items[0].parts[""].cites[0].page == 3
    assert got[2].kind == "none" and got[2].items == ()


def _problems(raw: Any, numbers: list[str]) -> list[str]:
    with pytest.raises(answers.AnswerError) as e:
        answers.parse_section(raw, numbers)
    return e.value.problems


def test_only_the_numbers_given() -> None:
    raw = _good()
    assert any("not a number this call was given" in p for p in _problems(raw, ["6.2", "6.4"]))
    assert any("6.7: missing" in p for p in _problems(raw, ["6.2", "6.4", "6.5", "6.7"]))
    raw["answers"].append({"number": "6.5", "kind": "none"})
    assert any("given more than once" in p for p in _problems(raw, ["6.2", "6.4", "6.5"]))


@pytest.mark.parametrize(
    "item,needle",
    [
        ({"name": "X", "parts": {"e": _v("x")}}, "not subparts of this interrogatory"),
        ({"name": "X", "parts": {"a": {"text": "x", "cites": []}}}, "at least one cite"),
        ({"name": "X", "parts": {"a": {"text": "x"}}}, "missing ['cites']"),
        ({"name": "X", "parts": {"a": _v("x")}, "note": "y"}, "keys not in the shape"),
        ({"name": "X", "parts": {"a": _v("line one\nline two")}}, "line break"),
        ({"name": "X", "parts": {"a": _v("**bold**")}}, "markdown mark"),
        ({"name": "X", "parts": {"a": _v("one (b) two")}}, "lettered label"),
        ({"name": "X", "parts": {"a": _v("{{FILL: x}}")}}, "markdown mark"),
        ({"name": "X", "parts": {"a": {"text": "x", "cites": [{**CITE, "page": 0}]}}}, "whole number"),
        ({"name": "X", "parts": {}}, "at least one lettered subpart"),
    ],
)
def test_item_shape_errors(item: dict[str, Any], needle: str) -> None:
    raw = {"answers": [{"number": "6.4", "kind": "answered", "items": [item]}]}
    assert any(needle in p for p in _problems(raw, ["6.4"])), _problems(raw, ["6.4"])


def test_kind_rules() -> None:
    bad_kind = {"answers": [{"number": "6.4", "kind": "maybe"}]}
    assert any("kind must be one of" in p for p in _problems(bad_kind, ["6.4"]))
    fixed_with_items = {"answers": [{"number": "6.4", "kind": "none", "items": []}]}
    assert any("keys not in the shape" in p for p in _problems(fixed_with_items, ["6.4"]))
    empty = {"answers": [{"number": "6.4", "kind": "answered", "items": []}]}
    assert any("at least one item" in p for p in _problems(empty, ["6.4"]))
    unnamed = {"answers": [{"number": "6.4", "kind": "answered", "items": [{"parts": {"a": _v("x")}}] * 2}]}
    assert any("every item needs a name" in p for p in _problems(unnamed, ["6.4"]))
    no_sub_parts = {"answers": [{"number": "6.2", "kind": "answered", "items": [{"parts": {"a": _v("x")}}]}]}
    assert any("keys not in the shape" in p for p in _problems(no_sub_parts, ["6.2"]))


def test_work_units_one_per_section_in_form_order() -> None:
    units = answers.work_units(["9.1", "2.1", "6.4", "2.2", "6.1"])
    assert [(u.section, u.numbers) for u in units] == [
        ("2.0", ("2.1", "2.2")),
        ("6.0", ("6.1", "6.4")),
        ("9.0", ("9.1",)),
    ]
    assert units[1].title == "Physical, Mental, or Emotional Injuries"
