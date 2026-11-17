"""`final_proof`: proof 15 on a full chronology, after the pre-delivery
review and before the upload. $0, RECORD-ONLY: it never fails or holds a run.

It reads the chronology the review left (``runs/<unit>/final-chronology.md``,
the text the filed document renders) and accounts for every paragraph
(``medchron/graded_proof``: graded and SUPPORTED, or a listed label), then
writes the findings and the filed document's sha256 into
``runs/<unit>/proofs.json``. The upload records a filing whose chronology
bytes are not the ones proven (``graded_proof.record_upload``).
"""

from __future__ import annotations

from ..medchron import graded_proof as GP
from ..medchron.audit import claims as CL
from .base import StageRun
from .render import chronology_name
from .update_base import headings


def run(sr: StageRun) -> int:
    rd, out = sr.slug_dir / "runs" / sr.unit.unit, sr.slug_dir / "out" / sr.unit.unit
    doc = rd / "final-chronology.md"
    if not doc.is_file():
        sr.log("final proof: no chronology to read; nothing recorded")
        return 0
    res = GP.check(
        CL.body_of(doc.read_text(encoding="utf-8")),
        GP.latest_supported(CL.read_rows(out / "audit-results.jsonl")),
        headings=headings(sr.cfg),
    )
    codes = GP.codes(res)
    filed = out / chronology_name(sr)
    GP.write(rd / GP.PROOFS, codes, {"name": filed.name, "sha256": GP.file_sha(filed)})
    found = ", ".join(f"{k}={v}" for k, v in codes.items() if v) or "none"
    sr.log(f"final proof (recorded, never fails the run): {res.paragraphs} paragraph(s) read; findings: {found}")
    return 0


__all__ = ["run"]
