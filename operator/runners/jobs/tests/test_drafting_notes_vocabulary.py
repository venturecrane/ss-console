"""The attorney-note lines code writes never carry the system's own words.

A $0 census replay of a real response job (2026-11-26): the Index of Exhibits
note line read "written by code", the pre-delivery review graded it MUST-FIX
("9 notes wording"), and every response job with an index was held with
nothing filed. Each note-producing function's fixed wording runs through both
checks that grade it: ``wording.notes_findings`` and the review's own
``_artifact_findings``.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from jobs.drafting import amended, exhibit_index, opening_demand, review, wording
from jobs.drafting.forms import revise_hook


def _graded(notes: list[str]) -> list[str]:
    """What the notes check and the review's wording check find in these lines."""
    md = "\n".join(f"- {n}" for n in notes)
    out = wording.notes_findings(md)
    out += [f.finding for f in review._artifact_findings("discovery_response", [], md) if f.check == "9 notes wording"]
    return out


def _system_words(fragment: str) -> list[str]:
    """The system's words in one fixed fragment of a note (a fragment is not a
    whole line, so only the vocabulary checks apply)."""
    return wording.vocabulary(fragment) + [m.group(0) for m in review._INTERNAL.finditer(fragment)]


def _consts(code: types.CodeType, doc: str | None) -> list[str]:
    out: list[str] = []
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            out += _consts(c, None)
        elif isinstance(c, str) and c != doc:
            out.append(c)
    return out


NOTE_WRITERS = [exhibit_index.notes, amended.unit_notes, opening_demand.fill, revise_hook._revise]


@pytest.mark.parametrize("fn", NOTE_WRITERS, ids=lambda f: f"{f.__module__}.{f.__name__}")
def test_every_fixed_word_a_note_writer_carries_passes_the_notes_checks(fn):
    words = [w for w in _consts(fn.__code__, fn.__doc__) if w.strip()]
    assert words
    bad = {w: _system_words(w) for w in words}
    assert {w: f for w, f in bad.items() if f} == {}


def test_the_index_of_exhibits_notes_pass_the_review_and_keep_their_counts(tmp_path: Path):
    rec = {
        "listed": [
            {"letter": "A", "title": "MEDICAL RECORDS", "requests": ["1"], "files": ["a.pdf", "b.pdf"]},
            {"letter": "B", "title": "MEDICAL BILLS", "requests": ["2"], "files": ["c.pdf"]},
        ],
        "unproduced": [],
        "left_out": [],
        "walled": 0,
        "unresolved": [],
    }
    (tmp_path / exhibit_index.RECORD).write_text(json.dumps(rec), encoding="utf-8")
    notes = exhibit_index.notes(tmp_path)
    assert notes[0].startswith("Index of Exhibits:") and notes[0].endswith("3 documents in 2 exhibits")
    assert _graded(notes) == []
