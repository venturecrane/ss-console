"""A continuation page whose note heading is on a page the update does not
compose (2026-10-14, builder L's finding on a re-printed hospital record).

An EMR note ran over three pages; its heading page (author, date of service)
was already read in a delivered exhibit, and only the last page was composed,
with one context block: the foot of the page before, which held no heading.
Compose took the page banner and credited a nurse practitioner's pre-op note
to the surgeon on the admission date. The composed page now carries the
note's heading; when none is found it says so and the provider is flagged.
Synthetic text only.
"""

from __future__ import annotations

from pathlib import Path

from medchron.update import select as S

BANNER = "01/20/2026 - Admission (Discharged) in Example Hospital (continued)\nAttending: Example Surgeon (M.D.)"
BODY = "\n".join(f"Review of systems item {i} was negative on the questionnaire." for i in range(30))
HEAD_PAGE = (
    BANNER + "\nDischarge Note\nThe patient was discharged home in stable condition.\n"
    "Preoperative Assessment\nAuthor: Example Nurse (N.P.) Author Type: NURSE PRACTITIONER\n"
    "Filed: 01/06/2026 2:04 PM Date of Service: 01/06/2026 9:40 AM\nStatus: Signed\n" + BODY
)
MIDDLE = BANNER + "\n" + BODY
LAST = BANNER + "\nIMPRESSION:\nThe patient is optimized for planned surgery.\nHistory provided by: Patient"


def test_a_continuation_page_carries_its_notes_heading_from_an_uncomposed_page() -> None:
    pages = {1: HEAD_PAGE, 2: MIDDLE, 3: LAST}
    text = S.selected_text(pages, [3])
    assert "[note heading, p.1: the note p.3 continues" in text
    assert "Author: Example Nurse (N.P.)" in text and "Date of Service: 01/06/2026" in text
    assert text.index("[note heading") < text.index("[p.3]")


def test_a_continuation_page_with_no_heading_found_is_flagged_never_assigned() -> None:
    pages = {1: MIDDLE, 2: MIDDLE, 3: LAST}
    text = S.selected_text(pages, [3])
    assert "[note heading: none found on the pages before p.3" in text
    assert "(note author not stated on the composed pages)" in text
    assert "Author:" not in text


def test_no_heading_block_when_the_page_before_is_composed_or_the_page_has_its_own() -> None:
    assert "the note p.3 continues" not in S.selected_text({1: HEAD_PAGE, 2: MIDDLE, 3: LAST}, [2, 3])
    own = "Progress Note\nAuthor: Example Doctor (M.D.)\nDate of Service: 02/01/2026\n" + BODY
    assert "[note heading" not in S.selected_text({1: MIDDLE, 2: own}, [2])
    assert "[note heading" not in S.selected_text({1: LAST}, [1])


def test_the_compose_prompt_keys_a_continuation_to_its_note_heading() -> None:
    text = (Path(__file__).parents[1] / "medchron" / "prompts" / "map-system.md").read_text()
    assert "NOTE HEADING" in text and "never the date or physician of a page banner" in text
