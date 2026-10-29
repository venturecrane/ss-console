"""Record SMD's correction of an update the independent review held, and resume.

The update's independent review (``stages/update_review.py``) holds when a
must-fix finding is left that its finisher could not correct against the
pages: "SMD finishes it". Before 2026-10-29 SMD could not. The root resume
refused the hold, and a hand edit of the job's document followed by a resume
would have failed anyway: the corrected sentence is a claim no audit row
grades, so the stage's own audit loop would re-grade it (and the audit is
what disagreed) or the update proofs would refuse it as unaudited.

WHY (2026-10-29). Oleksandra's update held on one finding: the drafter and
the audit read a flowsheet's time rows backwards, the reviewer read them
right, and the finisher's correction was reverted because the audit "did not
find the edited sentence on its page". A person read the page in the ops
workspace and wrote the corrected sentence. Nothing could take it.

This is SMD's act, as root on the seat, with the medchron venv's python::

    python -m medchron.update_decide <job_id> --corrections <file.json> \\
        --by <who decided> --source <where it was read> --reason <text> [--dry-run]

The corrections file is ``{"corrections": [{"old": "...", "new": "..."}]}``:
each ``old`` is text of an ADDED entry exactly as the held document has it
(once in the document, never in the delivered chronology), each ``new`` keeps
its citations, its paragraphs and the entry's date line. Every open finding
must sit on an entry a correction touches, or nothing is written. What is
written, in one act:

* the corrected document (and the same edit in ``entries_scoped_final.md``
  when the text is there verbatim);
* one audit row per new sentence, ``SUPPORTED``, attested by SMD with who and
  where (``mode: "smd"``): the verdict gate and the update proofs read it like
  any other, and the row says whose verdict it is;
* the review record closed (``status: clean``), each open finding marked
  corrected by SMD, the values the edit took out named so proof 10 reads them
  as named, not dropped;
* the daemon's resume marker, ``redo: []``. The update review re-runs and
  finds the document it already recorded as settled, so nothing is paid
  again; assemble, render, proofs, manifest, the pre-delivery review (a fresh
  paid read of the corrected chronology) and the upload with its read-back
  run as on any job.

The corrections file is deleted once applied: it carries record text, and the
job's own directory holds the record from then on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from .resume import MARKER_PREFIX

#: The update review's hold reason (``dag_update.py`` exit 2,
#: ``update_review_settle.hold_line``); the broker's resume reads the same
#: prefix (``workspace_broker.medchron_verbs.UPDATE_REVIEW_HOLD_PREFIX``).
HOLD_PREFIX = "update_held: the independent review of the update"
RECORD = "review_repair.json"


class Refused(RuntimeError):
    """The correction cannot be recorded; the message says why."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_corrections(path: Path) -> list[dict[str, str]]:
    body = json.loads(path.read_text(encoding="utf-8"))
    rows = body.get("corrections") if isinstance(body, dict) else body
    if not isinstance(rows, list) or not rows:
        raise Refused("the corrections file holds no corrections")
    out = []
    for i, r in enumerate(rows):
        old, new = (r.get("old"), r.get("new")) if isinstance(r, dict) else (None, None)
        if not isinstance(old, str) or not isinstance(new, str) or not old.strip() or not new.strip():
            raise Refused(f"correction {i + 1}: old and new must both be non-empty text")
        if old == new:
            raise Refused(f"correction {i + 1}: new is the same as old")
        out.append({"old": old, "new": new})
    return out


def _entry_index(entries: list[str], old: str, n: int) -> int:
    hits = [i for i, e in enumerate(entries) if old in e]
    if len(hits) != 1:
        raise Refused(f"correction {n}: its old text is not inside exactly one added entry")
    return hits[0]


def _anchor_index(anchor: str) -> int | None:
    head = anchor.split(".", 1)[0]
    return int(head[1:]) - 1 if head.startswith("N") and head[1:].isdigit() else None


