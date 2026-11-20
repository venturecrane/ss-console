"""Proof 1 counts delivered sentences and summary lines carried more often,
never an entry's heading lines (2026-11-16).

WHY. A real update failed ``update_proofs`` with "duplicated 1": the line was
a provider heading over 80 characters that 7 delivered entries carried and
one added entry (a re-filed copy of a visit, adding details) carried too.
``failed`` is terminal for a client job. Every name and value is synthetic.
"""

from __future__ import annotations

import pytest

from jobs.medchron.update import body as B, proofs as P

LONG = "Example Regional Medical Center Department of Physical Medicine and Rehabilitation Outpatient Clinic"
SENT = "Physical therapy evaluation recorded lumbar flexion limited to forty degrees with pain at end range. (Exhibit 1 - p. 1)"
SUMMARY = "The records describe a course of conservative care for neck and low back pain over the following months"


def _entry(date: str, text: str) -> str:
    return f"{date}\n{LONG} | All Other Information\n\nAll Other Information\n{text}"


def _md(entries: list[str], pre: str = "") -> str:
    return (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + (pre + "\n\n" if pre else "") + "\n\n".join(entries) + "\n\n## Exhibit List\n"
    )


BASE = [_entry("01/05/2026", SENT), _entry("01/19/2026", "Therapy continued with home exercise. (Exhibit 1 - p. 2)")]
ADDED = _entry(
    "01/12/2026", "Therapy note added traction to the plan of care for the cervical spine. (Exhibit 2 - p. 1)"
)


def _lines(md: str, rendered: bool) -> list[str]:
    """The Medical Summary lines as the proof reads them: the markdown's, or
    the rendered entry table's, whose left cell shows the provider alone."""
    out = [ln.strip() for ln in B.chronology_lines(md) if ln.strip()]
    return [ln.replace(f"{LONG} | All Other Information", LONG) for ln in out] if rendered else out


def _proof_1(base_md: str, updated_md: str, rendered: bool) -> list[str]:
    x = P.Inputs(
        delivered_lines=_lines(base_md, rendered),
        updated_lines=_lines(updated_md, rendered),
        base_md=base_md,
        updated_md=updated_md,
        composed=[],
        new_exhibits={2},
    )
    r = P.Result()
    P._delivered_text(x, r)
    return r.fails


@pytest.mark.parametrize("rendered", [False, True])
def test_an_added_entry_under_a_long_delivered_provider_heading_passes(rendered: bool) -> None:
    assert len(LONG) > 80
    base = _md(BASE)
    updated = _md([BASE[0], ADDED, BASE[1]])
    assert _proof_1(base, updated, rendered) == []


@pytest.mark.parametrize("rendered", [False, True])
def test_a_delivered_cited_sentence_carried_again_still_fails(rendered: bool) -> None:
    base = _md(BASE)
    again = _entry("01/12/2026", SENT)
    assert _proof_1(base, _md([BASE[0], again, BASE[1]]), rendered) == ["1"]


@pytest.mark.parametrize("rendered", [False, True])
def test_a_delivered_uncited_summary_line_carried_again_still_fails(rendered: bool) -> None:
    assert len(SUMMARY) > 80 and "(Exhibit" not in SUMMARY
    base = _md(BASE, pre=SUMMARY)
    assert _proof_1(base, _md([BASE[0], ADDED, BASE[1]], pre=SUMMARY + "\n\n" + SUMMARY), rendered) == ["1"]


def test_entry_headings_are_the_parsers_date_and_provider_lines() -> None:
    heads = P.entry_headings(_md(BASE), _md([ADDED]))
    assert {"01/05/2026", "01/12/2026", f"{LONG} | All Other Information", LONG} <= heads
    assert SENT not in heads and "All Other Information" not in heads
