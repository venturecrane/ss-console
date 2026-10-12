"""html_canon: one canonical hash for the html the broker sends and the html
Exchange stores (ss#3030 follow-up).

The calibration fixture is the evidence: each case is a body POSTed to Graph and
the body Graph returned for that item from the internal smd-staging mailbox. The
canonical form must make every pair equal (Exchange's rewrite is undone) and
must still move on a one-character content change (the falsifier) -- a canon
that equated everything would pass the first half alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from workspace_broker.html_canon import html_body_sha256, html_canon_v1

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "html-canon-calibration.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.parametrize("name", sorted(CASES))
def test_exchanges_stored_rewrite_canonicalizes_to_what_was_submitted(name: str) -> None:
    case = CASES[name]
    assert case["submitted"] != case["stored"]  # Exchange really did rewrite it
    assert html_canon_v1(case["submitted"]) == html_canon_v1(case["stored"])
    assert html_body_sha256(case["submitted"]) == html_body_sha256(case["stored"])


def test_falsifier_the_one_word_apart_reports_hash_apart_after_storage() -> None:
    sent = CASES["report"]["submitted"]
    stored_other = CASES["report_plus1"]["stored"]
    assert html_body_sha256(sent) != html_body_sha256(stored_other)


def _one_char_mutations(markup: str):
    """Every single-character substitution of a letter or digit, one at a time."""
    for i, ch in enumerate(markup):
        if ch.isalnum():
            yield i, markup[:i] + ("x" if ch != "x" else "y") + markup[i + 1 :]


@pytest.mark.parametrize("name", ["report", "inline_ws", "upper", "table", "unicode", "entities"])
def test_falsifier_every_one_character_content_change_changes_the_hash(name: str) -> None:
    """Every letter or digit inside the stored ``<body>`` -- text, tag, attribute
    name or value, style -- is content: substituting it must move the hash. The
    wrapper Exchange adds around it (html/head/meta/body) and the
    ``data-outlook-*`` attributes it injects are dropped by design, so a change
    there may not."""
    stored = CASES[name]["stored"]
    start = stored.index("<body>") + len("<body>")
    end = stored.rindex("</body>")
    injected = [m.span() for m in re.finditer(r'data-outlook-[a-z-]+="[^"]*"', stored)]
    base = html_body_sha256(stored)
    for offset, mutated in _one_char_mutations(stored):
        if any(a <= offset < b for a, b in injected):
            continue
        if start <= offset < end:
            assert html_body_sha256(mutated) != base, f"{name}: a change at offset {offset} kept the hash"


def test_specific_content_changes_move_the_hash() -> None:
    base = '<p>Pay <a href="https://example.com/a">here</a> by <b>Nov 1</b></p>'
    for changed in (
        base.replace("example.com", "examp1e.com"),  # an href
        base.replace("Nov 1", "Nov 2"),  # text
        base.replace("<b>", "<i>").replace("</b>", "</i>"),  # a tag
        base.replace("Pay ", "Pay x "),  # inserted content
        base.replace("<p>", '<p style="display:none">'),  # an attribute
    ):
        assert html_body_sha256(changed) != html_body_sha256(base)


def test_whitespace_exchange_normalizes_is_not_content() -> None:
    assert html_body_sha256("<p>a  b</p>\n<p> c </p>") == html_body_sha256("<p>a b</p><p>c</p>")
    # <pre> is kept verbatim, by Exchange and so by the canon
    assert html_body_sha256("<pre>a  b</pre>") != html_body_sha256("<pre>a b</pre>")
    # an inline space is content: "a <b>b</b>" renders differently from "a<b>b</b>"
    assert html_body_sha256("<p>a <b>b</b></p>") != html_body_sha256("<p>a<b>b</b></p>")


def test_the_canon_never_emits_markup_it_did_not_parse() -> None:
    assert html_canon_v1("<p>&lt;script&gt;</p>") == "<p>&lt;script&gt;</p>"
