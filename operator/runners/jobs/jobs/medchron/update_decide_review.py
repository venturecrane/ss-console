"""SMD's correction of an update the PRE-DELIVERY review held (2026-11-22).

``update_decide`` (its module doc holds the act) corrects an update its own
independent review held, before the final set. An update can also pass that
review, run its final set, assemble, render, prove and build its package, and
then hold at the pre-delivery ``review`` (``stages/review.py``, reason
``review_held: ...``) on a sentence the page contradicts. Before this mode
the only ways on were releasing the package with the wrong sentence in it
(``release_review``) or redoing the review on the same text (``redo_review``),
which holds again. Neither files a right chronology.

The same command and the same corrections file take it (``update_decide.main``
calls ``route_plan`` and ``route_apply``). The broker's hold reason (or, on a rehearsal copy, the job's own records: the update review
clean, the pre-delivery review held) picks this mode. What differs:

* Only ``corrections`` are taken, under the same words-only rules
  (``update_decide._edit``: every citation kept, an added paragraph cites
  the entry's own exhibit, the date line kept, delivered text never edited).
  Closures, repeats and confirmed drops answer the update review's findings,
  which are settled here, so a file carrying any is refused.
* The document edited is ``runs/<unit>/final-chronology.md``, the final set's
  output, and only while it still is (``final_set_io.ran``). Its sha then no
  longer matches the final set's marker, so ``final_set_io.source`` reads the
  corrected document afresh and the final set runs on it; its rules are
  delete-only, so the keys it carries are the corrected ones.
* New sentences get SMD-attested ``SUPPORTED`` audit rows, as in the first
  mode (``update_decide._write_corrections``).
* The update review's record stays ``clean``. One SMD round is added naming
  the values the edit took out (proof 10 reads ``applied`` findings), and its
  ``doc_sha_out`` names the corrected document, so a later rerun of that
  review reuses its settled record and pays nothing.
* The resume marker reopens the final set and everything after it that reads
  the document (``REDO``). ``review`` in a marker becomes the job dir's
  ``redo-review.json`` (``resume.start_run``), which the review stage reads
  to forget its held record (``stages/review._consume_redo``), so the
  pre-delivery review runs afresh, paid, on the corrected chronology. The
  billing stages are not reopened: they read the bills, not the chronology.
* Offline, on a rehearsal copy, the same stages are reopened in the copy's
  own state and its review record forgotten, so ``jobs rehearse <copy>``
  walks the final set, assemble, render, proofs and manifest at $0 and stops
  at the paid review.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..resume import MARKER_PREFIX
from . import update_decide as UD

#: The pre-delivery review's hold reason (``stages/review.EXITS``,
#: ``proof/review.held_reason``); the broker's ``release_review`` reads it too.
REVIEW_PREFIX = "review_held"
MODE = "pre_delivery_review"
#: The stages that read the corrected document, in DAG order (``dag_update``).
REDO = ("update_final_set", "update_assemble", "update_render", "update_proofs", "update_manifest", "review")


def _rd(job: Any) -> tuple[Path, Path, str]:
    unit = job.units[0].unit
    slug_dir = job.data_root / job.slug
    return slug_dir, slug_dir / "runs" / unit, unit


def applies(d: Any, job_id: str, copy: Path | None) -> bool:
    """This mode, not the update review's: the broker holds the job on the
    pre-delivery review (live), or the copy's update review is settled and its
    pre-delivery review held (offline)."""
    if copy is None:
        row = d.broker.status(job_id)
        return bool(row) and row.get("state") == "held" and str(row.get("reason") or "").startswith(REVIEW_PREFIX)
    from . import job as job_mod
    from ..proof import review as PR

    job = job_mod.load(UD.copy_dir(copy))
    if len(job.units) != 1:
        return False
    _slug, rd, _unit = _rd(job)
    rec = rd / "update" / UD.RECORD
    repair = json.loads(rec.read_text(encoding="utf-8")) if rec.is_file() else {}
    return repair.get("status") == "clean" and PR.status(PR.load(rd)) == "held"


def _live(d: Any, job_id: str) -> None:
    if d._daemon_state(job_id).get("state") != "held":
        raise UD.Refused(f"the daemon does not hold job {job_id}; it may be running already. Nothing was written.")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise UD.Refused(f"job {job_id} is already queued to run; nothing was written")


def _sent(slug_dir: Path, rd: Path, unit: str) -> bool:
    """A file of the package is recorded sent or started: the final set then
    leaves the document as filed (``stages/update_final_set.already_sent``),
    and a correction would never reach the attorney."""
    from ..filing import SendRecord

    mp = slug_dir / "out" / unit / "upload_manifest.json"
    rows = json.loads(mp.read_text(encoding="utf-8")) if mp.is_file() else []
    names = {str(m.get(k)) for m in rows if isinstance(m, dict) for k in ("name", "intended_name") if m.get(k)}
    return bool(names & set(SendRecord.load(rd / "delivery.json").recorded()))


def plan(
    d: Any,
    job_id: str,
    *,
    corrections: list[dict[str, str]],
    decision: dict[str, str],
    copy: Path | None = None,
    extras: bool = False,
) -> dict[str, Any]:
    """Every check, and what would be written. Raises ``UD.Refused``.
    `extras`: the file carried closures, repeats or confirmed drops."""
    if extras:
        raise UD.Refused(
            "the job is held by the pre-delivery review; its update review is settled, so the file may hold "
            "corrections only (no closures, repeats or confirmed drops); nothing was written"
        )
    if not corrections:
        raise UD.Refused("the job is held by the pre-delivery review; the file holds no corrections")
    if copy is None:
        _live(d, job_id)
    from . import job as job_mod
    from ..proof import review as PR
    from ..stages.update_review_io import page_counts
    from .audit import claims as CL
    from .audit.page_text import exhibit_paths
    from .update import body as B, final_set_io as FIO

    job = job_mod.load(UD.copy_dir(copy) if copy is not None else d.job_dir(job_id))
    if len(job.units) != 1:
        raise UD.Refused(f"job {job_id} has {len(job.units)} units; an update has one")
    slug_dir, rd, unit = _rd(job)
    record, doc, base_p = rd / "update" / UD.RECORD, rd / "final-chronology.md", rd / "update" / "base.md"
    if not record.is_file() or not doc.is_file() or not base_p.is_file():
        raise UD.Refused(f"job {job_id} has no update review record, document or delivered base on this seat")
    if PR.status(PR.load(rd)) != "held":
        raise UD.Refused(f"the pre-delivery review of {job_id} is {PR.status(PR.load(rd))}, not held")
    if json.loads(record.read_text(encoding="utf-8")).get("status") != "clean":
        raise UD.Refused(f"the update review record of {job_id} is not clean; correct what it holds first")
    if not FIO.ran(rd / "update"):
        raise UD.Refused("the document is not the one the final set wrote and the review read; nothing was written")
    if _sent(slug_dir, rd, unit):
        raise UD.Refused(f"a file of {job_id}'s package is recorded sent; a correction would not reach the filing")
    md, base = doc.read_text(encoding="utf-8"), base_p.read_text(encoding="utf-8")
    entries = B.entries_of(md)[1]
    new_md, _touched, removed = UD._edit(md, base, list(entries), corrections, page_counts(slug_dir / "out" / unit))
    if len(B.entries_of(new_md)[1]) != len(entries):
        raise UD.Refused("the corrections change how many entries the document holds")
    keep = set(exhibit_paths(slug_dir / "out" / unit))
    old_keys = {c["key"] for c in CL.extract_claims(CL.body_of(md), keep)}
    attested = [c for c in CL.extract_claims(CL.body_of(new_md), keep) if c["key"] not in old_keys]
    marker = {
        "job_id": job_id,
        "intervention": "decide_update",
        "reason": f"pre-delivery review finding corrected by SMD ({decision['by']}, {decision['source']})"[:500],
        "redo": list(REDO),
    }
    return {
        "mode": MODE,
        "job_id": job_id,
        "unit": unit,
        "slug_dir": str(slug_dir),
        "doc": new_md,
        "doc_sha": UD._sha(new_md),
        "corrections": corrections,
        "closures": [],
        "attested": attested,
        "repeats": [],
        "confirmed": [],
        "removed": removed,
        "decision": decision,
        "marker": marker if copy is None else None,
    }


def _record(record: Path, p: dict[str, Any], at: str) -> None:
    """The update review's record, still clean: one SMD round naming the
    values the edit took out, and the corrected document's sha."""
    rec = json.loads(record.read_text(encoding="utf-8"))
    finding = {
        "anchor": "pre-delivery review",
        "klass": "pre-delivery review finding corrected by SMD",
        "outcome": "applied",
        "removed": p["removed"],
    }
    dec = p["decision"]
    row = {"round": "smd", "kind": "smd_correction_after_review", **dec, "at": at, "attested": len(p["attested"])}
    rec["rounds"] = list(rec.get("rounds") or []) + [{**row, "findings": [finding]}]
    rec["smd_corrected_after_review"] = {**dec, "at": at, "corrections": len(p["corrections"])}
    rec["doc_sha_out"] = p["doc_sha"]
    record.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def _reopen_copy(rd: Path) -> None:
    """Offline: the copy's own state reopens the $0 stages (a rehearsal may
    reopen no paid one) and its review record is forgotten, as the live
    marker's ``review`` makes the stage do."""
    from ..proof import review as PR
    from .state import RunState

    st = RunState.load_or_new(rd / "state.json", slug=rd.parent.parent.name, unit=rd.name)
    st.invalidate([s for s in REDO if s != "review"])
    PR.forget(rd)


