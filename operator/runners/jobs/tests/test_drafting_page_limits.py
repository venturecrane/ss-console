"""A section's page limit, measured on the rendered pages (``page_limits.py``).

The firm's limit ("the liability section no more than three pages", 2026-11-03)
is a fact of layout, so the job measures the PDF of the rendered file. These
tests build PDFs whose page layout is known, so each span is exact; one test
runs the real LibreOffice conversion when the machine has it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from drafting_testkit import COURT, SECTIONS, firm_data
from jobs.drafting import finish, firm, format_check, page_limits, render
from jobs.drafting.outcome import DraftingFailed

LIAB = "V. LIABILITY"
_REAL_CONVERT = page_limits.convert


def _pdf(path: Path, pages: list[list[str]]) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for i, t in enumerate(lines):
            page.insert_text((72, 72 + 24 * i), t, fontsize=12)
        page.insert_text((300, 760), str(len(doc)), fontsize=12)  # the footer's page number
    doc.save(str(path))
    return path


HEADS = ["I. INTRODUCTION", LIAB, "VI. DAMAGES"]


def test_a_section_spans_from_its_heading_page_to_its_last_text(tmp_path):
    pdf = _pdf(
        tmp_path / "a.pdf",
        [["I. INTRODUCTION", "text"], ["text", LIAB, "text"], ["text"], ["text"], ["last of it", "VI. DAMAGES"]],
    )
    [s] = page_limits.spans(pdf, HEADS, {LIAB: 3})
    assert (s.pages, s.over) == (4, True)


def test_a_next_heading_at_the_top_of_a_page_ends_the_span_before_it(tmp_path):
    pdf = _pdf(tmp_path / "b.pdf", [["I. INTRODUCTION"], [LIAB, "text"], ["text"], ["VI. DAMAGES", "text"]])
    [s] = page_limits.spans(pdf, HEADS, {LIAB: 3})
    assert (s.pages, s.over) == (2, False)


def test_a_heading_not_on_any_page_is_unmeasured_never_within_the_limit(tmp_path):
    pdf = _pdf(tmp_path / "c.pdf", [["I. INTRODUCTION"], ["text"]])
    with pytest.raises(page_limits.Unmeasured):
        page_limits.spans(pdf, HEADS, {LIAB: 3})


def test_the_firm_config_names_an_authored_section_in_whole_pages():
    secs = list(SECTIONS)
    assert page_limits.validate({"mediation_brief": {LIAB: 3}}, firm.CLASSES, secs) == []
    assert page_limits.validate({"mediation_brief": {"LIABILITY": 3}}, firm.CLASSES, secs) == []
    for bad in ({"mediation_brief": {"V. LIABILTY": 3}}, {"mediation_brief": {LIAB: 0}}, {"brief": {LIAB: 3}}):
        assert page_limits.validate(bad, firm.CLASSES, secs), bad


class _Run(SimpleNamespace):
    def enforce(self, path, fmt, res):
        page_limits.enforce(path, self.cls, fmt, res, self.repaired, self.wrote.append, lambda m: None)


def _fmt(limit: int = 3) -> dict:
    f = copy.deepcopy(firm_data()["format"])
    f["limits"] = {"mediation_brief": {LIAB: limit}}
    return f


def _run(monkeypatch, tmp_path, pages, repaired=False):
    pdf = _pdf(tmp_path / "m.pdf", pages)
    monkeypatch.setattr(page_limits, "convert", lambda docx, out: pdf)
    monkeypatch.setattr(format_check, "headings", lambda path: HEADS)
    return _Run(cls="mediation_brief", wrote=[], repaired=repaired)


LONG = [["I. INTRODUCTION"], [LIAB, "t"], ["t"], ["t"], ["t"], ["VI. DAMAGES"]]


def test_over_the_limit_is_a_content_finding_for_the_one_repair(monkeypatch, tmp_path):
    run = _run(monkeypatch, tmp_path, LONG)
    res = format_check.Result()
    run.enforce(tmp_path / "x.docx", _fmt(), res)
    assert res.content and "runs 4 pages" in res.content[0] and not res.ours
    assert run.wrote == [[{"section": LIAB, "pages": 4, "limit": 3}]]


def test_still_over_after_the_repair_stops_for_smd_with_the_count(monkeypatch, tmp_path):
    run = _run(monkeypatch, tmp_path, LONG, repaired=True)
    with pytest.raises(DraftingFailed, match=r"^length_limit: .*runs 4 pages \(the firm's limit is 3\)"):
        run.enforce(tmp_path / "x.docx", _fmt(), format_check.Result())


def test_no_converter_stops_the_job_never_passes_it(monkeypatch, tmp_path):
    run = _run(monkeypatch, tmp_path, LONG)
    monkeypatch.setattr(page_limits, "convert", _REAL_CONVERT)  # the real one, with no soffice on PATH
    monkeypatch.setattr(page_limits, "soffice", lambda: None)
    with pytest.raises(DraftingFailed, match=r"^length_limit: .*could not be measured"):
        run.enforce(tmp_path / "x.docx", _fmt(), format_check.Result())


def test_a_class_without_limits_converts_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(page_limits, "convert", lambda *a: pytest.fail("converted with no limit authored"))
    res = format_check.Result()
    run = _Run(cls="mediation_brief", wrote=[], repaired=False)
    run.enforce(tmp_path / "x.docx", firm_data()["format"], res)
    assert res.ok and not run.wrote


def test_the_job_runs_the_measure_after_the_format_check():
    """Wired, not only built: the render step calls it with the format repair's state."""
    import inspect

    src = inspect.getsource(finish.Finish._render_unit)
    assert (
        src.index("format_check.check(")
        < src.index("page_limits.step(self, u, path, fmt, res)")
        < src.index("repair_format(")
    )


@pytest.mark.skipif(page_limits.soffice() is None, reason="LibreOffice is not on this machine (the seat image has it)")
def test_the_real_conversion_measures_a_rendered_brief(tmp_path):
    long = "\n\n".join(
        "The defendant ran the red light and struck the plaintiff's car at speed. " * 6 for _ in range(14)
    )
    body = "\n\n".join(f"# {s}\n\n" + (long if s == LIAB else "Body text.") for s in SECTIONS)
    md = COURT + body + "\n\n{{ATTORNEY: settlement authority, the target figure, and any bracket}}"
    fmt = _fmt()
    path, _ = render.render(md, "mediation_brief", tmp_path / "brief.docx", fmt)
    [s] = page_limits.measure(path, "mediation_brief", fmt, format_check.headings(path))
    assert s.title == LIAB and s.pages > 3 and s.over


def test_the_job_step_reads_the_repair_state_and_records_the_measure(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, LONG)
    wrote: dict = {}
    run = SimpleNamespace(
        cls="mediation_brief",
        _is_done=lambda stage: stage == "u:format_repair",
        _uwrite=lambda u, name, body: wrote.__setitem__(name, body),
        log=lambda m: None,
    )
    unit = SimpleNamespace(stage=lambda s: f"u:{s}")
    with pytest.raises(DraftingFailed, match="^length_limit: after one shortening pass"):
        page_limits.step(run, unit, tmp_path / "x.docx", _fmt(), format_check.Result())
    assert wrote[page_limits.RECORD] == [{"section": LIAB, "pages": 4, "limit": 3}]
