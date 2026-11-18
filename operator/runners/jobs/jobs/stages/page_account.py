"""The page account: every source page is cited, excluded with evidence, or
read and uncited for a reason code can name. $0, no model. RECORD-ONLY.

WHY (2026-11-17). On a real 4,997-page update, 349 pages were cited, 4,411
excluded by a rule, 34 withheld as another person's, and 203 READ BUT NEVER
EXPLAINED. A page that leaves a run unexplained is the class two misses hid
in. This module settles each page's row in ``runs/<unit>/pages.jsonl``
(``medchron/pages_record``) and writes ``runs/<unit>/page_account.json``.

Three stage functions, none of which ever stops, queues or edits anything:

* ``update_page_account`` (an update, after the audit, before the review):
  each page ``update_select`` sent to compose becomes ``cited`` or
  ``read_uncited``; a read uncited page is classed by code, in order:
  ``blank`` (no ink by ``pageid.MIN_INK``, or under ``select.MIN_CHARS``
  of text), ``nonrecord`` (the firm's ``nonrecord.page_classes``,
  ``classify.classify_text``), ``billing``, else ``unexplained`` (no code
  names a reason: the class a person reads).
  An excluded page that an entry cites resolves through its row's cover to
  ``cited_via`` (D). A cite on a blank, billing, non-record or other-person
  page, or past the exhibit's last page, is recorded as a DEFECT CODE; it
  stops nothing.
* ``update_page_final`` (an update, after the proofs): the same count on the
  final text (``update/updated-chronology.md``), with how many pages the
  first account left read-uncited the review then cited (``resolved``).
* ``page_account`` (a fresh run, after the coverage gate): the same account
  over the exhibit PDFs and the final chronology. ``FRESH_POLICY`` is
  RECORD_ONLY and a test pins it: no real fresh job has been rehearsed on it.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..medchron import pages_record as PR
from ..medchron.audit import claims as CL
from ..medchron.audit.page_text import exhibit_paths
from ..medchron.update import body as B, select as S
from ..medchron.update.billing_page import is_billing
from .base import StageRun, read_json

NAME = "page_account.json"
RECORD_ONLY, HOLD = "record_only", "hold"
#: What a fresh run's account may do: record. Pinned by a test.
FRESH_POLICY = RECORD_ONLY
#: The defect codes a cite can carry (D).
CITE_ON_BLANK, CITE_ON_BILLING, CITE_ON_NONRECORD, CITE_ON_OTHER_PERSON, CITE_PAST_END, CITE_ON_UNEXPLAINED = (
    "cite_on_blank_page",
    "cite_on_billing_page",
    "cite_on_nonrecord_page",
    "cite_on_another_persons_page",
    "cite_past_exhibit_end",
    "cite_on_excluded_page_with_no_cover",
)
_CITE_DEFECT = {
    "blank": CITE_ON_BLANK,
    "billing": CITE_ON_BILLING,
    "nonrecord": CITE_ON_NONRECORD,
    "withheld_other_person": CITE_ON_OTHER_PERSON,
}
#: read-uncited classes by code
BLANK, NONRECORD, BILLING, UNEXPLAINED = "blank", "nonrecord", "billing", "unexplained"


def _nonrecord(cfg: Any) -> Callable[[str], str | None]:
    from .classify import classify_text, page_classes

    try:
        classes = page_classes(cfg)
    except Exception:  # noqa: BLE001 - a firm with no non-record classes classes nothing; the account still records
        classes = []
    return lambda t: classify_text(t, classes)


def classify_uncited(text: str, inked: bool | None, nonrecord: Callable[[str], str | None]) -> str:
    """Why a read page no entry cites is uncited, by code."""
    t = (text or "").strip()
    if inked is False or len(t) < S.MIN_CHARS or t in S.FAILED_MARKS:
        return BLANK
    if nonrecord(t):
        return NONRECORD
    return BILLING if is_billing(t) else UNEXPLAINED


def _entries(md: str) -> list[str]:
    try:
        return B.entries_of(md)[1]
    except ValueError:
        return B.split_body(md)[1]


def _cited(entries: list[str]) -> set[tuple[int, int]]:
    """(exhibit, page) of every citation in the entries."""
    return {(int(m.group(1)), p) for e in entries for m in B.CITE.finditer(e) for p in CL.parse_pages(m.group(2))}


def settle(
    rows: list[dict[str, Any]],
    entries: list[str],
    exhibit_of: dict[str, int],
    texts: dict[str, dict[int, str]],
    nonrecord: Callable[[str], str | None],
    npages: dict[int, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(rows settled against `entries`, defect rows). `exhibit_of`: {document
    id: its new exhibit number}; `texts`: {document id: {page: text}};
    `npages`: {exhibit: its page count}."""
    cited = _cited(entries)
    defects: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    out = []
    for r in rows:
        r = json.loads(json.dumps(r))  # a copy: the input rows are never edited in place
        ex = exhibit_of.get(str(r["doc"]))
        key = (ex, int(r.get("page") or 0)) if ex is not None else None
        is_cited = key is not None and key in cited
        if key is not None:
            seen.add(key)
        st = r.get("status")
        if st in (PR.READ, PR.READ_UNCITED, PR.CITED):
            if is_cited:
                r["status"] = PR.CITED
            else:
                ev = r.get("evidence") or {}
                text = (texts.get(str(r["doc"])) or {}).get(int(r.get("page") or 0), "")
                r["status"], r["class"] = PR.READ_UNCITED, classify_uncited(text, ev.get("inked"), nonrecord)
        elif is_cited and key is not None:
            code = _CITE_DEFECT.get(str(r.get("class")))
            if code is None and st == PR.EXCLUDED and not (r.get("evidence") or {}).get("cover"):
                code = CITE_ON_UNEXPLAINED
            if code is not None:
                defects.append({"code": code, "exhibit": key[0], "page": key[1]})
            if st == PR.EXCLUDED and (r.get("evidence") or {}).get("cover"):
                r["status"] = PR.CITED_VIA  # resolved through its cover
        out.append(r)
    for ex, p in sorted(cited):
        if ex in npages and npages[ex] and p > npages[ex]:
            defects.append({"code": CITE_PAST_END, "exhibit": ex, "page": p})
    return out, defects


