"""What a paid stage is projected to add, from THIS matter's own artifacts,
for the driver's limits (``driver._projection``). Split out of ``driver.py``
so that module stays under the size ceiling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import budget


def claims_to_verify(slug_dir: Path, unit: str) -> int | None:
    """Claims in the built chronology STILL TO VERIFY, counted with the
    audit gate's OWN extractor, so the projection counts what the audit
    will actually call on rather than a remembered ratio from some other
    matter. The audit resumes by key, so a claim with a real verdict on
    disk for this very body costs nothing again: live 2026-09-16 a resume
    with 1,998 verdicts already paid for was held at the cap on a
    projection that re-counted all 1,998. None before the chronology
    exists: no document is "no count yet", not zero claims (a rehearsal
    that stops before build_doc would otherwise print the costliest late
    stage as $0)."""
    from .audit import claims as claims_mod
    from .audit.page_text import exhibit_paths

    doc = slug_dir / "runs" / unit / "final-chronology.md"
    if not doc.is_file():
        return None
    out = slug_dir / "out" / unit
    body = claims_mod.body_of(doc.read_text(encoding="utf-8"))
    claims = claims_mod.extract_claims(body, set(exhibit_paths(out)))
    sha = claims_mod.doc_sha_of(body)
    results = out / "audit-results.jsonl"
    rows = claims_mod.read_rows(results) if results.is_file() else []
    verified = {r["key"] for r in rows if r.get("kind") == "real" and r.get("doc_sha") == sha}
    verified -= claims_mod.pending_reaudit(rows)  # a stale verdict the next round pays for again
    return sum(1 for c in claims if c["key"] not in verified)


def update_review_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """The update review loop's projection (``stages/update_review``)."""
    from .stages import update_review

    return update_review.projected_usd(slug_dir, unit, cfg, pricing)