def _edit(md: str, base: str, entries: list[str], corrections: list[dict[str, str]]) -> tuple[str, set[int], list]:
    """The corrected document, the entries touched, and the values each edit removed."""
    from .audit import claims as CL
    from .update import values as V

    touched: set[int] = set()
    removed: list[str] = []
    for n, c in enumerate(corrections, 1):
        old, new = c["old"], c["new"]
        if md.count(old) != 1:
            raise Refused(f"correction {n}: its old text occurs {md.count(old)} times in the held document, not once")
        if old in base:
            raise Refused(f"correction {n}: its old text is in the delivered chronology, which an update never edits")
        i = _entry_index(entries, old, n)
        if CL.CITE.findall(old) != CL.CITE.findall(new):
            raise Refused(f"correction {n}: it changes a citation; a correction keeps every citation as it was")
        if old.count("\n\n") != new.count("\n\n"):
            raise Refused(f"correction {n}: it adds or removes a paragraph; a correction rewrites words only")
        if entries[i].split("\n", 1)[0] != entries[i].replace(old, new).split("\n", 1)[0]:
            raise Refused(f"correction {n}: it changes the entry's date line")
        md = md.replace(old, new)
        entries[i] = entries[i].replace(old, new)
        touched.add(i)
        removed += V.lost(old, new)
    return md, touched, sorted(set(removed))


