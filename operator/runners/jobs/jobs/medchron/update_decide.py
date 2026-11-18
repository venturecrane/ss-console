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

This is SMD's act, as root on the seat, with the jobs venv's python::

    python -m jobs.medchron.update_decide <job_id> --corrections <file.json> \\
        --by <who decided> --source <where it was read> --reason <text> [--dry-run]

The corrections file is ``{"corrections": [{"old": "...", "new": "..."}]}``:
each ``old`` is text of an ADDED entry exactly as the held document has it
(once in the document, never in the delivered chronology), each ``new`` keeps
its citations, its paragraphs and the entry's date line. Every open finding
must sit on an entry a correction touches, or be closed (below), or nothing
is written. What is written, in one act:

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

CLOSURES (2026-11-16). An update can also hold on findings the REVIEWER got
wrong: class 2 "omitted content" where the content it asks for is already
composed, in a delivered entry from another exhibit copy of the same visit,
in another added entry citing a different filed copy, or in a different added
entry than the one the finding is anchored to. Rewording an unrelated entry
to satisfy the gate would be wrong, so the same file takes closures beside,
or instead of, corrections::

    {"corrections": [],
     "closures": [{"anchor": "N7", "exhibit": 9, "page": 114,
                   "composed_in": {"added": "N89"}, "why": "..."},
                  {"anchor": "N12", "exhibit": 9, "page": 120,
                   "composed_in": {"delivered": "08/27/2026\\nSample Hospital | All Other Information"},
                   "why": "..."}]}

A closure names ONE open finding by anchor, exhibit and page: ``N7`` matches
the finding ``N7`` or a claim of it (``N7.c2``), ``N7.c2`` only itself, and
the three together must match exactly one open finding, never one another
closure took. ``composed_in`` is where the content already is, one key:
``{"added": "N<k>"}``, an added entry of the held document anchored as the
review anchors them (``review_repair.anchor_entries``), or
``{"delivered": "<text>"}``, text holding an MM/DD/YYYY date that occurs
verbatim EXACTLY ONCE in the delivered chronology (``update/base.md``), so it
identifies one delivered entry: the entry's date line and heading line
together (a bare date two delivered entries share is refused). ``why`` is
required. A closure changes no
document text: with closures only, the document, its sha and the audit rows
are untouched. Each closed finding is kept in the record's SMD round with
``outcome`` ``CLOSED`` and a ``closure`` field (where, why, by, source);
proof 10 reads only ``applied`` findings, so a closure names no value. Each
closure also gets one plain NOTE line in the attorney's notes file, through
the record the rule-settled findings use (``settled_by_rule`` and
``update_review_settle.SETTLED``, read by ``update_review_settle.notes``).

The decisions file is deleted once applied: it carries record text, and the
job's own directory holds the record from then on. ``--decisions`` is the
same flag as ``--corrections``.

OFFLINE (2026-11-16). ``--offline <copy>`` decides a rehearsal copy of the
job (``<job dir>/rehearsal/<stamp>``, made by ``jobs rehearse``) instead: no
broker, daemon or queue is read, no resume marker is written, the decisions
file is kept for the live act, and a directory that is not such a copy, or
whose data root lies outside it, is refused. ``jobs rehearse <copy>`` then
walks the settled update review and every $0 stage behind it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from ..resume import MARKER_PREFIX

#: The update review's hold reason (``dag_update.py`` exit 2,
#: ``update_review_settle.hold_line``); the broker's resume reads the same
#: prefix (``workspace_broker.medchron_verbs.UPDATE_REVIEW_HOLD_PREFIX``).
HOLD_PREFIX = "update_held: the independent review of the update"
RECORD = "review_repair.json"
#: the outcome of an open finding SMD closed: its content is already composed
CLOSED = "closed by SMD: already composed"
_MDY = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")


