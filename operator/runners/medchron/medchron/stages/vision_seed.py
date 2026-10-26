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


def prior_delivered(jobs: Path, matter_id: str, own_id: str) -> list[str]:
    """The ids of the delivered jobs on ``matter_id``, newest first.

    Called by the DAEMON (root) when it stages an update (``job.yaml``
    ``prior_delivered``): the child's uid can enter a job dir it is named but
    cannot list the jobs dir (0710 root:medchron). The first proof run of this
    code read the dir from the child and died on PermissionError (2026-10-25).
    An unreadable job is skipped; an unlistable dir gives none."""
    import yaml

    out: list[tuple[str, str]] = []
    try:
        dirs = [jd for jd in jobs.iterdir() if jd.is_dir()]
    except OSError:
        return []
    for jd in dirs:
        if jd.name == own_id:
            continue
        try:
            st = json.loads((jd / "daemon.json").read_text(encoding="utf-8"))
            y = yaml.safe_load((jd / "job.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if st.get("state") == "delivered" and str((y.get("matter") or {}).get("id") or "") == matter_id:
            out.append((str(st.get("finished_at") or ""), jd.name))  # ISO-8601 UTC: sorts as text
    out.sort(reverse=True)
    return [name for _, name in out]


def staged_fields(env: dict[str, Any], run_dir: Path, job_id: str) -> dict[str, Any]:
    """What the daemon adds to an update's job.yaml: ``prior_delivered``, from
    ``run_dir/jobs`` (not the daemon's ``jobs``: a proof twin's live elsewhere).
    Nothing for a run."""
    if env.get("update") is None:
        return {}
    return {"prior_delivered": prior_delivered(Path(run_dir) / "jobs", str(env["matter"]["id"]), job_id)}


def delivered_same_matter(job: Any, slug: str) -> list[Path]:
    """The ``slug`` dirs of the delivered jobs the daemon named in
    ``job.yaml``, newest first, read by path (never by listing)."""
    root = getattr(job, "install_root", None)
    if not root:
        return []
    jobs = Path(root) / "jobs"
    return [sd for jid in getattr(job, "prior_delivered", None) or [] if (sd := jobs / jid / "data" / slug).is_dir()]


def seed(sr: StageRun, queue: list[dict[str, Any]]) -> int:
    """Seed this update's checkpoints; returns the pages seeded."""
    if getattr(sr.job, "update", None) is None:
        return 0
    d = sr.slug_dir
    want = [str(r["id"]) for r in queue if not (d / "partial" / f"{r['id']}.jsonl").is_file()]
    if not want:
        return 0
    mine = _hashes(d)
    pages = files = 0
    left = set(want)
    for src in delivered_same_matter(sr.job, d.name):
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
