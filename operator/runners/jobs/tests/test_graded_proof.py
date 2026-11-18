"""Proof 15 (2026-11-17): every paragraph of the filed chronology is graded
and SUPPORTED, delivered byte for byte, or a listed label, and the bytes
filed are the bytes proven. Record-only: it never fails a run. Each check
here is paired with the mutation that makes it record. Every name, date and
value is invented."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import update_testkit as K
from jobs import wording
from jobs.medchron import config as config_mod, graded_proof as GP, job as job_mod
from jobs.medchron.audit import claims as CL
from jobs.medchron.update import body as B, collect as C
from jobs.stages import upload
from jobs.stages.base import StageRun

NEW = (
    "02/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    "MRI showed a disc protrusion measuring 3 mm at the fifth and sixth level. (Exhibit 2 - p. 2)\n\n"
    "All Other Information\nFollow up with the spine clinic was advised after the imaging. (Exhibit 2 - p. 3)"
)
NEX = [{"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 3}]


def _md(entries: list[str]) -> str:
    return C.audit_document(K.base_md(), entries, NEX)


def _rows(md: str, verdict: str = "SUPPORTED") -> list[dict]:
    body = CL.body_of(md)
    return [{"key": c["key"], "kind": "real", "verdict": verdict} for c in CL.extract_claims(body, B.cited(body))]


def _check(md: str, rows: list[dict], **kw) -> GP.Result:
    return GP.check(CL.body_of(md), GP.latest_supported(rows), headings=K.HEADINGS, **kw)


def test_the_segments_carry_the_audits_own_claim_keys() -> None:
    for md in (_md([NEW]), K.base_md(), _md([NEW, K.ENTRY_PRE.replace("Exhibit 1", "Exhibit 2")])):
        body = CL.body_of(md)
        want = {c["key"] for c in CL.extract_claims(body, B.cited(body))}
        assert {s.key for s in GP.segments(body) if s.key} == want


def test_a_supported_document_records_nothing() -> None:
    md = _md([NEW])
    r = _check(md, _rows(md))
    assert GP.codes(r) == {"P15_unsupported_claim": 0, "P15_ungraded_text": 0} and r.paragraphs > 0


def test_an_ungraded_sentence_appended_after_the_proofs_is_recorded() -> None:
    md = _md([NEW])
    rows = _rows(md)
    later = md.replace(
        "after the imaging. (Exhibit 2 - p. 3)", "after the imaging. (Exhibit 2 - p. 3) The patient is improving."
    )
    assert later != md
    assert GP.codes(_check(later, rows))["P15_ungraded_text"] == 1


def test_a_claim_without_a_supported_row_is_recorded() -> None:
    md = _md([NEW])
    assert GP.codes(_check(md, _rows(md, "UNSUPPORTED")))["P15_unsupported_claim"] == 2
    rows = _rows(md)
    rewritten = md.replace("3 mm", "4 mm")  # a claim's words changed after its verdict
    assert GP.codes(_check(rewritten, rows))["P15_unsupported_claim"] == 1


def test_labels_are_accepted_and_named() -> None:
    noted = NEW + f"\n\n{wording.NTD_ILLEGIBLE} (Exhibit 2 - p. 3)"
    md = _md([noted])
    r = _check(md, _rows(md))
    assert GP.codes(r) == {"P15_unsupported_claim": 0, "P15_ungraded_text": 0}
    assert r.accounted[GP.LABELS[3]] == 1 and r.accounted[GP.LABELS[0]] == 1  # the note; the entry's date line


def test_delivered_entries_need_no_row_and_their_edit_is_recorded() -> None:
    md = K.base_md()
    _, ents = B.entries_of(md)
    pre = B.entries_of(md)[0]
    delivered = [*ents, *[p.strip() for p in pre.split("\n\n")]]
    assert GP.codes(_check(md, [], delivered=delivered))["P15_unsupported_claim"] == 0
    edited = md.replace("recheck of the neck", "recheck of the necks")
    # the edited entry is no longer the delivered one: both its claims are now unproven
    assert GP.codes(_check(edited, [], delivered=delivered))["P15_unsupported_claim"] == 2


# ---- the filed bytes ------------------------------------------------------------------------


def _sr(job_dir: Path, firm: Path, data_root: Path, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: None,  # type: ignore[arg-type, return-value]
        date_stamp="11-17-26",
    )


def _fresh(sr: StageRun) -> Path:
    rd, out = sr.slug_dir / "runs" / "alpha", sr.slug_dir / "out" / "alpha"
    rd.mkdir(parents=True)
    out.mkdir(parents=True)
    md = _md([NEW])
    (rd / "final-chronology.md").write_text(md)
    (out / "audit-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _rows(md)))
    doc = out / "Alpha Example - Medical Chronology 11-17-26.docx"
    doc.write_bytes(b"the rendered chronology")
    return doc


def _manifest(sr: StageRun, doc: Path) -> list[dict]:
    m = [{"name": doc.name, "sha256": hashlib.sha256(doc.read_bytes()).hexdigest()}]
    (sr.slug_dir / "out" / "alpha" / "upload_manifest.json").write_text(json.dumps(m))
    return m


def _prove(sr: StageRun, doc: Path) -> Path:
    """A full run's proofs file, written with ``graded_proof.write`` as the
    upload reads it: proof 15's findings on the chronology the review left,
    and the filed document's sha256."""
    rd = sr.slug_dir / "runs" / "alpha"
    md = (rd / "final-chronology.md").read_text()
    GP.write(rd / GP.PROOFS, GP.codes(_check(md, _rows(md))), {"name": doc.name, "sha256": GP.file_sha(doc)})
    return rd / GP.PROOFS


def test_the_docx_rewritten_after_the_proof_is_recorded_at_upload(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, log)
    doc = _fresh(sr)
    proofs = _prove(sr, doc)
    assert GP.record_upload(proofs, _manifest(sr, doc), log.append) is None  # the bytes proven
    doc.write_bytes(b"the chronology, rewritten after the proof")
    assert GP.record_upload(proofs, _manifest(sr, doc), log.append) == GP.SHA_CHANGED
    assert GP.recorded(sr.slug_dir, "alpha")[GP.SHA_CHANGED] == 1
    upload._record_proven(sr)  # the upload stage's own call: still recorded once, never refused
    assert GP.recorded(sr.slug_dir, "alpha")[GP.SHA_CHANGED] == 1


def test_no_proof_on_record_compares_nothing(tmp_path: Path) -> None:
    log: list[str] = []
    assert GP.record_upload(tmp_path / "proofs.json", [{"name": "x", "sha256": "y"}], log.append) is None
    assert log == [] and GP.recorded(tmp_path, "alpha") == {}
    # SAFE ALONE: the update proofs' own file, with no final proof registered, is left byte for byte
    own = tmp_path / "update-proofs.json"
    own.write_text(json.dumps({"lines": ["1. ok"], "fails": []}, indent=1))
    before = own.read_bytes()
    assert GP.record_upload(own, [{"name": "x", "sha256": "y"}], log.append) is None
    assert own.read_bytes() == before and log == []
