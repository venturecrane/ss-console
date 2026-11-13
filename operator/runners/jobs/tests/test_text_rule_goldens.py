"""Every text checker's answers over its test corpus and a sample deliverable, against goldens.

The text rules (dates read and written, money, citations, the system's own
words, markers, garbled text and text layers, Word / PDF / RTF / HTML to text)
are written several times across the lanes (lanes review 2026-11-10, concept 7).
Wave 2 gives them one home. This is the instrument that proves the move changed
no answer. By calling each REAL checker:

* ``goldens/text_rules/<module>.json``: per checker, every input the existing
  tests fed it (recorded by ``text_rule_harvest.py``) and its answer (or what it
  raised). The test replays every stored input and fails on any different answer;
* ``goldens/text_rules/synthetic.json``: every checker over one fictional sample
  deliverable built here (a letter with dates in each written form, money,
  citations, markers and system words, as markdown, as a Word file with a
  chronology entry table, as a PDF, as RTF and as HTML).

``allowed-changes.json`` names the paths that may differ (none today).
``JOBS_WRITE_KIT_GOLDENS=1`` rewrites the answers for the stored inputs.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Callable

import pytest

import goldens_kit as gk
import text_rule_kit as kit

FAMILY = "text_rules"
DIR = gk.GOLDENS / FAMILY
ALLOWED = gk.allowed(FAMILY)
MODULES = sorted({m for m, _ in kit.TARGETS})

#: A fictional deliverable. Every name, number and date is invented.
LETTER = """# Demand for Settlement

Re: Example Claimant v. Example Insured
Claim No.: EX-0001
Date of Loss: March 14, 2024

Dear Example Adjuster,

On 03/14/2024 our client was struck at an intersection in Exampletown. She was seen at Example Clinic on 3/15/24
and again on 2024-04-02, and imaging on April 9, 2024 showed a disc protrusion (FILE: Example Clinic records.pdf, p. 2).
The deposition of Example Witness confirms the light was red (Example Witness Dep. 12:4).
"The light was red when the truck entered the intersection" (FILE: Police Report.pdf, p. 1).

