"""Code lays out the response body; deterministic checks hold it to the form.
Every sentence, name and value here is invented for the test."""

from __future__ import annotations

import random
from typing import Any

import pytest

from jobs.drafting.forms import answers, catalog, checks, render_body

POLICY = render_body.policy_from_mapping(
    {
        "sentences": {
            "none": "Test sentence: no.",
            "no_information": "Test sentence: not known after a diligent search.",
            "not_applicable": "Test sentence: does not apply.",
        },
        "per_number": {"17.1": {"not_applicable": "Test sentence: no requests were served."}},
    }
)
CITE = {"file": "f1.pdf", "page": 2, "excerpt": "an invented excerpt"}


def _v(text: str) -> dict[str, Any]:
    return {"text": text, "cites": [CITE]}


def _parse(raw: list[dict[str, Any]], numbers: list[str]) -> list[answers.Answer]:
    return answers.parse_section({"answers": raw}, numbers)


def _six_four() -> list[answers.Answer]:
    items = [
        {"name": f"Provider {i}", "parts": {k: _v(f"value {i}{k}") for k in "abcd" if not (i == 2 and k == "c")}}
        for i in (1, 2, 3)
    ]
    return _parse([{"number": "6.4", "kind": "answered", "items": items}], ["6.4"])


def test_three_providers_render_three_blocks_each_subpart_on_its_own_line() -> None:
    text = render_body.to_plain(render_body.render(["6.4"], _six_four(), POLICY))
    lines = text.splitlines()
    i = lines.index("RESPONSE TO FORM INTERROGATORY NO. 6.4:")
    assert lines[i + 1 :] == [
        "Provider 1", "(a) value 1a", "(b) value 1b", "(c) value 1c", "(d) value 1d",
        "Provider 2", "(a) value 2a", "(b) value 2b", "(c) Test sentence: not known after a diligent search.",
        "(d) value 2d",
        "Provider 3", "(a) value 3a", "(b) value 3b", "(c) value 3c", "(d) value 3d",
    ]  # fmt: skip
    assert checks.check(text, ["6.4"], _six_four(), POLICY) == []


def test_restated_text_is_the_catalogue_text_and_order_is_form_order() -> None:
    cat = catalog.load()
    ans = _parse(
        [
            {"number": "6.7", "kind": "none"},
            {"number": "6.1", "kind": "no_information"},
            {"number": "6.2", "kind": "not_applicable"},
        ],
        ["6.7", "6.1", "6.2"],
    )
    blocks = render_body.render(["6.7", "6.2", "6.1"], list(reversed(ans)), POLICY)
    assert [b.number for b in blocks if b.role == "label"] == ["6.1", "6.2", "6.7"]
    for n in ("6.1", "6.2", "6.7"):
        restated = " ".join(b.text for b in blocks if b.number == n and b.role in ("stem", "subpart"))
        assert restated == cat.get(n).text
    assert "6.3" not in render_body.to_plain(blocks)
    assert [b.text for b in blocks if b.role == "fixed"] == [
        "Test sentence: not known after a diligent search.", "Test sentence: does not apply.", "Test sentence: no."
    ]  # fmt: skip


def test_per_number_sentence_wins() -> None:
    ans = _parse([{"number": "17.1", "kind": "not_applicable"}], ["17.1"])
    assert render_body.render(["17.1"], ans, POLICY)[-1].text == "Test sentence: no requests were served."


def test_unchecked_answer_and_unanswered_checked_number_refused() -> None:
    with pytest.raises(ValueError, match="unchecked"):
        render_body.render(["6.1"], _six_four(), POLICY)


def test_markdown_parses_one_paragraph_per_block_with_styled_labels() -> None:
    from smokeball_connector import docgrammar
    from smokeball_connector.docx_classes import _DISCOVERY_LABEL

    blocks = render_body.render(["6.4"], _six_four(), POLICY)
    parsed = docgrammar.parse_document(render_body.to_markdown(blocks))
    assert [type(p).__name__ for p in parsed] == ["Paragraph"] * len(blocks)
    assert [p.text for p in parsed] == [b.text for b in blocks]  # type: ignore[union-attr]
    labels = [p.text for p in parsed if _DISCOVERY_LABEL.match(p.text)]  # type: ignore[union-attr]
    assert labels == ["FORM INTERROGATORY NO. 6.4:", "RESPONSE TO FORM INTERROGATORY NO. 6.4:"]


def test_policy_errors() -> None:
    with pytest.raises(render_body.PolicyError):
        render_body.policy_from_mapping({"sentences": {"none": "x"}})
    with pytest.raises(render_body.PolicyError):
        render_body.policy_from_mapping({**_policy_raw(), "label": "NO {x}"})
    with pytest.raises(render_body.PolicyError):
        render_body.policy_from_mapping({**_policy_raw(), "extra": 1})


