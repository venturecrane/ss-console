"""An update re-reads nothing the delivered run already paid to read.

WHY (2026-10-25). An update reads the files a delivered chronology did not
cite. The delivered run had already transcribed every one of them: on the
first real update request, 158 of the 222 requested files were scans whose
pages the delivered run had sent to the transcription model, checkpointed in
its ``partial/<id>.jsonl``. ``update_pages`` skips a page whose PICTURE is a
delivered exhibit page, but an uncited file is in no exhibit, so every one of
those pages would have been paid for twice.

The rule. Before ``vision`` gathers, an update seeds its own checkpoint for a
queued document from a DELIVERED job on the SAME matter, still on the volume,
whose copy of the document has the SAME sha256 (``raw_manifest.jsonl``). Only
pages that were read are copied: a failed or refused page is asked again. The
newest such job wins. Nothing is seeded for a document that already has a
checkpoint (a resume), for a run that is not an update, or when the hashes
cannot both be read: a page whose source cannot be proven the same is paid
for, never assumed.

Job dirs are wiped 72 hours after a job ends, so this reuses a read only
within that window; outside it the update pays, as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import StageRun, read_jsonl

#: The page texts that were never a reading (``vision.FAILED`` / ``REFUSED``).
NOT_READ = frozenset({"[page transcription failed]", "[page transcription refused]"})


def _hashes(slug_dir: Path) -> dict[str, str]:
    return {
        str(r["id"]): str(r["sha256"])
        for r in read_jsonl(slug_dir / "raw_manifest.jsonl")
        if r.get("ok") and r.get("id") and r.get("sha256")
    }


def delivered_same_matter(jobs: Path, matter_id: str, own: Path) -> list[Path]:
    """The slug dirs of delivered jobs on ``matter_id``, newest first."""
    import yaml

    out: list[tuple[str, Path]] = []
    for jd in jobs.iterdir() if jobs.is_dir() else ():
        try:
            if jd.resolve() == own.resolve():
                continue
            st = json.loads((jd / "daemon.json").read_text(encoding="utf-8"))
            y = yaml.safe_load((jd / "job.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if st.get("state") != "delivered" or str((y.get("matter") or {}).get("id") or "") != matter_id:
            continue
        slug = str(y.get("slug") or "")
        sd = jd / "data" / slug
        if slug and sd.is_dir():
            out.append((str(st.get("finished_at") or ""), sd))  # ISO-8601 UTC: sorts as text
    out.sort(key=lambda t: (t[0], str(t[1])), reverse=True)
    return [sd for _, sd in out]


def seed(sr: StageRun, queue: list[dict[str, Any]]) -> int:
    """Seed this update's checkpoints; returns the pages seeded."""
    if getattr(sr.job, "update", None) is None:
        return 0
    d = sr.slug_dir
    want = [str(r["id"]) for r in queue if not (d / "partial" / f"{r['id']}.jsonl").is_file()]
    if not want:
        return 0
    mine = _hashes(d)
    jobs = Path(sr.job.install_root) / "jobs"
    pages = files = 0
    left = set(want)
    for src in delivered_same_matter(jobs, str(sr.job.matter_id), Path(sr.job.path).parent):
        theirs = _hashes(src)
        for doc in sorted(left):
            part = src / "partial" / f"{doc}.jsonl"
            if not (mine.get(doc) and mine.get(doc) == theirs.get(doc) and part.is_file()):
                continue
            rows = [r for r in read_jsonl(part) if isinstance(r.get("page"), int) and r.get("text") not in NOT_READ]
            if not rows:
                continue
            dest = d / "partial" / f"{doc}.jsonl"
            dest.write_text("".join(json.dumps({"page": r["page"], "text": r["text"]}) + "\n" for r in rows), "utf-8")
            pages += len(rows)
            files += 1
            left.discard(doc)
        if not left:
            break
    if files:
        sr.log(f"vision: {pages} page(s) of {files} document(s) reused from the delivered run's reading (same sha256)")
    return pages