def summary(rows: list[dict[str, Any]], defects: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "status": dict(Counter(str(r.get("status")) for r in rows)),
        "class": dict(Counter(str(r.get("class")) for r in rows)),
        "read_uncited": dict(Counter(str(r.get("class")) for r in rows if r.get("status") == PR.READ_UNCITED)),
        "defects": defects,
        # A cover must be delivered or read here: excluded pages whose cover is a withheld or unread document (0).
        # RECORD ONLY (they change nothing in the run): copies naming no cover, and covers that are a
        # document this run withheld or could not read, each {count, docs}.
        "invalid_covers": PR.invalid_covers(rows),
        "uncovered_exclusions": PR.uncovered_exclusions(rows),
    }


def _update_inputs(
    sr: StageRun, md_path: Path, docs: set[str]
) -> tuple[list[str], dict[str, int], dict[str, dict[int, str]]]:
    """(entries, {document: its new exhibit}, {document: {page: text}}).

    Every recorded document's text is read from ``text/<id>.txt`` whether or
    not the update made it an exhibit: a page's class comes from its own text
    and ink. 2026-11-17 (a $0 rehearsal): on an update that added no exhibit,
    73 index and cover pages with text and ink were classed blank, because
    only the new exhibits' text was read."""
    ud = sr.slug_dir / "runs" / sr.unit.unit / "update"
    entries = _entries(md_path.read_text(encoding="utf-8")) if md_path.is_file() else []
    exhibit_of: dict[str, int] = {}
    for r in read_json(ud / "new_exhibits.final.json", []) or []:
        if isinstance(r, dict) and isinstance(r.get("exhibit"), int) and r.get("id"):
            exhibit_of[str(r["id"])] = r["exhibit"]
    texts: dict[str, dict[int, str]] = {}
    for rid in sorted(docs | set(exhibit_of)):
        tp = sr.slug_dir / "text" / f"{rid}.txt"
        texts[rid] = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace")) if tp.is_file() else {}
    return entries, exhibit_of, texts


def _account(sr: StageRun, md_path: Path, key: str) -> dict[str, Any] | None:
    rd = sr.slug_dir / "runs" / sr.unit.unit
    rows = PR.read(rd)
    if not rows:
        sr.log(f"{key}: no page record (runs/<unit>/pages.jsonl); nothing to account")
        return None
    entries, exhibit_of, texts = _update_inputs(sr, md_path, {str(r["doc"]) for r in rows})
    npages = {ex: len(texts.get(rid) or {}) for rid, ex in exhibit_of.items()}
    settled, defects = settle(rows, entries, exhibit_of, texts, _nonrecord(sr.cfg), npages)
    PR.write(rd, settled)
    s = summary(settled, defects)
    # The record's own completeness: a page of a new exhibit with no row is named, never assumed.
    s["unrecorded"] = len(PR.missing(settled, {rid: len(t) for rid, t in texts.items()}))
    # The allowance charge is the pages sent to be read, read off this same record (``update/charged``).
    from ..medchron.update import charged

    s["charge"] = charged.figures(sr.slug_dir, sr.unit.unit)
    cap = charged.capped(sr.slug_dir, sr.unit.unit)
    if cap is not None:  # read more than asked: charged the asked figure, recorded for triage
        s["defects"].append(cap)
    return s


