"""Defects the $0 proof run on a preserved real job found in the evidence
path (2026-11-04), each pinned by a test that fails on the code before the
fix. Synthetic data only.

* two extracted rows with one name: the later overwrote the earlier, so its
  text was never read (on the real job 22 names covered 114 rows);
* a copied job dir whose rows name paths that no longer resolve read as an
  empty file, with no error;
* an excerpt on several other pages was pinned to the lowest of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evidence_testkit import CHECKER
from jobs.drafting import pincheck
from jobs.drafting.evidence import extract, verify
from jobs.drafting.evidence.shapes import Candidate, DocPages


@pytest.fixture(autouse=True)
def _gate_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


def _job(tmp: Path, rows: list[tuple[str, str, str]]) -> Path:
    """rows: (id, name, folder) with text written under data/text/<id>.txt."""
    data = tmp / "data"
    (data / "text").mkdir(parents=True)
    out = []
    for i, name, folder in rows:
        p = data / "text" / f"{i}.txt"
        p.write_text(f"[p.1]\nThe text of {i}, filed as {name}.", encoding="utf-8")
        out.append({"id": i, "name": name, "folder": folder, "pages": 1, "text_path": str(p)})
    (data / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    return data


def test_two_rows_with_one_name_are_two_documents_and_both_are_read(tmp_path):
    data = _job(
        tmp_path,
        [
            ("d1", "Letter", "Correspondence"),
            ("d2", "Letter", "Medical"),
            ("d3", "Letter", "Medical"),
            ("d4", "Bill", ""),
        ],
    )
    corp = extract.load_corpus(data)
    assert sorted(corp) == ["Bill", "Letter (Correspondence)", "Letter (Medical)", "Letter (Medical) #2"]
    texts = {d.pages[1] for d in corp.values()}
    assert len(texts) == 4, "every row's text is in the corpus; none overwrote another"
    assert len(extract.windows(corp, "m")) == 4


def test_a_copied_job_resolves_its_text_under_its_own_data_dir(tmp_path):
    data = _job(tmp_path / "orig", [("d1", "Chart", "Medical")])
    rows = [json.loads(ln) for ln in (data / "extracted.jsonl").read_text().splitlines()]
    copy = tmp_path / "copy" / "data"
    (copy / "text").mkdir(parents=True)
    (copy / "text" / "d1.txt").write_text((data / "text" / "d1.txt").read_text())
    (copy / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (data / "text" / "d1.txt").unlink()  # the stored path no longer resolves
    assert list(extract.load_corpus(copy)) == ["Chart"]


def test_a_row_whose_text_cannot_be_found_raises_never_reads_as_empty(tmp_path):
    data = _job(tmp_path, [("d1", "Chart", "Medical")])
    (data / "text" / "d1.txt").unlink()
    with pytest.raises(extract.ExtractError) as e:
        extract.load_corpus(data)
    assert "no text file" in str(e.value)


def test_an_excerpt_on_several_other_pages_is_ambiguous_and_rejected_never_pinned_low():
    words = "the patient reported neck pain radiating to the shoulder"
    doc = DocPages("Chart", {1: "Intake form.", 3: f"Visit one: {words}.", 7: f"Visit two: {words}."})
    [f] = list(verify.verify([Candidate("Chart", 1, "fact", words, "neck pain")], {"Chart": doc}))
    assert f.status == "rejected" and "ambiguous" in f.note
    one = DocPages("Chart", {1: "Intake form.", 7: f"Visit two: {words}."})
    [g] = list(verify.verify([Candidate("Chart", 1, "fact", words, "neck pain")], {"Chart": one}))
    assert g.status == "relocated" and g.page == 7
    on_cited = DocPages("Chart", {1: f"Intake: {words}.", 7: f"Visit two: {words}."})
    [h] = list(verify.verify([Candidate("Chart", 1, "fact", words, "neck pain")], {"Chart": on_cited}))
    assert h.status == "ok" and h.page == 1


def test_the_pin_check_resolves_a_cite_to_a_name_two_rows_share():
    words = "the patient reported neck pain radiating to the shoulder"
    named = [
        ("Letter (Correspondence)", "[p.1]\nThank you for your letter."),
        ("Letter (Medical)", f"[p.1]\nIntake.\n[p.2]\nAt the visit {words}."),
    ]
    pins, counts = pincheck.check(f'The record says "{words}" (FILE: Letter, p. 2).', named)
    assert counts.get("unresolved", 0) == 0 and pins[0].resolved == "Letter (Medical)"
    assert pins[0].status not in ("wrong_page", "wrong_file"), (pins[0].status, pins[0].why)
