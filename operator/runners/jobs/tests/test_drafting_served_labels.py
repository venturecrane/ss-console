"""A request label is a label only when its number ends it (D7, 2026-11-26).

A $0 census replay of a real served special interrogatories set (35 requests)
stopped ``served_unnumbered``: requests cross-reference earlier ones ("state all
facts that support your response to Special Interrogatory No. 3 for which YOU
contend"), the pleading's wrap put "Special Interrogatory No. 3 for which" at a
line start, and the slicer and the numbering both read it as request 3 again
(1, 2, 3, 4, 3, 5, 3...). Synthetic text only.
"""

from __future__ import annotations

import pytest

from jobs.drafting import served
from jobs.drafting.served_slice import slice_requests

_HEAD = "PROPOUNDING PARTY: Plaintiff\nRESPONDING PARTY: Defendant\nSET NO.: One\n\n"


def _set(*requests: str) -> dict[int, str]:
    return {1: _HEAD + "\n\n".join(requests) + "\n\nDated: today\n"}


def test_a_wrapped_cross_reference_at_a_line_start_stays_inside_its_request():
    pages = _set(
        "SPECIAL INTERROGATORY NO. 1:\nState your name.",
        "SPECIAL INTERROGATORY NO. 2:\nState your address.",
        "SPECIAL INTERROGATORY NO. 3:\nState each defense YOU contend applies.",
        "SPECIAL INTERROGATORY NO. 4:\nState all facts that support your response to\n"
        "Special Interrogatory No. 3 for which YOU contend the defense applies.",
        "SPECIAL INTERROGATORY NO. 5:\nIdentify each witness to the facts in your response to\n"
        "Special Interrogatory No. 4, and their address.",
    )
    text, style = slice_requests(pages, "special_interrogatories")
    assert style == "labelled"
    assert served.numbering(text) == [1, 2, 3, 4, 5]
    by_number = served.numbered(text)
    assert by_number[4] == (
        "State all facts that support your response to Special Interrogatory No. 3 "
        "for which YOU contend the defense applies."
    )
    assert by_number[5].endswith("Special Interrogatory No. 4, and their address.")


def test_a_title_case_cross_reference_first_in_the_body_never_starts_the_slice():
    pages = _set(
        "Special Interrogatory No. 9 for which the caption is wrong",
        "Special Interrogatory No. 1:\nState your name.",
        "Special Interrogatory No. 2: State your address.",
    )
    text, style = slice_requests(pages, "special_interrogatories")
    assert style == "labelled"
    assert text.startswith("Special Interrogatory No. 1:")
    assert served.numbering(text) == [1, 2]


@pytest.mark.parametrize(
    "line",
    [
        "SPECIAL INTERROGATORY NO. 5:",
        "SPECIAL INTERROGATORY NO. 5: State your name.",
        "REQUEST FOR PRODUCTION NO. 5.",
        "REQUEST FOR PRODUCTION NO. 5. All DOCUMENTS.",
        "REQUEST FOR ADMISSION NO. 5)",
        "SPECIAL INTERROGATORY NO. 5",
        "SPECIAL INTERROGATORY NO. 5   ",
        "**SPECIAL INTERROGATORY NO. 5:**",
        "**SPECIAL INTERROGATORY NO. 5**",
        "Special Interrogatory No. 5:",
    ],
)
def test_real_labels_still_label(line):
    assert served.numbering(line + "\nText below.") == [5]
    text, style = slice_requests({1: _HEAD + line + "\nText below.\n"}, "special_interrogatories")
    assert style == "labelled"
    assert text.startswith(line.strip())


def test_a_label_alone_on_its_line_keeps_the_text_below():
    pages = _set("REQUEST FOR PRODUCTION NO. 1\nAll DOCUMENTS.", "REQUEST FOR PRODUCTION NO. 2\nAll photographs.")
    text, style = slice_requests(pages, "requests_for_production")
    assert style == "labelled"
    assert served.numbered(text) == {1: "All DOCUMENTS.", 2: "All photographs."}


@pytest.mark.parametrize(
    "line",
    ["Special Interrogatory No. 3 for which YOU contend", "SPECIAL INTERROGATORY NO. 3, state", "No. 3 and 4"],
)
def test_a_number_followed_by_a_word_or_comma_is_a_cross_reference(line):
    assert served.numbering(line) == []
