"""The find stage reads a found form set's boxes only when the Operator drafts
form sets (``served.FORM_SETS_BY_OPERATOR``); off, nothing is read or spent.
Every name and value here is invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobs.drafting import served as served_mod
from jobs.drafting.forms import read_stage
from jobs.drafting.outcome import DraftingFailed


def _job(tmp: Path, drafted_by: str, on_disk: bool = True) -> tuple[Path, Path]:
    data = tmp / "data"
    data.mkdir()
    pdf = tmp / "form.pdf"
    if on_disk:
        pdf.write_bytes(b"invented")
    (data / "corpus.json").write_text(json.dumps([{"id": "f1", "path": str(pdf)}]))
    sets = [
        {"set_type": "special_interrogatories", "drafted_by": "operator", "source": "f2"},
        {"set_type": "form_interrogatories", "drafted_by": drafted_by, "source": "f1", "request_count": 3},
    ]
    (tmp / "served.json").write_text(json.dumps({"sets": sets}))
    return data, pdf


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(read_stage, "read_served_form", lambda pdf, jd, i, door, model, **kw: calls.append((pdf, i)))
    return calls


def test_off_reads_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert served_mod.FORM_SETS_BY_OPERATOR is False
    data, _ = _job(tmp_path, "operator")
    calls = _spy(monkeypatch)
    got = read_stage.read_found(data, tmp_path, None, "m", by_operator=served_mod.FORM_SETS_BY_OPERATOR, log=print)
    assert got == [] and calls == []


def test_on_reads_each_operator_form_set_by_its_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data, pdf = _job(tmp_path, "operator")
    calls = _spy(monkeypatch)
    assert read_stage.read_found(data, tmp_path, None, "m", by_operator=True, log=print) == [1]
    assert calls == [(pdf, 1)]


def test_on_leaves_an_smd_form_set_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data, _ = _job(tmp_path, "smd")
    calls = _spy(monkeypatch)
    assert read_stage.read_found(data, tmp_path, None, "m", by_operator=True, log=print) == [] and calls == []


def test_a_missing_file_stops_form_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data, _ = _job(tmp_path, "operator", on_disk=False)
    _spy(monkeypatch)
    with pytest.raises(DraftingFailed, match="^form_incomplete: "):
        read_stage.read_found(data, tmp_path, None, "m", by_operator=True, log=print)


def test_one_revision_printed_with_two_spaces_on_one_page_is_still_one_revision() -> None:
    """L5 (2026-11-20 replay): the dedupe ran before whitespace was collapsed, so every real form
    read as two revisions and stopped form_unconfirmed."""
    pages = [
        "DISC-001 [Rev. January 1, 2024]",
        "DISC-001 [Rev. January  1, 2024]",
        "DISC-001 [ Rev. January 1,\n2024 ]",
    ]
    assert read_stage.revision_of(pages) == "Rev. January 1, 2024"
    assert read_stage.revision_of([*pages, "DISC-001 [Rev. July 1, 2025]"]) is None
