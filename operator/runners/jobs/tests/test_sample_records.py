"""The fictional record sets and the sample chronology built from them.

What these prove, in order of how much rests on them:

* every citation in the sample chronology the PRODUCTION stages wrote lands on
  a page that prints the fact the paragraph states, by way of the real
  exhibit page map (so a demo never shows a citation that points nowhere);
* the build spends nothing: every stage runs with a client that raises;
* the deliberate defects are in the records and the pipeline shows them
  (a missing bill, a date-of-service conflict, an illegible page, a misfiled
  page, a treatment gap, a prior injury, re-sent pages, handwriting);
* the records are byte-stable and near their page targets.

The full builds take about a minute together; one module-scoped fixture builds
each matter once.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from jobs.medchron.sample import answer_key, pdf, pipeline, spec

SPECS = Path(__file__).resolve().parents[3] / "customers" / "pilot-smokeball" / "seed" / "records"
MATTERS = ["chen-pi102", "ramirez-pi103", "whitfield-pi104"]


def _plan(slug: str) -> spec.Plan:
    return spec.load(SPECS / f"{slug}.yaml")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, pipeline.Build]:
    root = tmp_path_factory.mktemp("sample")
    return {slug: pipeline.build(_plan(slug), root / slug) for slug in MATTERS}


def _final(b: pipeline.Build) -> str:
    return (b.slug_dir / "runs" / b.unit / "final-chronology.md").read_text(encoding="utf-8")


def _page_map(b: pipeline.Build) -> list[dict]:
    return json.loads((b.out_dir / "page_map.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("slug", MATTERS)
def test_every_citation_lands_on_the_page_that_prints_its_fact(built: dict, slug: str) -> None:
    """Through the real exhibit page map: exhibit page -> matter file page ->
    the words printed there. Every authored paragraph arrives, cited right."""
    b = built[slug]
    assert b.problems == []
    cited = answer_key.cited_paragraphs(_final(b))
    assert len(cited) == len(answer_key.expected_citations(b.plan)) > 0


@pytest.mark.parametrize("slug", MATTERS)
def test_no_fax_cover_or_certification_lands_in_an_exhibit(built: dict, slug: str) -> None:
    """The strip ran as in production: every page left in a delivered exhibit
    is a record page, read off the exhibit PDF itself and traced to the spec
    through the page map and the strip's own drops."""
    from jobs.stages.classify import resolve_exhibit_files

    b = built[slug]
    by_name = {d.file_name: d for d in b.plan.documents}
    drops = pipeline.strip_drops(b)
    stripped = 0
    for ex, path in resolve_exhibit_files(b.out_dir).items():
        with pymupdf.open(str(path)) as pdfdoc:
            n = len(pdfdoc)
        for page in range(1, n + 1):
            src = answer_key.source_page(_page_map(b), ex, answer_key.unstripped_page(drops, ex, page))
            assert src is not None
            kind = by_name[src[0]].pages[src[1] - 1].kind
            assert kind not in ("fax_cover", "custodian_cert"), f"Exhibit {ex} p. {page} is a {kind}"
        before, after = pipeline.exhibit_pages(b)[str(ex)]
        assert n == after < before
        stripped += before - after
    wrappers = sum(
        1
        for e in _page_map(b)
        for f in e["files"]
        for p in by_name[f["file"]].pages
        if p.kind in ("fax_cover", "custodian_cert")
    )
    assert stripped == wrappers > 0, "every cover and certification in an exhibited file was removed, nothing else"


def test_the_score_can_fail(built: dict) -> None:
    """A check that cannot fail measures nothing: move one citation a page and
    replace one paragraph, and the score names both."""
    b = built["ramirez-pi103"]
    text = _final(b)
    _day, para, exhibit, page = answer_key.cited_paragraphs(text)[0]
    shifted = text.replace(f"(Exhibit {exhibit} - p. {page})", f"(Exhibit {exhibit} - p. {page + 1})", 1)
    assert any("cites" in p for p in answer_key.score(b.plan, shifted, _page_map(b), pipeline.strip_drops(b)))
    swapped = answer_key.score(
        b.plan, text.replace(para, "A sentence no record states.", 1), _page_map(b), pipeline.strip_drops(b)
    )
    assert any("not in the answer key" in p for p in swapped) and any("lost" in p for p in swapped)


@pytest.mark.parametrize("slug", MATTERS)
def test_the_build_spent_nothing_and_every_stage_passed(built: dict, slug: str) -> None:
    b = built[slug]
    assert all(rc == 0 for rc in b.verdicts.values()), b.verdicts
    ledgers = list(b.slug_dir.rglob("usage-ledger*.jsonl"))
    assert all(not p.read_text(encoding="utf-8").strip() for p in ledgers)
    names = {p.name for p in b.out_dir.iterdir()}
    assert any("Medical Chronology" in n for n in names)
    assert any("Medical Billing Worksheet" in n for n in names)
    with pytest.raises(pipeline.SpendAttempted):
        getattr(pipeline.NoSpendClient(), "messages")