class Refused(RuntimeError):
    """The correction cannot be recorded; the message says why."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _corrections(rows: object) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        raise Refused("the corrections in the file are not a list")
    out = []
    for i, r in enumerate(rows):
        old, new = (r.get("old"), r.get("new")) if isinstance(r, dict) else (None, None)
        if not isinstance(old, str) or not isinstance(new, str) or not old.strip() or not new.strip():
            raise Refused(f"correction {i + 1}: old and new must both be non-empty text")
        if old == new:
            raise Refused(f"correction {i + 1}: new is the same as old")
        out.append({"old": old, "new": new})
    return out


def _closure(i: int, r: object) -> dict[str, Any]:
    from .update import review_repair as RR

    row: dict[str, Any] = r if isinstance(r, dict) else {}
    anchor, why, where = RR._str(row.get("anchor")), RR._str(row.get("why")), row.get("composed_in")
    exhibit, page = RR._int(row.get("exhibit")), RR._int(row.get("page"))
    if not anchor or exhibit is None or page is None:
        raise Refused(f"closure {i + 1}: anchor, exhibit and page must name the open finding")
    if not why:
        raise Refused(f"closure {i + 1}: why is required")
    kind = next(iter(where), "") if isinstance(where, dict) and len(where) == 1 else ""
    text = RR._str(where.get(kind)) if isinstance(where, dict) and kind in ("added", "delivered") else ""
    if not text:
        raise Refused(f'closure {i + 1}: composed_in must be {{"added": "N<k>"}} or {{"delivered": "<date line>"}}')
    return {"anchor": anchor, "exhibit": exhibit, "page": page, "composed_in": {kind: text}, "why": why}


def load_decisions(path: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """(corrections, closures) from the decisions file; at least one of either."""
    body = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(body, list):
        body = {"corrections": body}
    if not isinstance(body, dict):
        raise Refused("the decisions file is not a JSON object")
    corrections = _corrections(body.get("corrections") or [])
    rows = body.get("closures") or []
    if not isinstance(rows, list):
        raise Refused("the closures in the file are not a list")
    closures = [_closure(i, r) for i, r in enumerate(rows)]
    if not corrections and not closures:
        raise Refused("the decisions file holds no corrections and no closures")
    return corrections, closures


def load_corrections(path: Path) -> list[dict[str, str]]:
    corrections, _ = load_decisions(path)
    if not corrections:
        raise Refused("the corrections file holds no corrections")
    return corrections


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


def _matches(f: dict[str, Any], c: dict[str, Any]) -> bool:
    from .update import review_repair as RR

    anchor = str(f.get("anchor") or "")
    same = anchor == c["anchor"] or ("." not in c["anchor"] and anchor.split(".", 1)[0] == c["anchor"])
    return same and RR._int(f.get("exhibit")) == c["exhibit"] and RR._int(f.get("page")) == c["page"]


def _where(c: dict[str, Any], entries: list[str], base: str, n: int) -> str:
    """Where the closed content is composed, in the attorney's words. Refused
    when the place named is not there."""
    kind, text = next(iter(c["composed_in"].items()))
    if kind == "added":
        k = _anchor_index(text)
        if "." in text or k is None or not 0 <= k < len(entries):
            raise Refused(f"closure {n}: {text} is not an added entry of the held document")
        lines = [ln.strip() for ln in entries[k].split("\n")[:2] if ln.strip()]
        return "the entry for " + ", ".join(lines)
    if not _MDY.search(text) or text not in base:
        raise Refused(f"closure {n}: the delivered chronology does not hold {text[:80]!r} with its date")
    if (times := base.count(text)) != 1:  # it must name ONE delivered entry, never a date two entries share
        raise Refused(
            f"closure {n}: {text[:80]!r} occurs {times} times in the delivered chronology; "
            "give the date line and the heading line together"
        )
    return "the delivered entry for " + " ".join(text.split())


def _close(
    rec: dict[str, Any], closures: list[dict[str, Any]], entries: list[str], base: str, touched: set[int]
) -> list[dict[str, Any]]:
    """Each closure with its one open finding and where its content is.
    Refused when a closure matches none or several, two close one finding,
    or an open finding is neither corrected nor closed."""
    taken: dict[int, int] = {}
    out = []
    for n, c in enumerate(closures, 1):
        hits = [i for i, f in enumerate(rec["open"]) if isinstance(f, dict) and _matches(f, c)]
        if len(hits) != 1:
            raise Refused(
                f"closure {n}: {c['anchor']} Exhibit {c['exhibit']} p. {c['page']} matches "
                f"{len(hits)} open findings, not one"
            )
        if hits[0] in taken:
            raise Refused(f"closures {taken[hits[0]]} and {n} close the same open finding")
        taken[hits[0]] = n
        out.append({**c, "finding": hits[0], "where": _where(c, entries, base, n)})
    uncovered = [
        f"entry {f.get('anchor') if isinstance(f, dict) else '?'}"
        for i, f in enumerate(rec["open"])
        if i not in taken and (not isinstance(f, dict) or _anchor_index(str(f.get("anchor") or "")) not in touched)
    ]
    if uncovered:
        raise Refused(
            f"open findings no correction touches or closure closes: {', '.join(uncovered)}; nothing was written"
        )
    return out


def _held(d: Any, job_id: str) -> None:
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


def plan(
    d: Any,
    job_id: str,
    *,
    corrections: list[dict[str, str]],
    by: str,
    source: str,
    reason: str,
    closures: list[dict[str, Any]] | None = None,
    copy: Path | None = None,
) -> dict[str, Any]:
    """Every check, and what would be written. Raises Refused. `copy`: a
    rehearsal copy of the job (``copy_dir``), decided offline: no broker,
    daemon or queue is read, and ``apply`` writes no resume marker."""
    if not by.strip() or not source.strip() or not reason.strip():
        raise Refused("--by (who decided), --source (where it was read) and --reason are all required")
    if not corrections and not closures:
        raise Refused("nothing to record: no correction and no closure")
    if copy is None:
        _held(d, job_id)
    from . import job as job_mod
    from .audit import claims as CL
    from .audit.page_text import exhibit_paths
    from .update import body as B

    job = job_mod.load(copy_dir(copy) if copy is not None else d.job_dir(job_id))
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
    entries, base = B.entries_of(md)[1], base_p.read_text(encoding="utf-8")
    new_md, touched, removed = _edit(md, base, list(entries), corrections)
    if len(B.entries_of(new_md)[1]) != len(entries):
        raise Refused("the corrections change how many entries the document holds")
    closed = _close(rec, list(closures or []), entries, base, touched)
    keep = set(exhibit_paths(slug_dir / "out" / unit))
    old_keys = {c["key"] for c in CL.extract_claims(CL.body_of(md), keep)}
    attested = [c for c in CL.extract_claims(CL.body_of(new_md), keep) if c["key"] not in old_keys]
    act = "corrected" if corrections else "closed"
    return {
        "job_id": job_id,
        "unit": unit,
        "slug_dir": str(slug_dir),
        "doc": new_md,
        "doc_sha": _sha(new_md),
        "corrections": corrections,
        "closures": closed,
        "attested": attested,
        "removed": removed,
        "decision": {"by": by.strip(), "source": source.strip(), "reason": reason.strip()[:500]},
        "marker": None
        if copy is not None
        else {
            "job_id": job_id,
            "intervention": "decide_update",
            "reason": f"update review finding {act} by SMD ({by.strip()}, {source.strip()})"[:500],
            "redo": [],
        },
    }


def copy_dir(path: Path) -> Path:
    """A rehearsal copy of a job (``<job dir>/rehearsal/<stamp>``, as ``jobs
    rehearse`` makes it) whose data root is inside the copy. Anything else is
    Refused: an offline decision never writes the live job's files."""
    from . import job as job_mod

    jd = path.resolve()
    if jd.parent.name != "rehearsal" or not (jd / "job.yaml").is_file():
        raise Refused(f"{path} is not a rehearsal copy (<job dir>/rehearsal/<stamp>/job.yaml); nothing was written")
    if not job_mod.load(jd).data_root.resolve().is_relative_to(jd):
        raise Refused(f"the data root of {path} is outside the copy, so it is live data; nothing was written")
    return jd


