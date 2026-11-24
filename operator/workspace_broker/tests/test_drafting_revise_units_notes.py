"""A set revision names the newest delivery's attorney notes once, so the
runner files their new version with the revision's changes on top
(``drafting_revise_units.collect``, A2). Invented ids and names."""

from __future__ import annotations

import json
from types import SimpleNamespace

from workspace_broker import drafting_revise_units as ru

PRIOR, LATER = "01JAAAAAAAAAAAAAAAAAAAAAAA", "01JCCCCCCCCCCCCCCCCCCCCCCC"


def _f(fid: str, name: str, role: str = "draft") -> dict:
    return {"role": role, "file_id": fid, "name": name, "size": 100, "sha256": "a" * 64}


def _db(files: dict[str, list[dict]]):
    return SimpleNamespace(proven_files=lambda jid: files[jid])


def _row(jid: str) -> dict:
    return {"id": jid, "folder_id": "folder-0001", "delivery_json": json.dumps({})}


def test_the_newest_deliverys_notes_are_named_once() -> None:
    """FALSIFIER: name no notes and the attorney never sees what changed; name the
    oldest and the notes filed describe a delivery that was re-filed since."""
    db = _db({
        LATER: [_f("d-0001", "Responses to Requests for Production, Set One.docx"),
                _f("n-0002", "Attorney Notes (later).docx", "attorney_notes")],
        PRIOR: [_f("d-0001", "Responses to Requests for Production, Set One.docx"),
                _f("n-0001", "Attorney Notes.docx", "attorney_notes")],
    })  # fmt: skip
    block, bad = ru.collect(db, [_row(LATER), _row(PRIOR)], lambda r: True, notes_from=lambda r: True)
    assert bad == "" and block is not None
    assert block["notes"] == {"file_id": "n-0002", "name": "Attorney Notes (later).docx", "size": 100,
                              "sha256": "a" * 64, "folder_id": "folder-0001"}  # fmt: skip
    assert [u["file_id"] for u in block["units"]] == ["d-0001"]


def test_a_delivery_with_no_notes_names_none() -> None:
    db = _db({PRIOR: [_f("d-0001", "Responses to Special Interrogatories, Set One.docx")]})
    block, _bad = ru.collect(db, [_row(PRIOR)], lambda r: True, notes_from=lambda r: True)
    assert block is not None and "notes" not in block


def test_notes_come_only_from_the_threads_own_jobs() -> None:
    """A job outside the thread that only holds a draft of the same set.
    FALSIFIER: take notes from every job passing the unit filter and the
    revision files a new version of another request's attorney notes."""
    db = _db({
        LATER: [_f("d-0002", "Responses to Special Interrogatories, Set One.docx"),
                _f("n-0009", "Attorney Notes (other request).docx", "attorney_notes")],
        PRIOR: [_f("d-0001", "Responses to Requests for Production, Set One.docx"),
                _f("n-0001", "Attorney Notes.docx", "attorney_notes")],
    })  # fmt: skip
    block, _bad = ru.collect(db, [_row(LATER), _row(PRIOR)], lambda r: True, notes_from=lambda r: r["id"] == PRIOR)
    assert block is not None and block["notes"]["file_id"] == "n-0001"
    block, _bad = ru.collect(db, [_row(LATER), _row(PRIOR)], lambda r: True)
    assert block is not None and "notes" not in block