def apply(d: Any, p: dict[str, Any], corrections_file: Path | None = None) -> None:
    slug_dir, dec = Path(p["slug_dir"]), p["decision"]
    rd = slug_dir / "runs" / p["unit"]
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    written = UD._write_corrections(rd, slug_dir, p, f"{dec['by']}, {dec['source']}", at)
    record = rd / "update" / UD.RECORD
    _record(record, p, at)
    written.append(record)
    if p["marker"] is None:  # a rehearsal copy: reopened in place, nothing queued, the file kept
        _reopen_copy(rd)
        return
    for f in written:
        d._chown_child(f)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(json.dumps(p["marker"], sort_keys=True), "utf-8")
    if corrections_file is not None:
        corrections_file.unlink(missing_ok=True)


def route_plan(d: Any, job_id: str, **kw: Any) -> dict[str, Any]:
    """The command's plan: this mode when the job is held by the pre-delivery
    review (``applies``), else ``update_decide.plan`` with the same arguments,
    which refuses any hold but the update review's."""
    copy = kw.get("copy")
    if not applies(d, job_id, copy):
        return UD.plan(d, job_id, **kw)
    by, source, reason = (str(kw.get(k) or "").strip() for k in ("by", "source", "reason"))
    if not by or not source or not reason:
        raise UD.Refused("--by (who decided), --source (where it was read) and --reason are all required")
    extras = bool(kw.get("closures") or kw.get("confirmed") or kw.get("repeats"))
    decision = {"by": by, "source": source, "reason": reason[:500]}
    return plan(d, job_id, corrections=list(kw.get("corrections") or []), decision=decision, copy=copy, extras=extras)


def route_apply(d: Any, p: dict[str, Any], corrections_file: Path | None = None) -> None:
    """Write what ``route_plan`` planned, by the mode it planned in."""
    (apply if p.get("mode") == MODE else UD.apply)(d, p, corrections_file)


__all__ = ["MODE", "REDO", "REVIEW_PREFIX", "applies", "apply", "plan", "route_apply", "route_plan"]