@pytest.mark.parametrize("slug", MATTERS)
def test_page_count_is_within_ten_percent_of_target(slug: str) -> None:
    plan = _plan(slug)
    target = int(plan.matter["target_pages"])
    assert abs(plan.total_pages() - target) <= target * 0.10, plan.total_pages()
    scanned = sum(1 for d in plan.documents for p in d.pages if p.scan)
    assert 0.6 <= scanned / plan.total_pages() <= 0.8, "about 70% of pages arrive as image-only scans"
    resent = sum(len(d.pages) for d in plan.documents if d.resend_of)
    assert 0.12 <= resent / plan.total_pages() <= 0.30, "about a fifth of the pages are re-sent copies"


def test_records_are_byte_stable_and_scans_have_no_text_layer() -> None:
    plan_a, plan_b = _plan("ramirez-pi103"), _plan("ramirez-pi103")
    for da, db in zip(plan_a.documents, plan_b.documents):
        a, b = pdf.render(plan_a, da), pdf.render(plan_b, db)
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), da.name
    doc = plan_a.doc("vpa")
    with pymupdf.open(stream=pdf.render(plan_a, doc)) as rendered:
        for page, pg in zip(rendered, doc.pages):
            assert bool(page.get_text().strip()) is (not pg.scan), f"page {pg.number}"


def test_whitfield_defects(built: dict) -> None:
    b = built["whitfield-pi104"]
    chart = json.loads((b.slug_dir / "billing_chart.json").read_text(encoding="utf-8"))
    rows = {r["provider"]: r for r in chart["rows"]}
    assert rows["Sacramento Metropolitan Fire District"]["total"] is None, "the missing ambulance bill shows as a gap"
    assert chart["grand_total"] == 25430.00, "the worksheet subtotal is the firm's billing summary total"
    dos = (b.root / "build.log").read_text(encoding="utf-8")
    assert "MISSED VISIT 2026-02-11" in dos, "the date-of-service conflict between note and bill is surfaced"
    ill = b.plan.doc("kaiser-ed").pages[[p.pid for p in b.plan.doc("kaiser-ed").pages].index("ked-illegible")]
    assert (
        ill.illegible and ill.scan and answer_key.transcription(b.plan, b.plan.doc("kaiser-ed"), ill) == "[illegible]"
    )
    assert "A portion of the records were handwritten and illegible" in _final(b)
    prior = next(p for p in b.plan.doc("kaiser-pcp").pages if p.pid == "kpcp-1")
    assert any("Lumbar strain in 2019" in line for line in prior.lines)


def test_chen_misfiled_page_is_another_patient_and_never_cited(built: dict) -> None:
    b = built["chen-pi102"]
    doc = b.plan.doc("okonkwo")
    page = next(p for p in doc.pages if p.pid == "ooa-misfiled")
    text = answer_key.transcription(b.plan, doc, page)
    assert "Robert Chang" in text and "Patient: Robert Chen" not in text
    cited = {(c["file"], c["page"]) for c in answer_key.expected_citations(b.plan)}
    assert (doc.file_name, page.number) not in cited


def test_ramirez_gap_and_resent_pages(built: dict) -> None:
    b = built["ramirez-pi103"]
    log = (b.root / "build.log").read_text(encoding="utf-8")
    assert "1 gap bar(s)" in log, "the 63-day gap between 03/18 and 05/20 is drawn as a treatment gap"
    original, resent = b.plan.doc("vpa"), b.plan.doc("vpa-resend")
    assert resent.resend_of == "vpa"
    assert [p.lines for p in resent.pages[1:]] == [p.lines for p in original.pages[2:25]]


@pytest.mark.parametrize("slug", MATTERS)
def test_handwritten_pages_are_scans_marked_handwritten(slug: str) -> None:
    plan = _plan(slug)
    hand = [(d, p) for d in plan.documents for p in d.pages if p.hand]
    assert hand
    for d, p in hand:
        assert p.scan and answer_key.transcription(plan, d, p).startswith("[handwritten]")


def test_a_paragraph_whose_evidence_is_not_on_its_page_refuses() -> None:
    raw = __import__("yaml").safe_load((SPECS / "ramirez-pi103.yaml").read_text(encoding="utf-8"))
    raw["encounters"][0]["paras"][0][2] = "a phrase printed on no page"
    with pytest.raises(spec.SpecError):
        spec.expand(raw)