def _write_corrections(rd: Path, slug_dir: Path, p: dict[str, Any], who: str, at: str) -> list[Path]:
    """The corrected document, its side copy and the SMD-attested audit rows."""
    from .audit import claims as CL

    unit, dec = p["unit"], p["decision"]
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
    return written


def _close_record(rec: dict[str, Any], p: dict[str, Any], who: str, at: str) -> list[dict[str, str]]:
    """The review record settled by SMD's round; the attorney's note rows."""
    dec = p["decision"]
    closures = {c["finding"]: c for c in p["closures"]}
    findings, notes = [], []
    for i, f in enumerate(rec.get("open") or []):
        c = closures.get(i)
        if c is None:
            f["outcome"] = f"corrected by SMD ({who})"[:400]
            first = not any(x.get("outcome") == "applied" for x in findings)
            findings.append({**f, "outcome": "applied", "removed": p["removed"] if first else []})
            continue
        f["outcome"] = f"{CLOSED} in {c['where']} ({who})"[:400]
        closure = {"composed_in": c["composed_in"], "where": c["where"], "why": c["why"][:400], "by": dec["by"]}
        findings.append({**f, "outcome": CLOSED, "removed": [], "closure": {**closure, "source": dec["source"]}})
        ask = f"Exhibit {c['exhibit']} p. {c['page']}"
        notes.append(
            {"grade": "NOTE", "finding": f"the review asked for content from {ask}; it is already in {c['where']}"}
        )
    rec["rounds"] = list(rec.get("rounds") or []) + [
        {
            "round": "smd",
            "kind": "smd_correction",
            **dec,
            "at": at,
            "attested": len(p["attested"]),
            "closed": len(p["closures"]),
            "findings": findings,
        }
    ]
    rec["smd_corrected"] = {**dec, "at": at, "corrections": len(p["corrections"]), "closures": len(p["closures"])}
    rec["status"], rec["open"], rec["doc_sha_out"] = "clean", [], p["doc_sha"]
    return notes