def plan(
    d: Any, job_id: str, *, corrections: list[dict[str, str]], by: str, source: str, reason: str
) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused."""
    if not by.strip() or not source.strip() or not reason.strip():
        raise Refused("--by (who decided), --source (where it was read) and --reason are all required")
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no chronology job {job_id}")
    if row.get("state") != "held" or not str(row.get("reason") or "").startswith(HOLD_PREFIX):
        raise Refused(
            f"job {job_id} is {row.get('state')} ({str(row.get('reason') or '')[:80]}), "
            "not held by the update's independent review"
        )
    if d._daemon_state(job_id).get("state") != "held":
        raise Refused(f"the daemon does not hold job {job_id}; it may be running already. Nothing was written.")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was written")
    from . import job as job_mod
    from .audit import claims as CL
    from .audit.page_text import exhibit_paths
    from .update import body as B

    job = job_mod.load(d.job_dir(job_id))
    if len(job.units) != 1:
        raise Refused(f"job {job_id} has {len(job.units)} units; an update has one")
    unit = job.units[0].unit
    slug_dir = job.data_root / job.slug
    rd = slug_dir / "runs" / unit
    record, doc, base_p = rd / "update" / RECORD, rd / "final-chronology.md", rd / "update" / "base.md"
    if not record.is_file() or not doc.is_file() or not base_p.is_file():
        raise Refused(f"job {job_id} has no update review record, document or delivered base on this seat")
    rec = json.loads(record.read_text(encoding="utf-8"))
    md = doc.read_text(encoding="utf-8")
    if rec.get("status") != "held" or not rec.get("open"):
        raise Refused(f"the update review record of {job_id} is {rec.get('status')!r}, not held with open findings")
    if rec.get("doc_sha_out") != _sha(md):
        raise Refused("the document changed since the review held on it; correct the document the review read")
    entries = B.entries_of(md)[1]
    new_md, touched, removed = _edit(md, base_p.read_text(encoding="utf-8"), list(entries), corrections)
    if len(B.entries_of(new_md)[1]) != len(entries):
        raise Refused("the corrections change how many entries the document holds")
    uncovered = [
        f"entry {f.get('anchor')}"
        for f in rec["open"]
        if not isinstance(f, dict) or _anchor_index(str(f.get("anchor") or "")) not in touched
    ]
    if uncovered:
        raise Refused(f"open findings no correction touches: {', '.join(uncovered)}; nothing was written")
    keep = set(exhibit_paths(slug_dir / "out" / unit))
    old_keys = {c["key"] for c in CL.extract_claims(CL.body_of(md), keep)}
    attested = [c for c in CL.extract_claims(CL.body_of(new_md), keep) if c["key"] not in old_keys]
    return {
        "job_id": job_id,
        "unit": unit,
        "slug_dir": str(slug_dir),
        "doc": new_md,
        "doc_sha": _sha(new_md),
        "corrections": corrections,
        "attested": attested,
        "removed": removed,
        "decision": {"by": by.strip(), "source": source.strip(), "reason": reason.strip()[:500]},
        "marker": {
            "job_id": job_id,
            "reason": f"update review finding corrected by SMD ({by.strip()}, {source.strip()})"[:500],
            "redo": [],
        },
    }


def apply(d: Any, p: dict[str, Any], corrections_file: Path | None = None) -> None:
    from .audit import claims as CL
    from .stages import update_review as UR

    slug_dir, unit, dec = Path(p["slug_dir"]), p["unit"], p["decision"]
    rd = slug_dir / "runs" / unit
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    who = f"{dec['by']}, {dec['source']}"
    doc = rd / "final-chronology.md"
    doc.write_text(p["doc"], encoding="utf-8")
    written = [doc]
    side = rd / "entries_scoped_final.md"  # best effort, as review_settle edits it
    if side.is_file():
        text = side.read_text(encoding="utf-8")
        for c in p["corrections"]:
            if text.count(c["old"]) == 1:
                text = text.replace(c["old"], c["new"])
        side.write_text(text, encoding="utf-8")
        written.append(side)
    results = slug_dir / "out" / unit / "audit-results.jsonl"
    for c in p["attested"]:
        CL.append_row(
            results,
            {
                "key": c["key"],
                "kind": "real",
                "exhibit": c["exhibit"],
                "page_spec": c["page_spec"],
                "pages": CL.parse_pages(c["page_spec"]) or [1],
                "claim": c["claim"][:500],
                "verdict": "SUPPORTED",
                "note": f"attested by SMD ({who}) against the page: {dec['reason']}"[:500],
                "mode": "smd",
                "smd": {"by": dec["by"], "source": dec["source"], "at": at},
                "doc_sha": CL.doc_sha_of(CL.body_of(p["doc"])),
                "ts": at,
            },
        )
    written.append(results)
    record = rd / "update" / UR.RECORD
    rec = json.loads(record.read_text(encoding="utf-8"))
    closed = []
    for f in rec.get("open") or []:
        f["outcome"] = f"corrected by SMD ({who})"[:400]
        closed.append({**f, "outcome": "applied", "removed": p["removed"] if not closed else []})
    rec["rounds"] = list(rec.get("rounds") or []) + [
        {"round": "smd", "kind": "smd_correction", **dec, "at": at, "attested": len(p["attested"]), "findings": closed}
    ]
    rec["smd_corrected"] = {**dec, "at": at, "corrections": len(p["corrections"])}
    rec["status"], rec["open"], rec["doc_sha_out"] = "clean", [], p["doc_sha"]
    record.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    written.append(record)
    for f in written:
        d._chown_child(f)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(json.dumps(p["marker"], sort_keys=True), "utf-8")
    if corrections_file is not None:
        corrections_file.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.update_decide", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--corrections", required=True, type=Path, help='{"corrections": [{"old": ..., "new": ...}]}')
    ap.add_argument("--by", required=True, help="who decided (an email address)")
    ap.add_argument("--source", required=True, help="where the page was read (a vfy_ id)")
    ap.add_argument("--reason", required=True, help="what the page says that the held text got wrong, in brief")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    a = ap.parse_args(argv)
    from . import daemon as daemon_mod

    d = daemon_mod.build()
    try:
        p = plan(d, a.job_id, corrections=load_corrections(a.corrections), by=a.by, source=a.source, reason=a.reason)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    # Counts only: the corrected text stays in the job's files.
    summary = {"corrections": len(p["corrections"]), "sentences_attested": len(p["attested"])}
    print(json.dumps({"dry_run": a.dry_run, **summary, "values_named": len(p["removed"])}, indent=1))
    if a.dry_run:
        return 0
    apply(d, p, a.corrections)
    print(f"recorded SMD's correction on {a.job_id}; the daemon re-queues it on its next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
