"""An update that finds nothing new is an ANSWER, not a hold (2026-10-25). $0.

WHY. A firm asked for an update into a delivered chronology naming 222 files
the delivered chronology did not cite. The delivered run had read every one:
bills it captured in its billing worksheet, records read in full with nothing
to cite, photographs, papers that are not records. The update lane HELD on
exactly that likely answer ("nothing new to read", "nothing new to add", or
every named file excluded by the firm's own rules, which was even paged as "a
named document id is not on the matter"), and a held chronology tells the firm
nothing and waits for SMD. The Captain's rule: the Operator finishes the
chronology job end to end; a stop that needs SMD's hand is a lane defect; the
lane decides by rule and discloses.

So the three conclusions end the job DELIVERED with the outcome
``already_current``. Nothing is uploaded and nothing deleted: the delivered
chronology stays as it is. The stage that concluded exits with its ``CURRENT``
code (``dag.CURRENT``) and the driver calls ``end``, which writes
``runs/<unit>/update/update_outcome.json`` from what the code decided, never
from model prose: for every requested file id (and every document the pull
holds) its name, a CODED reason and its factual wording, and the counts and
pages per reason. ``end`` delivers only when the record PROVES nothing new
(``problem``): every requested id accounted for, no entry and no exhibit
added. Otherwise the job holds as ours (``update_held:``, paged).

What reads the record:

* ``unit_proof`` builds the zero-file proof block (``proof``), which the
  daemon's gate accepts only with a coherent ``already_current`` summary and
  no file filed (``proof/gate``). An update that added entries and filed
  nothing is never believed delivered.
* the DELIVER wake carries the counts per reason (``wake_lines``); the
  skill words the reply from the codes, never from file names.
* ``covered.delivery_fields`` puts the summary on the ledger row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import proof_mode
from ..gates.foreign_identity import WITHHELD_REASONS
from ..stages.base import read_json, read_jsonl
from . import body as B, select as S
from .billing_page import BILLING
from .locate import vendor_name
from .medlegal import REASON as MEDLEGAL_REASON
from ..stages.prior_reading import PREFIX as PRIOR_PREFIX
from ..unopened import REASON as PASSWORD_REASON

RECORD = "update_outcome.json"
KIND = "already_current"
#: The exit an update stage returns when it concludes nothing new; the update
#: DAG maps it to ``dag.CURRENT`` (``download`` has its own, EXIT_ALL_EXCLUDED).
EXIT = 5
#: An image file whose one reading the page transcription could not make
#: (``update_select``): set aside and named, read again by the next update.
IMAGE_UNREAD = "an image the page transcription could not read"
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"})

# ---- the coded reasons and their factual wording -----------------------------------
EXCLUDED = "excluded_by_rule"
DELIVERED_PACKAGE = "delivered_package"
DELETED = "deleted"
LEFT_OUT = "left_out_by_rule"
DUPLICATE = "duplicate"
PAGES_DELIVERED = "pages_already_delivered"
REPEATS_NEW = "repeats_another_new_file"
BILLING_ONLY = "billing_only"
MEDLEGAL = "medlegal_report"
NO_NEW_VISIT = "read_no_new_visit"
NO_TEXT = "no_record_text"
NO_PAGE = "no_page_to_read"
UNREADABLE = "unreadable"
NOT_RETRIEVED = "not_retrieved"
EMAIL = "email"
#: The delivered run read it and recorded why it adds nothing (``prior_reading``).
PRIOR = "read_for_the_delivered_chronology"
#: Never proven nothing new: a file no rule accounts for, or one the update read.
UNACCOUNTED = "unaccounted"
SELECTED = "selected_to_read"
NOT_PROVEN = frozenset({UNACCOUNTED, SELECTED})

WORDING = {
    EXCLUDED: "excluded by the firm's rule",
    DELIVERED_PACKAGE: "a file of the delivered chronology's own package",
    DELETED: "deleted on the matter",
    LEFT_OUT: "left out by rule",
    DUPLICATE: "a byte-identical copy",
    PAGES_DELIVERED: "every page is already in the delivered exhibits",
    REPEATS_NEW: "every page repeats another file read in this update",
    BILLING_ONLY: "billing pages only",
    MEDLEGAL: MEDLEGAL_REASON,
    NO_NEW_VISIT: "read in full; no visit or finding the delivered chronology does not already list",
    NO_TEXT: "no record text on any page after the text layer and the page transcription were read",
    NO_PAGE: "no page left to read: each is billing, already in the delivered exhibits, a repeat, or blank",
    UNREADABLE: "could not be read as record pages",
    NOT_RETRIEVED: "could not be retrieved from the matter",
    EMAIL: "an email; its attachments were read as their own files",
    PRIOR: "read for the delivered chronology, which recorded why it adds nothing",
    UNACCOUNTED: "not accounted for by this update",
    SELECTED: "selected to read",
}


def _file(rid: str, name: str, reason: str, detail: str = "", pages: int = 0) -> dict[str, Any]:
    return {"id": rid, "name": name, "reason": reason, "text": detail or WORDING[reason], "pages": pages}


def _exhibit_reason(rec: dict[str, Any], *, collect: bool) -> str:
    """The reason for one selected document, from its page decisions."""
    if rec.get("not_itemized"):
        return MEDLEGAL
    if rec.get("read"):
        return NO_NEW_VISIT if collect else SELECTED
    why = set(rec.get("excluded") or {}) - {S.BLANK}
    if not why:
        return NO_TEXT
    if why == {BILLING}:
        return BILLING_ONLY
    if why == {S.ALREADY}:
        return PAGES_DELIVERED
    if why == {S.OVERLAP}:
        return REPEATS_NEW
    return NO_PAGE


def _pages_on_file(slug_dir: Path, rid: str) -> int:
    tp = slug_dir / "text" / f"{rid}.txt"
    return len(S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace"))) if tp.is_file() else 0


def _skipped(rid: str, sel: dict[str, Any]) -> tuple[str, str] | None:
    for why, ids in (sel.get("skipped") or {}).items():
        if rid not in {str(x) for x in ids or []}:
            continue
        if why in WITHHELD_REASONS:
            return LEFT_OUT, why if why.startswith("left out") else f"it {why}"
        if why == "byte-identical to a delivered exhibit":
            return DUPLICATE, "a byte-identical copy of an exhibit the delivered chronology already holds"
        if why == "not a PDF or an image":
            return UNREADABLE, "not a PDF or an image, so it has no pages to read"
        if why == IMAGE_UNREAD:
            return UNREADABLE, "an image file the page transcription could not read; the next update reads it again"
        if why == PASSWORD_REASON:
            return UNREADABLE, PASSWORD_REASON
        if why.startswith(PRIOR_PREFIX):
            return PRIOR, why[len(PRIOR_PREFIX) :]
        return UNACCOUNTED, why
    return None


def _excluded(x: dict[str, Any]) -> tuple[str, str]:
    why = str(x.get("why") or "")
    if why == "in the delivered chronology's own folder":
        return DELIVERED_PACKAGE, ""
    if why == "deleted":
        return DELETED, ""
    return EXCLUDED, f"excluded by the firm's rule: {why}" if why else ""


def classify(slug_dir: Path, ud: Path, requested: list[str], *, collect: bool) -> list[dict[str, Any]]:
    """One row per document: every requested id, and every document the pull
    holds (an attachment folded from an email is read too)."""
    sel = read_json(ud / "selection.json", {}) or {}
    raw = {str(r.get("id")): r for r in read_jsonl(slug_dir / "raw_manifest.jsonl") if r.get("id")}
    excluded = {str(x.get("id")): x for x in sel.get("excluded_named") or [] if x.get("id")}
    excluded |= {str(x.get("id")): x for x in read_json(slug_dir / "named_excluded.json", []) or [] if x.get("id")}
    emails = {str(r.get("id")): r for r in read_jsonl(slug_dir / "msg_manifest.jsonl") if r.get("ok")}
    exhibits = {str(r.get("id")): r for r in sel.get("exhibits") or [] if r.get("id")}
    subsets = {str(s.get("id")): s for s in sel.get("subsets") or [] if s.get("id")}
    out: list[dict[str, Any]] = []
    for rid in dict.fromkeys([*requested, *raw, *exhibits]):
        r = raw.get(rid) or {}
        name = vendor_name(r) if r else str((excluded.get(rid) or emails.get(rid) or {}).get("name") or rid)
        pages = _pages_on_file(slug_dir, rid)
        if rid in excluded:
            out.append(_file(rid, name, *_excluded(excluded[rid])))
        elif rid in emails and rid not in raw:
            out.append(_file(rid, name, EMAIL))
        elif r and not r.get("ok"):
            out.append(
                _file(
                    rid,
                    name,
                    NOT_RETRIEVED,
                    f"could not be retrieved from the matter ({r.get('error') or 'no reason given'})"[:200],
                )
            )
        elif r.get("duplicate_of"):
            out.append(_file(rid, name, DUPLICATE, "a byte-identical copy of another file in this update", pages))
        elif rid in exhibits:
            rec = exhibits[rid]
            why = _exhibit_reason(rec, collect=collect)
            if why == NO_TEXT and str(r.get("ext") or "").lower() in IMAGE_EXTS:
                out.append(_file(rid, name, why, "an image with no record text on it", int(rec.get("pages") or 0)))
            else:
                out.append(_file(rid, name, why, pages=int(rec.get("pages") or 0)))
        elif rid in subsets:
            s = subsets[rid]
            home = (s.get("subset_of") or {}).get("id")
            out.append(_file(rid, name, REPEATS_NEW if home else PAGES_DELIVERED, pages=int(s.get("pages") or 0)))
        elif (hit := _skipped(rid, sel)) is not None:
            out.append(_file(rid, name, hit[0], hit[1], pages))
        else:
            out.append(_file(rid, name, UNACCOUNTED, pages=pages))
    return out


def _requested(slug_dir: Path) -> list[str]:
    return [str(x) for x in (read_json(slug_dir / "include.json", {}) or {}).get("include_file_ids") or []]


def _added(ud: Path, stage: str) -> tuple[int, int]:
    """(entries added, exhibits added) by the stage that concluded. Collect
    writes both files empty when it keeps nothing; a stale file from an earlier
    attempt counts against the proof, never for it."""
    if stage != "update_collect":
        return 0, 0
    p = ud / "new_entries.md"
    entries = len(B.split_body("\n\n" + p.read_text(encoding="utf-8"))[1]) if p.is_file() else 0
    return entries, len(read_json(ud / "new_exhibits.final.json", []) or [])


def write(slug_dir: Path, unit: str, stage: str) -> dict[str, Any]:
    """The outcome record, written and returned. On an all-excluded pull
    (``download``) no selection was made, so this run's selection is written
    as what it is: nothing selected, every named file excluded (the coverage
    record and Records Reviewed read it)."""
    ud = slug_dir / "runs" / unit / "update"
    ud.mkdir(parents=True, exist_ok=True)
    if stage == "download":
        sel = {"exhibits": [], "skipped": {}, "subsets": [], "readings": []}
        sel["excluded_named"] = list(read_json(slug_dir / "named_excluded.json", []) or [])
        (ud / "selection.json").write_text(json.dumps(sel, indent=1), encoding="utf-8")
    requested = _requested(slug_dir)
    files = classify(slug_dir, ud, requested, collect=stage == "update_collect")
    by: dict[str, dict[str, int]] = {}
    for f in files:
        row = by.setdefault(f["reason"], {"files": 0, "pages": 0})
        row["files"] += 1
        row["pages"] += int(f["pages"])
    entries, exhibits = _added(ud, stage)
    rec = {
        "kind": KIND,
        "stage": stage,
        "requested": len(requested),
        "files": files,
        "by_reason": by,
        "entries_added": entries,
        "exhibits_added": exhibits,
    }
    (ud / RECORD).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def load(run_dir: Path) -> dict[str, Any] | None:
    rec = read_json(run_dir / "update" / RECORD, None)
    return rec if isinstance(rec, dict) else None


def problem(rec: dict[str, Any] | None) -> str | None:
    """Why the record does not prove nothing new, or None when it does."""
    if not rec or rec.get("kind") != KIND:
        return "no outcome record says so"
    if rec.get("entries_added") or rec.get("exhibits_added"):
        return f"yet {rec.get('entries_added')} entr(ies) and {rec.get('exhibits_added')} exhibit(s) were added"
    files = rec.get("files") or []
    ids = {str(f.get("id")) for f in files}
    bad = [f for f in files if f.get("reason") in NOT_PROVEN]
    if bad:
        return f"{len(bad)} document(s) are not accounted for or were selected to read: {[f['id'] for f in bad][:5]}"
    if int(rec.get("requested") or 0) > len(ids):
        return "not every requested document has a recorded outcome"
    return None


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    """Counts only: what the verdict, the ledger and the wake carry."""
    return {
        "kind": rec["kind"],
        "requested": int(rec.get("requested") or 0),
        "files": len(rec.get("files") or []),
        "by_reason": {k: dict(v) for k, v in sorted((rec.get("by_reason") or {}).items())},
        "entries_added": int(rec.get("entries_added") or 0),
        "exhibits_added": int(rec.get("exhibits_added") or 0),
        "review_file": str(rec.get("review_file") or ""),
    }


def line(s: dict[str, Any]) -> str:
    """The counts the reply is worded from. A file found inside a named one (an
    email's attachment) is counted as such, never as a surplus: Pamplona's
    first update (2026-10-26) read "files asked: 222; files accounted for:
    223" and the reply told the attorney the counts did not reconcile."""
    kinds = "; ".join(f"{k} {v['files']} file(s) {v['pages']} page(s)" for k, v in s["by_reason"].items())
    inside = max(0, int(s["files"]) - int(s["requested"]))
    found = f", plus {inside} found inside them" if inside else ""
    return f"files named: {s['requested']}{found}, every one accounted for; by kind: {kinds or 'none'}"


def end(sr: Any, st: Any, stage: str, code: int, out: Any) -> Any:
    """The driver's ending for a stage that concluded nothing new: the record
    written, then DELIVERED (nothing filed) only when it proves nothing new;
    otherwise held as ours, and paged."""
    rec = write(sr.slug_dir, sr.unit.unit, stage)
    why = problem(rec)
    if why is not None:
        reason = f"update_held: the update found nothing to add, and {why}"[:480]
        st.finish(stage, status="held", exit_code=code, dollars=out.dollars, pages=out.pages, note=reason)
        st.end("held", reason)
        out.outcome, out.reason = "held", reason
        return out
    # The firm's answer, file by file (``review_list``), filed beside the
    # chronology and proven by hash; the chronology itself is untouched.
    from ..stages import update_review_file
    from . import review_list

    doc = review_list.build(sr, rec["files"], {})
    if proof_mode.active():
        # A proof run stops before its one write (proof_mode): the review is
        # staged under out/ for SMD to read, and the verdict says what would be filed.
        note = f"proof run: already current; would file the records review {doc.name}: {line(summary(rec))}"[:480]
        st.finish(stage, status="current", exit_code=code, dollars=out.dollars, pages=out.pages, note=note)
        out.outcome, out.reason, out.current = proof_mode.OUTCOME, note, summary(rec)
        sr.log(note)
        return out
    filed, why = update_review_file.file(sr, doc)
    if filed is None:
        reason = f"update_held: the update found nothing to add, and its records review was not filed: {why}"[:480]
        st.finish(stage, status="held", exit_code=code, dollars=out.dollars, pages=out.pages, note=reason)
        st.end("held", reason)
        out.outcome, out.reason = "held", reason
        return out
    rec["review_file"] = str(filed[0].get("name") or doc.name)
    (sr.slug_dir / "runs" / sr.unit.unit / "update" / RECORD).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    s = summary(rec)
    note = f"already current, records review filed: {line(s)}"[:480]
    st.finish(stage, status="current", exit_code=code, dollars=out.dollars, pages=out.pages, note=note)
    st.end("delivered", note)
    out.current, out.files = s, filed
    sr.log(note)
    return out


def proof(run_dir: Path, filed: list[dict[str, Any]]) -> dict[str, Any]:
    """The already-current proof block (``proof/gate``): the records review
    it filed, proven by hash, and nothing else; unproven (so failed by the
    daemon) when the record does not prove nothing new, when anything but the
    review was filed, or when the review is not proven."""
    rec = load(run_dir)
    why = problem(rec)
    if why is None and rec is not None:
        names = [str(f.get("name") or "") for f in filed]
        if names != [str(rec.get("review_file") or "")] or not all(f.get("proven") and f.get("file_id") for f in filed):
            why = "the records review is not the one proven file filed"
    if why is not None or rec is None:
        return {"readback": "unproven", "files": [], "review": "", "already_current": None, "why": why}
    return {
        "readback": "confirmed",
        "files": [
            {"name": f["name"], "file_id": str(f["file_id"]), "size": int(f.get("bytes") or 0), "sha256": f["sha256"]}
            for f in filed
        ],
        "review": "code_only",
        "review_cents": 0,
        "review_model": "",
        "already_current": summary(rec),
    }


def totals_line(fields: dict[str, Any], already_current: bool) -> str:
    """The wake's totals. An already-current update's counts line is its only
    tally: the delivered package's totals beside it read as a second,
    disagreeing count (Pamplona 2026-10-26: "200 documents, 472 pages" against
    "198 files, 583 pages", relayed to the attorney as unreconciled)."""
    totals = "" if already_current else f"Documents: {fields.get('documents', 0)}; pages: {fields.get('pages', 0)}; "
    return f"{totals}spend cents: {fields.get('cents', 0)}."


def wake_lines(outcomes: list[Any], state: str) -> list[str]:
    """The DELIVER wake's lines for an already-current update the daemon
    recorded delivered: code-authored tokens and counts only, never a file
    name. None on any other state (a delivery the gate did not believe)."""
    if state != "delivered":
        return []
    for o in outcomes:
        s = o.get("current") if isinstance(o, dict) else None
        if isinstance(s, dict) and s.get("kind") == KIND:
            filed = " A records review, one row per file, was filed beside it." if s.get("review_file") else ""
            return [f"Update outcome: {KIND}. The delivered chronology is unchanged.{filed}", line(s) + "."]
    return []


def wake_block(outcomes: list[Any], state: str, envelope: dict[str, Any]) -> list[str]:
    """The update's lines in the DELIVER wake: none for a run; for an update,
    the request line, then an already-current update's counts."""
    if not isinstance(envelope.get("update"), dict):
        return []
    return ["Request: update into the delivered chronology.", *wake_lines(outcomes, state)]


__all__ = [
    "KIND",
    "RECORD",
    "WORDING",
    "classify",
    "end",
    "load",
    "problem",
    "proof",
    "summary",
    "totals_line",
    "wake_block",
    "wake_lines",
    "write",
]