def apply(d: Any, p: dict[str, Any], corrections_file: Path | None = None) -> None:
    from ..stages import update_review as UR, update_review_settle as URS

    slug_dir, unit, dec = Path(p["slug_dir"]), p["unit"], p["decision"]
    rd = slug_dir / "runs" / unit
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    who = f"{dec['by']}, {dec['source']}"
    # A closure changes no text: with closures only, the document and the audit rows are untouched.
    written = _write_corrections(rd, slug_dir, p, who, at) if p["corrections"] else []
    record = rd / "update" / UR.RECORD
    rec = json.loads(record.read_text(encoding="utf-8"))
    notes = _close_record(rec, p, who, at)
    if notes:  # the attorney's notes file reads these as it reads the rule-settled findings
        rec["settled_by_rule"] = list(rec.get("settled_by_rule") or []) + notes
        settled = slug_dir / "out" / unit / URS.SETTLED
        settled.write_text(json.dumps(rec["settled_by_rule"], indent=1), encoding="utf-8")
        written.append(settled)
    record.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    written.append(record)
    if p["marker"] is None:  # a rehearsal copy: no daemon owns it, nothing is queued, the file is kept for the live act
        return
    for f in written:
        d._chown_child(f)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(json.dumps(p["marker"], sort_keys=True), "utf-8")
    if corrections_file is not None:
        corrections_file.unlink(missing_ok=True)


_SHAPE = (
    '{"corrections": [{"old": ..., "new": ...}], "closures": [{"anchor": "N7", "exhibit": 9, "page": 114, '
    '"composed_in": {"added": "N89"} or {"delivered": "<date line>\\n<heading line>, verbatim, once in the delivered '
    'chronology>"}, "why": ...}]}; either list may be empty, not both'
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.medchron.update_decide", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--corrections", "--decisions", dest="corrections", required=True, type=Path, help=_SHAPE)
    ap.add_argument("--by", required=True, help="who decided (an email address)")
    ap.add_argument("--source", required=True, help="where the page was read (a vfy_ id)")
    ap.add_argument("--reason", required=True, help="what the page says that the held text got wrong, in brief")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; write nothing")
    ap.add_argument(
        "--offline", type=Path, metavar="COPY", help="decide a rehearsal copy of the job instead (no daemon)"
    )
    a = ap.parse_args(argv)
    from . import lane as lane_mod

    try:
        d = None if a.offline else lane_mod.build()
        corrections, closures = load_decisions(a.corrections)
        p = plan(
            d,
            a.job_id,
            corrections=corrections,
            closures=closures,
            by=a.by,
            source=a.source,
            reason=a.reason,
            copy=a.offline,
        )
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    # Counts only: the corrected text and the closures' words stay in the job's files.
    summary = {"corrections": len(p["corrections"]), "closures": len(p["closures"])}
    summary |= {"sentences_attested": len(p["attested"]), "values_named": len(p["removed"])}
    print(json.dumps({"dry_run": a.dry_run, **summary}, indent=1))
    if a.dry_run:
        return 0
    apply(d, p, a.corrections)
    print(
        f"recorded SMD's decision in the copy {a.offline}; nothing was queued; rehearse it with: jobs rehearse {a.offline}"
        if a.offline
        else f"recorded SMD's decision on {a.job_id}; the daemon re-queues it on its next tick"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
