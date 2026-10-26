"""Why each uncovered document went unread, as a fixed token (`uncovered_why`).

2026-10-26: the Operator guessed two password-protected files were scans with
no text layer, because the record carried only a count. These tests pin that
the record now carries the reason, in tokens only, through every writer and
through the union.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from medchron import config as config_mod, unopened, uncovered_why as W
from medchron.covered import covered_payload, covered_sets, merge_covered
from medchron.update import coverage as UCov
from medchron.update.outcome import IMAGE_UNREAD

OPERATOR = Path(__file__).resolve().parents[3]


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_an_update_records_a_password_protected_file_as_such(tmp_path: Path) -> None:
    ud = tmp_path / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    _jsonl(
        tmp_path / "raw_manifest.jsonl",
        [
            {"id": "lock", "name": "Hospital Encounters", "ext": ".pdf", "ok": True},
            {"id": "torn", "name": "x", "ext": ".pdf", "ok": False},
            {"id": "cited", "name": "y", "ext": ".pdf", "ok": True},
        ],
    )
    (ud / "selection.json").write_text(
        json.dumps(
            {
                "skipped": {
                    unopened.REASON: ["lock"],
                    "not a PDF or an image": ["docx"],
                    IMAGE_UNREAD: ["img"],
                },
                "exhibits": [
                    {"id": "cited", "read": True},
                    {"id": "blank", "read": False, "excluded": {"no text on the page": [1]}},
                ],
            }
        )
    )
    sets = UCov.covered_sets(tmp_path, "alpha")
    assert sets["covered"] == ["cited"]
    assert sets["uncovered"] == ["blank", "docx", "img", "lock", "torn"]
    assert sets["why"] == {
        "blank": W.NO_TEXT,
        "docx": W.NOT_PDF_OR_IMAGE,
        "img": W.IMAGE_UNREADABLE,
        "lock": W.PASSWORD_PROTECTED,
        "torn": W.RETRIEVAL_FAILED,
    }
    # Tokens only: no file name reaches the record.
    assert "Hospital Encounters" not in json.dumps(sets)


def _seed_run(d: Path) -> None:
    (d / "units").mkdir(parents=True)
    (d / "units" / "alpha.json").write_text(
        json.dumps(
            [
                {"id": "a", "name": "note", "ext": ".pdf"},
                {"id": "c", "name": "scan", "ext": ".pdf", "compose_skip": "read in full; 3 words of text; nothing"},
            ]
        )
    )
    _jsonl(
        d / "raw_manifest.jsonl",
        [
            {"id": "a", "name": "note", "ext": ".pdf", "ok": True},
            {"id": "c", "name": "scan", "ext": ".pdf", "ok": True},
            {"id": "e", "name": "torn", "ext": ".pdf", "ok": False},
            {"id": "msgatt-1", "name": "locked", "ext": ".pdf", "ok": True, "container_id": "email-9"},
            {"id": "msgatt-2", "name": "docx", "ext": ".docx", "ok": True, "container_id": "email-9"},
        ],
    )
    _jsonl(d / "extracted.jsonl", [{"id": "msgatt-1", "error": "File has not been decrypted"}])
    (d / "orphans.json").write_text(
        json.dumps(
            {
                "orphans": [
                    {"name": "locked.pdf", "reason": unopened.REASON},
                    {"name": "docx.docx", "reason": "a .docx file, a type this review does not read"},
                ]
            }
        )
    )
    rd = d / "runs" / "alpha"
    rd.mkdir(parents=True)
    (rd / "entries_final.md").write_text("01/02/2026\nExample Clinic\n\nStrain. (Exhibit 1 - p. 1)\n")
    (d / "out" / "alpha").mkdir(parents=True)
    (d / "out" / "alpha" / "page_map.json").write_text(json.dumps([{"exhibit": 1, "files": [{"file": "note.pdf"}]}]))


def test_a_run_records_why_each_uncovered_document_went_unread(tmp_path: Path, firm_config_path: Path) -> None:
    _seed_run(tmp_path)
    cfg = config_mod.load(str(firm_config_path))
    sets = covered_sets(tmp_path, "alpha", cfg)
    assert sets["uncovered"] == ["c", "e", "email-9"]
    # The email parent keeps its attachments' token; two attachments disagree,
    # so the first in sorted id order (msgatt-1, the locked PDF) decides.
    assert sets["why"] == {"c": W.NO_TEXT, "e": W.RETRIEVAL_FAILED, "email-9": W.PASSWORD_PROTECTED}
    assert covered_payload(tmp_path, ["alpha"], cfg)["why"] == sets["why"]


def test_the_union_keeps_reasons_only_for_ids_still_uncovered() -> None:
    merged = merge_covered(
        [
            {"covered": {"covered": ["a"], "uncovered": ["b", "c"], "why": {"b": W.NO_TEXT, "c": W.NOT_EXPLAINED}}},
            {"covered": {"covered": ["c"], "uncovered": ["d"], "why": {"d": "a file name", "c": W.NO_TEXT}}},
        ]
    )
    assert merged["uncovered"] == ["b", "c", "d"]
    # First unit to name an id decides; a value outside the vocabulary is dropped.
    assert merged["why"] == {"b": W.NO_TEXT, "c": W.NOT_EXPLAINED}
    assert W.union([{"x": W.NO_TEXT}], []) == {}


def test_the_runner_vocabulary_is_the_brokers() -> None:
    """The broker refuses any token outside its own set, and neither side can
    import the other, so the two tuples are pinned here by reading the broker's
    source."""
    src = (OPERATOR / "workspace_broker" / "medchron_coverage.py").read_text(encoding="utf-8")
    block = src.split("WHY_TOKENS = frozenset(", 1)[1].split(")", 1)[0]
    assert set(re.findall(r'"([a-z_]+)"', block)) == set(W.TOKENS)
