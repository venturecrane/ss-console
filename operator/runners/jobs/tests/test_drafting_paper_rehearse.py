"""The $0 rehearse command: counts and reasons per file, compared with a key,
and never a request's text."""

from __future__ import annotations

import json
from pathlib import Path

import paper_testkit as kit
import pytest

from jobs.drafting.paper import rehearse


def test_rehearse_prints_counts_and_matches_the_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kit.set_docx(tmp_path / "a.docx")
    kit.response_docx(tmp_path / "b.docx")
    key = {
        "a.docx": {
            "items": 40,
            "terms": ["YOU", "DOCUMENT", "INCIDENT", "PERSON"],
            "declaration": {"counts": [40, 40, 40], "lists": {"A": list(range(1, 21)), "C": [1]}},
        },
        "b.docx": {"items": 3, "terms": []},
    }
    (tmp_path / "key.json").write_text(json.dumps(key))
    assert rehearse.main([str(tmp_path), "--key", str(tmp_path / "key.json")]) == 0
    out = capsys.readouterr().out
    assert "a.docx: special_interrogatories, 40 items, numbering ok" in out
    assert "lists {A: 20, C: 1, E: 20}" in out
    assert out.count("key: match") == 2
    assert "2 files, 2 read and matched, 0 not" in out
    assert "invented topic" not in out and "invented answer" not in out  # no document text


def test_rehearse_reports_unread_and_key_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kit.set_docx(tmp_path / "gap.docx", kit.SetSpec(skip=(3,)))
    kit.set_docx(tmp_path / "ok.docx", kit.SetSpec(n=12, declaration=False))
    (tmp_path / "key.json").write_text(json.dumps({"ok.docx": {"items": 11, "terms": ["YOU"]}}))
    assert rehearse.main([str(tmp_path), "--key", str(tmp_path / "key.json")]) == 1
    out = capsys.readouterr().out
    assert "gap.docx: UNREAD region=items" in out and "a gap" in out
    assert "items 12 != key 11" in out and "extra ['DOCUMENT', 'INCIDENT', 'PERSON']" in out


def test_rehearse_empty_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rehearse.main([str(tmp_path)]) == 1
    assert "no .docx files" in capsys.readouterr().out
