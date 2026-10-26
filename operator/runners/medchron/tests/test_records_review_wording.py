"""The records review reads plainly to the firm (``update/review_list``).

Proof 5 of the first real update (2026-10-25): 196 of 223 rows carried the
delivered run's internal label ("composer: ..."), and the intro counted an
attachment found inside a named email as a file the request named."""

from __future__ import annotations

from pathlib import Path

from medchron.stages.review import docx_text
from medchron.update import outcome as O, review_list as RL


def _f(rid: str, reason: str, text: str, name: str = "x.pdf", pages: int = 1) -> dict:
    return {"id": rid, "name": name, "reason": reason, "text": text, "pages": pages}


def test_a_reused_finding_drops_the_internal_label_and_says_where_it_came_from():
    cases = {
        "composer: scene photograph, no record text": "scene photograph, no record text",
        "compose skipped: read in full; 12 words of text; nothing in it was cited": "read in full; nothing in it to cite",
        "compose skipped: billing-only source (VENDOR_INVOICE), 2 chunk(s) fully captured by billing_extract": (
            "an invoice to the firm (records or services), not a medical bill"
        ),
        "compose skipped: billing-only source (MEDICAL_BILL), 1 chunk(s) fully captured": (
            "billing pages only; their charges are in the billing worksheet"
        ),
        "set aside: records request": "records request",
        "evidenced billing dates only (3); the billing chart carries them": (
            "billing dates only (3); they are in the billing worksheet"
        ),
    }
    for raw, want in cases.items():
        got = RL.plain(_f("a", O.PRIOR, raw))
        assert got == f"read for the delivered chronology: {want}", raw
        for word in ("composer", "compose skipped", "chunk", "billing_extract", "vendor_invoice", "set aside"):
            assert word not in got.lower(), (raw, got)


def test_other_reasons_keep_their_own_wording():
    """Falsifier: only a reused finding is rewritten."""
    f = _f("b", O.EXCLUDED, "excluded by the firm's rule: a photograph, not a medical record")
    assert RL.plain(f) == "excluded by the firm's rule: a photograph, not a medical record"


def test_the_intro_counts_the_files_named_and_those_found_inside_them(tmp_path: Path):
    files = [
        _f("n1", O.PRIOR, "composer: letter"),
        _f("n2", O.EXCLUDED, "excluded"),
        _f("msgatt-1", O.PRIOR, "composer: x"),
    ]
    path = RL.render(tmp_path / "r.docx", "Alpha Example", "2099", RL.rows(files, {}, {"n1", "n2"}))
    text = docx_text(path)
    assert "The 2 file(s) named in the request, plus 1 found inside them" in text
    assert "composer" not in text.lower()
