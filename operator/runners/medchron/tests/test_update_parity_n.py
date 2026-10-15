"""Parity N: no paragraph of an added entry reaches the firm unaudited.

On a real update (2026-10-14) compose wrote a lab panel as a run of
paragraphs and cited only the last. The audit grades the paragraph a citation
closes, so the earlier one, which restated the panel with a value misread (a
comma inside a three-digit number) and a sentence about how hard the flags
were to align, was graded by nothing and reached the delivered chronology.
The update review caught it every round and asked to drop it, but a drop
applied only to a claim anchor; the paragraph had none, and the job held.

N1. Collect cites every uncited paragraph of an added entry to the page that
    closes its run, so the audit grades it (a wrong value is repaired or
    dropped by the audit's own rules; a supported one is kept, cited).
N2. A paragraph the review sees uncited has an anchor (``N<k>.u<j>``), and a
    drop or a grounded replace on it applies.

Every value is invented; each test fails on the branch head before parity N.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import update_testkit as K
from medchron.audit import claims as CL
from medchron.stages import update_collect as UC
from medchron.update import review_repair as RR

PROV = "Sample Laboratory"
HEAD = f"03/11/2026\n{PROV} | Medical Diagnoses\n\n"
WRONG = (
    'Metabolic panel:\nGlucose was 105. Alkaline phosphatase was printed as "1,07." '
    "Flags for the remaining components are not legibly aligned with them."
)
CITED = "The metabolic panel recorded glucose 105 and alkaline phosphatase 107, both flagged high. (Exhibit 2 - p. 3)"
SUPPORTED = "Blood count:\nHemoglobin was 4.7 with a critical low flag, verified by repeat analysis."
COUNTS = "The differential recorded neutrophils 66 percent and lymphocytes 22 percent. (Exhibit 2 - p. 4)"


def _shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[str]) -> tuple[list[str], dict]:
    """The real collect shaping (`update_collect._shape`) over composed `entries`."""
    sd = tmp_path / "matter"
    (sd / "text").mkdir(parents=True)
    (sd / "text" / "n2.txt").write_text("".join(f"[p.{i}]\nSample lab report page {i}\n" for i in range(1, 6)))
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    sr = SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha"),
        job=SimpleNamespace(incident_date=K.INCIDENT),
        cfg=SimpleNamespace(get=lambda *_a: sorted(K.HEADINGS)),
    )
    monkeypatch.setattr(UC.C, "composed", lambda _rd: entries)
    monkeypatch.setattr(UC.C, "collect", lambda comp, _base, _inc, _canon: (list(comp), {}))
    monkeypatch.setattr(UC, "Canon", lambda _cfg: None)
    return UC._shape(
        sr, K.base_md(), sd / "runs" / "alpha", [{"exhibit": 2, "id": "n2", "title": "Exhibit 2 - Sample"}]
    )  # type: ignore[arg-type]


def _claims(entries: list[str]) -> list[dict]:
    return CL.extract_claims("\n\n".join(entries), {2})


def _audited(entries: list[str], fragment: str) -> dict | None:
    """The audited claim whose text carries `fragment`, or None."""
    return next((c for c in _claims(entries) if fragment in c["claim"]), None)


# ---- N1 -------------------------------------------------------------------------
def test_an_uncited_paragraph_with_a_wrong_value_is_audited_never_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = HEAD + "Medical Diagnoses\n" + WRONG + "\n\n" + CITED
    entries, rep = _shape(tmp_path, monkeypatch, [entry])
    got = _audited(entries, '"1,07."')
    # The misread value and the sentence about our reading are now a claim the
    # audit grades, against the page that closes the panel's run.
    assert got is not None and got["exhibit"] == 2 and got["page_spec"] == "3", entries
    assert "not legibly aligned" in got["claim"]
    # Nothing of the entry's body is left outside an audited claim.
    spans = "".join(CL.claim_spans("\n\n".join(entries), {2}).values())
    for line in entries[0].split("\n")[2:]:
        assert not line.strip() or line.strip() in K.HEADINGS or line in spans, line
    assert len(rep["uncited_paragraphs"]) == 1 and rep["uncited_paragraphs"][0]["cite"] == "(Exhibit 2 - p. 3)"
    assert any("uncited paragraph" in ln for ln in UC.report_lines(rep))


def test_text_after_a_citation_and_a_short_fragment_are_audited_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = (
        HEAD + "Medical Diagnoses\n"
        "Potassium was 4.9 on the panel. (Exhibit 2 - p. 3). Chloride was printed as 1.10 on that page.\n\n"
        "Calcium 8.5.\n\n" + COUNTS
    )
    entries, _ = _shape(tmp_path, monkeypatch, [entry])
    assert _audited(entries, "Chloride was printed as 1.10") is not None, entries
    short = _audited(entries, "Calcium 8.5.")
    assert short is not None and short["page_spec"] == "4", entries


def test_an_uncited_paragraph_the_page_supports_is_kept_whole_and_cited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = HEAD + "Medical Diagnoses\n" + SUPPORTED + "\n\n" + COUNTS + "\n\n" + CITED
    entries, _ = _shape(tmp_path, monkeypatch, [entry])
    text = entries[0]
    # Every word and value kept, now closed by the citation of its run.
    assert SUPPORTED + " (Exhibit 2 - p. 4)" in text, text
    got = _audited(entries, "Hemoglobin was 4.7")
    assert got is not None and got["page_spec"] == "4"
    # No page the entry did not already cite becomes cited.
    before = {(m.group(1), m.group(2)) for m in CL.CITE.finditer(entry)}
    assert {(m.group(1), m.group(2)) for m in CL.CITE.finditer(text)} == before


# ---- N2 -------------------------------------------------------------------------
def _md(entry: str) -> str:
    return (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        f"## Medical Chronology\n\n{entry}\n\n## Exhibit List\n"
    )


def _finding(action: str, corrected: str = "") -> RR.Finding:
    return RR.Finding(
        RR.MUST, "6 process wording", "N1.u1", 2, 3, "repeats the cited panel with a misread value",
        action, corrected, "the cited claim states the panel as printed",
    )  # fmt: skip


def test_a_review_drop_on_an_uncited_paragraph_applies(tmp_path: Path) -> None:
    md = _md(HEAD + "Medical Diagnoses\n" + WRONG + "\n\n" + CITED)
    ents = RR.anchor_entries(md, {2}, K.HEADINGS)
    assert [c.anchor for c in ents[0].uncited] == ["N1.u1"] and '"1,07."' in ents[0].uncited[0].text
    f = _finding("drop")
    new, edited = RR.apply(md, [f], {2}, K.HEADINGS)
    assert f.outcome == "applied", f.outcome
    assert '"1,07."' not in new and "not legibly aligned" not in new and CITED in new
    assert new.index("Medical Diagnoses") < new.index(CITED)  # the heading stays over what remains
    assert edited == {0} and "105" in f.removed


def test_a_grounded_replace_on_an_uncited_paragraph_applies(tmp_path: Path) -> None:
    md = _md(HEAD + "Medical Diagnoses\n" + WRONG + "\n\n" + CITED)
    fixed = "Glucose was 105 and alkaline phosphatase was 107 on the panel. (Exhibit 2 - p. 3)"
    f = _finding("replace", fixed)
    new, _ = RR.apply(md, [f], {2}, K.HEADINGS)
    assert f.outcome == "applied", f.outcome
    assert fixed in new and '"1,07."' not in new
    # A replace that ends in no citation would leave the text uncited: refused.
    g = _finding("replace", "Glucose was 105 and alkaline phosphatase was 107 on the panel.")
    RR.apply(md, [g], {2}, K.HEADINGS)
    assert g.outcome != "applied"