Medical specials total $12,345.67, of which $1,200.00 is the emergency visit. The future care estimate is 8,500.
We demand $250,000 to settle. [ATTORNEY: confirm the policy limit] [TO BE SUPPLIED
{{FILL: date of service | at service}}

The digest shows a gap in treatment; the preflight found no missing bill. In this chunk the provider not identified.
=== NEEDS THE ATTORNEY ===
The next visit is scheduled for 12/31/2099.

Sincerely,
Example Counsel
"""
SOURCES = "Seen 03/14/2024 and 2024-04-02. Charges $12,345.67 and $1,200.00. March 14, 2024."
RTF = r"{\rtf1\ansi{\fonttbl\f0\fswiss Helvetica;}\f0\pard Example Clinic visit on 03/14/2024.\par Total \b $1,200.00\b0.\par}"
HTML = "<html><body><p>Example Clinic</p><p>Visit on <b>03/14/2024</b>.</p><script>x()</script><p>Total $1,200.00</p></body></html>"
GARBLED = "/12 /34 /56 /78 /90 qxzv bcdfg hjklm npqrst 0O/lI/2O24"
PAGES = [LETTER[:900], "", GARBLED, LETTER[900:]]
REVIEW = "| GRADE | check | finding | cite |\n|---|---|---|---|\n| MUST-FIX | 2 | a date the record does not print | entry 03/14/2024 |\n\nVERDICT: 1 MUST-FIX"
ENTRIES = [
    ("03/14/2024", "Example Clinic", "Emergency visit after the collision. Neck pain 6 of 10. (Exhibit 1 - p. 2)"),
    ("04/02/2024 (as written)", "Example Imaging", "MRI of the cervical spine; a 3 mm protrusion. (Exhibit 2 - p. 1)"),
]


def _docx(tmp: Path) -> Path:
    from docx import Document

    d = Document()
    for line in LETTER.splitlines():
        d.add_paragraph(line)
    t = d.add_table(rows=0, cols=2)
    for date, provider, body in ENTRIES:
        row = t.add_row().cells
        row[0].text = date
        row[0].add_paragraph(provider)
        row[1].text = body
    p = tmp / "Example Deliverable.docx"
    d.save(str(p))
    return p


def _pdf(pages: list[str]) -> bytes:
    from jobs_testkit import make_pdf

    return make_pdf([p.encode("latin-1", "replace").decode("latin-1") for p in pages])


def synthetic(tmp: Path) -> dict[str, Any]:
    """Every checker over the sample deliverable, in each form it reads."""
    from jobs.demand import review as demand_review
    from jobs.drafting.citations import Policy
    from jobs.litigation import extract as lit
    from jobs.negotiation import text as neg
    from jobs.stages import extract as st_extract, review as st_review

    docx_path = _docx(tmp)
    docx_bytes = docx_path.read_bytes()
    pdf = _pdf(PAGES)
    text_pdf = _pdf([p for p in PAGES if p])
    pdf_path = tmp / "Example Deliverable.pdf"
    pdf_path.write_bytes(pdf)
    policy = Policy(frozenset({"medical", "deposition-quote", "police-report"}))
    texts = {
        "Example Clinic records.pdf": "disc protrusion",
        "Police Report.pdf": "The light was red when the truck entered the intersection",
    }
    calls: dict[str, Callable[[], Any]] = {}
    one_text = [
        ("jobs.proof.checks", "system_vocabulary"),
        ("jobs.proof.checks", "extraction_vocabulary"),
        ("jobs.proof.checks", "sentinels"),
        ("jobs.proof.checks", "money"),
        ("jobs.proof.checks", "money_in_sources"),
        ("jobs.proof.checks", "dates"),
        ("jobs.proof.review", "complete"),
        ("jobs.demand.gapaudit", "firm_words"),
        ("jobs.demand.review", "firm_banned"),
        ("jobs.demand.review", "malformed_markers"),
        ("jobs.stages.extract", "not_english"),
        ("jobs.stages.extract", "glyph_junk"),
        ("jobs.stages.extract", "thin"),
        ("jobs.stages.extract", "garbled"),
        ("jobs.stages.extract", "_ocr_tells"),
        ("jobs.litigation.extract", "garbled"),
        ("jobs.wording", "fill"),
        ("jobs.wording", "subjects"),
        ("jobs.wording", "names_records_differ"),
    ]
    for mod, fn in one_text:
        for label, text in (("letter", LETTER), ("garbled", GARBLED)):
            calls[f"{mod}.{fn}({label})"] = lambda mod=mod, fn=fn, text=text: kit.target(mod, fn)(text)
    checks = kit.module("jobs.proof.checks")
    calls["jobs.proof.checks.money_not_in(letter, sources)"] = lambda: checks.money_not_in(LETTER, SOURCES)
    calls["jobs.proof.checks.dates_not_in(letter, sources)"] = lambda: checks.dates_not_in(LETTER, SOURCES)
    calls["jobs.proof.checks.future(dates(letter))"] = lambda: checks.future(checks.dates(LETTER), dt.date(2026, 11, 1))
    calls["jobs.proof.checks.fmt_cents"] = lambda: [checks.fmt_cents(c) for c in (0, 5, 100, 123456789, -250)]
    calls["jobs.proof.review.parse(review)"] = lambda: kit.target("jobs.proof.review", "parse")(REVIEW, (1, 2, 3, 4))
    cit = kit.module("jobs.drafting.citations")
    calls["jobs.drafting.citations.kind_of"] = lambda: [
        cit.kind_of(n, LETTER)
        for n in ("Example Clinic records.pdf", "Police Report.pdf", "Witness depo.pdf", "notes.txt")
    ]
    calls["jobs.drafting.citations.apply(letter)"] = lambda: cit.apply(LETTER, policy, texts)
    calls["jobs.drafting.citations.check(letter)"] = lambda: cit.check(LETTER, policy, texts)
    calls["jobs.demand.review.wording(letter)"] = lambda: demand_review.wording({"Demand.docx": LETTER})
    calls["jobs.stages.review._system_findings(letter)"] = lambda: st_review._system_findings("letter", LETTER)
    calls["jobs.stages.review._as_date"] = lambda: [
        st_review._as_date(s) for s in ("03/14/2024", "02/30/2024", "3/1/24", "")
    ]
    calls["jobs.stages.review._date_forms"] = lambda: st_review._date_forms(dt.date(2024, 3, 4))
    calls["jobs.stages.review.heading_findings(entries)"] = lambda: st_review.heading_findings(
        [(d, p) for d, p, _b in ENTRIES] + [("13/45/2024", "")]
    )
    calls["jobs.litigation.extract.strip_rtf"] = lambda: lit.strip_rtf(RTF)
    calls["jobs.litigation.extract.rtf_text"] = lambda: lit.rtf_text(RTF.encode())
    calls["jobs.litigation.extract.html_text"] = lambda: lit.html_text(HTML)
    calls["jobs.negotiation.text.html_text"] = lambda: neg.html_text(HTML)
    calls["jobs.litigation.extract.ocr_pages"] = lambda: lit.ocr_pages(PAGES)
    calls["jobs.negotiation.text.ocr_pages"] = lambda: neg.ocr_pages(PAGES)
    # Word, PDF: the readers each lane has of its own.
    calls["jobs.stages.review.docx_text(docx)"] = lambda: st_review.docx_text(docx_path)
    calls["jobs.stages.review.entries(docx)"] = lambda: st_review.entries(docx_path)
    calls["jobs.stages.review.entry_bodies(docx)"] = lambda: st_review.entry_bodies(docx_path)
    calls["jobs.stages.extract.docx_text(docx)"] = lambda: st_extract.docx_text(docx_path)
    calls["jobs.demand.review.docx_text(docx)"] = lambda: demand_review.docx_text(docx_path)
    calls["jobs.litigation.extract.docx_text(docx)"] = lambda: lit.docx_text(docx_bytes)
    calls["jobs.negotiation.text.docx_text(docx)"] = lambda: neg.docx_text(docx_bytes)
    calls["jobs.stages.extract.pdf_text(pdf)"] = lambda: st_extract.pdf_text(pdf_path)
    calls["jobs.litigation.extract.pdf_text(pdf)"] = lambda: lit.pdf_text(pdf, None)
    calls["jobs.negotiation.text.pdf_text(pdf)"] = lambda: neg.pdf_text(pdf, None)
    calls["jobs.litigation.extract.pdf_text(text-layer pdf)"] = lambda: lit.pdf_text(text_pdf, None)
    calls["jobs.negotiation.text.pdf_text(text-layer pdf)"] = lambda: neg.pdf_text(text_pdf, None)
    out: dict[str, Any] = {}
    for name, fn in sorted(calls.items()):
        try:
            out[name] = {"out": gk.plain(fn())}
        except Exception as exc:  # noqa: BLE001 - a checker that raises on the sample is a fact
            out[name] = {"raises": f"{type(exc).__name__}: {exc}"}
    return out


def test_every_target_has_a_golden_and_an_allowed_list() -> None:
    files = {p.stem for p in DIR.glob("jobs.*.json")}
    assert files == set(MODULES), "run text_rule_harvest.py: a target module has no golden"
    assert set(MODULES) | {"synthetic"} <= set(ALLOWED) - {"_why"}
    for mod in MODULES:
        doc = gk.read_json(DIR / f"{mod}.json")
        assert set(doc) == {fn for m, fn in kit.TARGETS if m == mod}, mod


def test_every_checker_was_fed_by_the_tests_or_the_sample() -> None:
    """A checker no test feeds and the sample does not reach pins nothing."""
    sample = gk.read_json(DIR / "synthetic.json")
    for mod, fn in kit.TARGETS:
        fed = gk.read_json(DIR / f"{mod}.json")[fn]
        assert fed or any(k.startswith(f"{mod}.{fn}") for k in sample), f"{mod}.{fn} has no input"


@pytest.mark.parametrize("mod", MODULES)
def test_a_checker_answers_its_test_corpus_as_the_golden_holds(mod: str) -> None:
    """MUTATION: change a date pattern, a banned word, a money rule, a citation
    kind, a garbled-text threshold or an RTF/HTML stripper and the module's
    replay fails, naming the checker and the input's index."""
    path = DIR / f"{mod}.json"
    stored = gk.read_json(path)
    got = {
        fn: [{"args": c["args"], "kwargs": c["kwargs"], **kit.call(mod, fn, c)} for c in calls]
        for fn, calls in stored.items()
    }
    bad = gk.write_or_compare(path, got, ALLOWED[mod])
    assert bad == [], f"{mod}: {bad[:20]}"


def test_every_checker_answers_the_sample_deliverable_as_the_golden_holds(tmp_path: Path) -> None:
    bad = gk.write_or_compare(DIR / "synthetic.json", gk.scrub_tmp(synthetic(tmp_path), tmp_path), ALLOWED["synthetic"])
    assert bad == [], bad[:20]


def test_the_stored_inputs_round_trip() -> None:
    """The store keeps each input's type: a tuple stays a tuple, a date a date."""
    for value in (("a", 1), {"k": [dt.date(2024, 3, 14)]}, frozenset({"x"}), b"\x00\x01", [None, True, 1.5]):
        assert kit.decode(kit.encode(value)) == value


def test_a_planted_date_rule_fails_the_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: the US date form no longer read (``proof.checks._D_US``), and
    the replay of the dates the tests fed it differs."""
    import re

    checks = kit.module("jobs.proof.checks")
    monkeypatch.setattr(checks, "_D_US", re.compile(r"(?!x)x"))
    stored = gk.read_json(DIR / "jobs.proof.checks.json")
    got = {fn: [{**c, **kit.call("jobs.proof.checks", fn, c)} for c in calls] for fn, calls in stored.items()}
    assert any(p.startswith("dates.") for p in gk.compare(DIR / "jobs.proof.checks.json", got))