def _save(rd: Path, key: str, body: dict[str, Any]) -> None:
    p = rd / NAME
    rec = read_json(p, {}) if p.is_file() else {}
    rec = rec if isinstance(rec, dict) else {}
    rec[key] = body
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def _log(sr: StageRun, key: str, s: dict[str, Any]) -> None:
    st = s["status"]
    sr.log(
        f"{key}: {s['total']} page(s): {st.get(PR.CITED, 0)} cited, {st.get(PR.CITED_VIA, 0)} cited through a cover, "
        f"{st.get(PR.EXCLUDED, 0)} excluded with evidence, {st.get(PR.READ_UNCITED, 0)} read and uncited "
        f"{s['read_uncited']}, {st.get(PR.UNREAD, 0)} unread; {len(s['defects'])} cite defect(s) recorded"
        + (
            f"; charge: asked {s['charge']['pages_asked']}, {s['charge']['pages_read']} read, "
            f"{s['charge']['pages_charged']} charged, charged_capped {s['charge']['charged_capped']} "
            f"({s['charge']['charged_old_rule']} under the 2026-11-06 rule); "
            f"covers on a withheld or unread document {(s.get('invalid_covers') or {}).get('count', 0)}"
            if s.get("charge")
            else ""
        )
    )


def update_page_account(sr: StageRun) -> int:
    """After the audit: the account on the audited added entries. Always 0."""
    try:
        rd = sr.slug_dir / "runs" / sr.unit.unit
        s = _account(sr, rd / "final-chronology.md", "update_page_account")
        if s is not None:
            _save(rd, "account", s)
            _log(sr, "update_page_account", s)
    except Exception as exc:  # noqa: BLE001 - a record-only account never stops a run; its failure is logged
        sr.log(f"update_page_account: not recorded ({type(exc).__name__}: {str(exc)[:160]})")
    return 0


def update_page_final(sr: StageRun) -> int:
    """After the proofs: the account on the final text, and what the review resolved. Always 0."""
    try:
        rd = sr.slug_dir / "runs" / sr.unit.unit
        first = {(r["doc"], r["page"]) for r in PR.read(rd) if r.get("status") == PR.READ_UNCITED}
        s = _account(sr, rd / "update" / "updated-chronology.md", "update_page_final")
        if s is not None:
            now = {(r["doc"], r["page"]) for r in PR.read(rd) if r.get("status") == PR.CITED}
            s["resolved"] = len(first & now)
            _save(rd, "final", s)
            _log(sr, "update_page_final", s)
    except Exception as exc:  # noqa: BLE001 - a record-only account never stops a run; its failure is logged
        sr.log(f"update_page_final: not recorded ({type(exc).__name__}: {str(exc)[:160]})")
    return 0


def fresh_rows(out_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, dict[int, str]]]:
    """A fresh run's rows: every page of every exhibit PDF, read (the run lane reads the exhibits it built)."""
    rows, exhibit_of, texts = [], {}, {}
    for ex, pdf in sorted(exhibit_paths(out_dir).items()):
        doc = f"exhibit:{ex}"
        try:
            pages = dict(enumerate(S.pdf_pages(pdf), 1))
        except Exception:  # noqa: BLE001 - an unreadable exhibit records no pages; the coverage gate owns it
            pages = {}
        exhibit_of[doc], texts[doc] = ex, pages
        rows += [PR.row(doc, "", p, PR.READ, "exhibit", PR.NONE, "page_account") for p in pages]
    return rows, exhibit_of, texts


def page_account(sr: StageRun) -> int:
    """A fresh run's account, after the coverage gate. RECORD-ONLY (``FRESH_POLICY``)."""
    rd = sr.slug_dir / "runs" / sr.unit.unit
    try:
        rows, exhibit_of, texts = fresh_rows(sr.slug_dir / "out" / sr.unit.unit)
        md = rd / "final-chronology.md"
        entries = _entries(md.read_text(encoding="utf-8")) if md.is_file() else []
        npages = {ex: len(texts.get(d) or {}) for d, ex in exhibit_of.items()}
        settled, defects = settle(rows, entries, exhibit_of, texts, _nonrecord(sr.cfg), npages)
        PR.replace_by(rd, "page_account", settled)
        s = summary(settled, defects)
        s["policy"] = FRESH_POLICY
        _save(rd, "account", s)
        _log(sr, "page_account", s)
    except Exception as exc:  # noqa: BLE001 - a record-only account never stops a run; its failure is logged
        sr.log(f"page_account: not recorded ({type(exc).__name__}: {str(exc)[:160]})")
        return 0
    unexplained = s["read_uncited"].get(UNEXPLAINED, 0)
    return 1 if FRESH_POLICY == HOLD and unexplained else 0