def _policy_raw() -> dict[str, Any]:
    return {"sentences": {"none": "a", "no_information": "b", "not_applicable": "c"}}


# ---- property: random checked lists and answers render clean --------------------------------


def _random_answers(rng: random.Random, checked: list[str]) -> list[answers.Answer]:
    cat = catalog.load()
    raw: list[dict[str, Any]] = []
    for n in checked:
        kind = rng.choice(["answered", "answered", "none", "no_information", "not_applicable"])
        if kind != "answered":
            raw.append({"number": n, "kind": kind})
            continue
        letters = cat.get(n).letters
        count = rng.randint(1, 3)
        items: list[dict[str, Any]] = []
        for i in range(count):
            name = {"name": f"Item {n} {i}"} if count > 1 or rng.random() < 0.5 else {}
            if letters:
                chosen = [k for k in letters if rng.random() < 0.7] or [letters[0]]
                items.append({**name, "parts": {k: _v(f"v {n} {i} {k}") for k in chosen}})
            else:
                items.append({**name, "text": f"v {n} {i}", "cites": [CITE]})
        raw.append({"number": n, "kind": "answered", "items": items})
    return answers.parse_section({"answers": raw}, checked)


@pytest.mark.parametrize("seed", range(25))
def test_property_render_then_checks_pass(seed: int) -> None:
    rng = random.Random(seed)
    cat = catalog.load()
    checked = sorted(rng.sample(cat.numbers, rng.randint(1, 40)), key=cat.position)
    ans = _random_answers(rng, checked)
    blocks = render_body.render(checked, ans, POLICY)
    assert checks.check(render_body.to_markdown(blocks), checked, ans, POLICY) == []
    assert checks.check(render_body.to_plain(blocks), checked, ans, POLICY) == []


# ---- corrupted bodies: each check fires and names the number ------------------------------


def _body() -> tuple[str, list[str], list[answers.Answer]]:
    checked = ["2.2", "6.1", "6.4"]
    ans = _parse(
        [{"number": "2.2", "kind": "answered", "items": [{"text": "a test date and place", "cites": [CITE]}]},
         {"number": "6.1", "kind": "none"}],
        ["2.2", "6.1"],
    ) + _six_four()  # fmt: skip
    return render_body.to_plain(render_body.render(checked, ans, POLICY)), checked, ans


def _codes(text: str, checked: list[str], ans: list[answers.Answer]) -> set[tuple[str, str]]:
    return {(f.number, f.code) for f in checks.check(text, checked, ans, POLICY)}


def test_clean_body_passes() -> None:
    assert _codes(*_body()) == set()


def test_corruptions() -> None:
    text, checked, ans = _body()
    cat = catalog.load()
    cases = {
        ("6.4", "subparts"): text.replace("(b) value 2b\n", ""),
        ("6.4", "subpart_inline"): text.replace("(a) value 3a\n(b) value 3b", "(a) value 3a (b) value 3b"),
        ("6.4", "uncited_value"): text.replace("(d) value 3d", "(d) a value nobody cited"),
        ("6.4", "item_name"): text.replace("Provider 2", "Provider Two"),
        ("6.4", "item_count"): text.replace("Provider 3\n(a) value 3a\n(b) value 3b\n(c) value 3c\n(d) value 3d\n", ""),
        ("6.1", "fixed_sentence"): text.replace("Test sentence: no.", "No."),
        ("6.1", "restated_text"): text.replace(
            cat.get("6.1").stem, cat.get("6.1").stem.replace("emotional", "emotion")
        ),
        ("2.2", "uncited_value"): text.replace("a test date and place", "another date"),
        ("6.4", "restated_layout"): text.replace("provided;\n(c) the dates", "provided; (c) the dates"),
        ("2.2", "unchecked"): text,  # checked list without 2.2
    }
    for (n, code), body in cases.items():
        chk = [c for c in checked if c != "2.2"] if code == "unchecked" else checked
        assert (n, code) in _codes(body, chk, ans), (n, code)
    assert ("6.5", "missing") in _codes(text, [*checked, "6.5"], ans)


def test_out_of_order_and_duplicate() -> None:
    text, checked, ans = _body()
    head, rest = text.split("FORM INTERROGATORY NO. 6.1:\n", 1)
    six_one, six_four = rest.split("FORM INTERROGATORY NO. 6.4:\n", 1)
    reordered = head + "FORM INTERROGATORY NO. 6.4:\n" + six_four + "FORM INTERROGATORY NO. 6.1:\n" + six_one
    assert any(code == "order" for _, code in _codes(reordered, checked, ans))
    assert ("6.1", "order") in _codes(text + "FORM INTERROGATORY NO. 6.1:\n" + six_one, checked, ans)
