"""Pleading paper's margin line numbers never reach the text a quote is taken
from (2026-11-05: a served discovery set quoted the defendant's answer as
"lease, 26 occupy" and "negligent or 8 otherwise"). Synthetic text only."""

from __future__ import annotations

import re
from pathlib import Path

from jobs.drafting import quote_end
from jobs.stages import pleading_margin as pm

BODY = [
    "DEFENDANT'S ANSWER TO COMPLAINT",
    "",
    "1. Defendant admits that it held the lease,",
    "and that it permitted tenants to occupy the premises at all",
    "times alleged. Defendant denies that there was no",
    "unreasonable risk of harm, and denies that it was negligent or",
    "otherwise at fault within 30 days of the incident.",
] + [f"Paragraph text of the answer, line {i} of the filler." for i in range(21)]

QUOTES = (
    "held the lease, and that it permitted tenants to occupy the premises",
    "there was no unreasonable risk of harm",
    "it was negligent or otherwise at fault within 30 days",
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _leading(lines: list[str]) -> str:
    """pypdf's shape: each margin number opens its printed line."""
    return "\n".join(f"{n} {t}".rstrip() for n, t in enumerate(lines, 1))


def _clean(page: str) -> None:
    out = pm.strip_page(page)
    flat = _flat(out)
    for q in QUOTES:
        assert q in flat, (q, out)
    assert "1. Defendant admits" in out  # the answer's own paragraph number stays
    assert "within 30 days" in out and "line 20 of" in out  # content numbers stay
    assert not re.search(r"(?m)^\s*\d{1,2}\s*$", out)  # no number-only lines left


def test_margin_numbers_opening_each_line_are_removed() -> None:
    _clean(_leading(BODY))


def test_margin_numbers_printed_as_a_block_before_the_text_are_removed() -> None:
    _clean("\n".join(str(n) for n in range(1, 29)) + "\n" + "\n".join(t for t in BODY if t))


def test_margin_numbers_closing_each_line_are_removed() -> None:
    _clean("\n".join(f"{t} {n}".lstrip() for n, t in enumerate(BODY, 1)))


def test_margin_numbers_on_their_own_lines_between_text_lines_are_removed() -> None:
    _clean("\n".join(f"{n}\n{t}" if t else str(n) for n, t in enumerate(BODY, 1)))


def test_a_deposition_page_keeps_its_line_numbers() -> None:
    page = "\n".join(f"{n}   {'Q.' if n % 2 else 'A.'}  Where were you on the {n}th?" for n in range(1, 26))
    assert pm.strip_page(page + "\n26") == page + "\n26"


def test_a_page_that_is_not_pleading_paper_is_untouched() -> None:
    page = "1 cup flour\n2 eggs\n3 Mix well.\nServes 4"
    assert pm.strip_page(page) == page


def test_a_paged_document_is_cleaned_page_by_page_under_either_marker() -> None:
    page = _leading(BODY)
    for doc in (f"[p.1]\n{page}\n[p.2]\n{page}", f"===== page 1 =====\n{page}\n===== page 2 =====\n{page}"):
        out = pm.strip_document(doc)
        assert out.count("[p.") + out.count("===== page") == 2
        assert _flat(out).count(QUOTES[0]) == 2
        assert pm.strip_document(out) == out  # idempotent: a cached text cleans to itself


def _pleading_pdf(path: Path) -> Path:
    import pymupdf

    doc = pymupdf.open()
    pg = doc.new_page(width=612, height=792)
    for i, line in enumerate(BODY[:28]):
        pg.insert_text((40, 72 + i * 24), str(i + 1), fontsize=11)
        if line:
            pg.insert_text((100, 72 + i * 24), line, fontsize=11)
    doc.save(str(path))
    return path


def test_both_extractors_write_the_answer_without_its_margin_numbers(tmp_path: Path) -> None:
    from jobs.litigation import extract as lit
    from jobs.stages import extract

    pdf = _pleading_pdf(tmp_path / "answer.pdf")
    stage_text = "\n".join(t for _n, t in extract.pdf_text(pdf))
    lit_text, _scanned = lit.pdf_text(pdf, None)
    for text in (stage_text, lit_text):
        flat = _flat(text)
        assert all(q in flat for q in QUOTES), text
        assert "lease, 4 and" not in flat and "or 7 otherwise" not in flat


def test_a_cached_litigation_text_is_read_without_its_margin_numbers(tmp_path: Path) -> None:
    from jobs.litigation import extract as lit
    from jobs.litigation import textcache

    cache = textcache.TextCache(tmp_path / "tc", method_version="v", job_id="J", log=lambda m: None)
    assert cache.put_text("ab" * 32, "===== page 1 =====\n" + _leading(BODY), scanned_pages=0, matter_id="m1")
    rec = lit.extract_cached(tmp_path / "m2", {"id": "f1", "sha256": "ab" * 32, "path": "x.pdf"}, None, cache)
    assert QUOTES[0] in _flat(Path(rec["text_path"]).read_text(encoding="utf-8"))


def test_a_quote_never_ends_on_a_dangling_comma() -> None:
    doc = (
        'Admit that YOU "held the lease,".\n'
        'Admit that "there was no unreasonable risk," (Answer, p. 2).\n'
        'Admit that "the lease," "the premises," and "the stairs" were YOURS.\n'
        'Admit that YOU denied "negligence,"\n'
    )
    out, notes = quote_end.trim(doc)
    assert 'YOU "held the lease".' in out
    assert '"there was no unreasonable risk" (Answer' in out
    assert '"the lease," "the premises," and' in out  # a series keeps its commas
    assert 'denied "negligence"\n' in out
    assert notes and "3" in notes[0]
    assert quote_end.trim(out) == (out, [])
