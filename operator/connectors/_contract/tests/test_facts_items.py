"""Every finder item lands under exactly one heading, and the helpers keep the overlay's behaviour."""

from __future__ import annotations

import json
from pathlib import Path

from records_contract import facts

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def _render(name: str) -> facts.Facts:
    given = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["input"]
    return facts.render(
        given["results"],
        given["files"],
        staged_matter=given["staged_matter"],
        read_matters=set(given["read_matters"]),
    )


def _headings(f: facts.Facts) -> list[tuple[str, int, str]]:
    return [(i["file_id"], i["index"], i["heading"]) for i in f.items]


def test_each_item_has_one_heading() -> None:
    assert _headings(_render("not_established_unreadable_closest")) == [
        ("f0", 0, facts.HEADING_ATTACHED),
        ("f0", 1, facts.HEADING_NOT_ESTABLISHED),
    ]
    assert _headings(_render("not_found_established")) == [("f1", 0, facts.HEADING_NOT_FOUND)]
    assert _headings(_render("whole_file_and_two_results")) == [
        ("f2", 0, facts.HEADING_ATTACHED),
        ("f3", 0, facts.HEADING_NOT_ESTABLISHED),
    ]


def test_a_found_item_with_nothing_on_the_reply_is_unplaced_not_dropped() -> None:
    f = _render("nothing_to_say")
    assert f.text == "" and f.hold_reason is None
    assert _headings(f) == [("f0", 0, facts.HEADING_UNPLACED)]


def test_matter_id_is_the_finders() -> None:
    assert _render("all_found_attached").matter_id == "5f0c1a2b-0000-4000-8000-00000000abcd"
    assert _render("hold_two_matters").matter_id is None


def test_staged_files_without_a_matter_hold() -> None:
    given = json.loads((FIXTURES / "all_found_attached.json").read_text(encoding="utf-8"))["input"]
    got = facts.render(given["results"], given["files"])
    assert got.hold_reason == "the page finder's results and the staged files are not all from one matter"


def test_insert_missing_unstated_round_trip() -> None:
    f = _render("all_found_attached")
    args = {"text": "Hi Pat,\n\n[[FILE FACTS]]\n\nThanks", "html": "<p>Hi</p>[[FILE FACTS]]"}
    out = facts.insert(args, f)
    assert f.text in out["text"] and f.html in out["html"]
    assert facts.missing(f, out["text"], out["html"]) == ""
    assert facts.missing(f, args["text"], "") == "the facts marker is still in the reply"
    assert facts.unstated(f, "pages 1-4 and 48-49 are attached") == ""
    assert facts.unstated(f, "pp 50 is attached") == "p50"
    assert facts.model_words(args) == "Hi Pat,\n\n \n\nThanks"
    assert facts.without_names(f, "see 8-C Hospital.pdf") == "see the file"


def test_tokens_skip_labels_and_names() -> None:
    assert facts.tokens("Exhibit 4 files and 12 pages, pp. 2,313 to 2,320") == frozenset({"12 page", "p2313-2320"})


def test_known_caps_survive_title_casing() -> None:
    given = json.loads((FIXTURES / "whole_file_and_two_results.json").read_text(encoding="utf-8"))
    results = given["input"]["results"]
    files = [dict(given["input"]["files"][0], pages="1-30")]
    got = facts.render(results, files, staged_matter=given["input"]["staged_matter"])
    assert "Itemized Statement - EXHIBIT B" in got.text
