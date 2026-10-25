"""An update sets a password-protected PDF aside, named, instead of crashing
(2026-10-25: the third proof run of a real update died in update_select on
pypdf's FileNotDecryptedError)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from medchron import unopened
from medchron.stages import update_select
from medchron.update import outcome


def _pdf(path: Path, text: str, *, password: str = "", owner_only: bool = False) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    if password or owner_only:
        doc.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw="owner-secret",
            permissions=pymupdf.PDF_PERM_PRINT,
        )
    else:
        doc.save(str(path))
    return path


def _sr(tmp_path: Path, rows: list[dict]) -> SimpleNamespace:
    d = tmp_path / "slug"
    d.mkdir()
    (d / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return SimpleNamespace(slug_dir=d)


def test_a_password_protected_pdf_is_set_aside_with_the_run_lanes_reason(tmp_path):
    locked = _pdf(tmp_path / "locked.pdf", "Clinic note", password="user-secret")
    plain = _pdf(tmp_path / "plain.pdf", "Clinic note")
    rows = [
        {"id": "L1", "ok": True, "path": str(locked), "ext": ".pdf", "sha256": "a"},
        {"id": "P1", "ok": True, "path": str(plain), "ext": ".pdf", "sha256": "b"},
    ]
    out, skipped = update_select._candidates(_sr(tmp_path, rows), {})  # type: ignore[arg-type]
    assert [r["id"] for r in out] == ["P1"]
    assert skipped[unopened.REASON] == ["L1"]


def test_a_pdf_locked_only_against_editing_is_read_and_counted(tmp_path):
    """Falsifier for an over-broad lock check: owner-only encryption opens
    without a password, so it is a candidate and its pages are counted."""
    owner = _pdf(tmp_path / "owner.pdf", "Clinic note", owner_only=True)
    rows = [{"id": "O1", "ok": True, "path": str(owner), "ext": ".pdf", "sha256": "c"}]
    out, skipped = update_select._candidates(_sr(tmp_path, rows), {})  # type: ignore[arg-type]
    assert [r["id"] for r in out] == ["O1"] and unopened.REASON not in skipped
    assert update_select._write_pdf(owner, ".pdf", tmp_path / "out" / "o.pdf") == 1


def test_the_outcome_names_a_password_protected_file_unreadable_not_unaccounted():
    got = outcome._skipped("L1", {"skipped": {unopened.REASON: ["L1"]}})
    assert got == (outcome.UNREADABLE, unopened.REASON)
