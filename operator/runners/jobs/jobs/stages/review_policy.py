"""What the full build's pre-delivery review does with its findings before it
decides: drop, note or delete. $0 (the one repair round was removed 2026-11-18).

WHY (2026-10-23). The review could only hold (``proof/review.py``). On the
Curtis chronology (3CH59XBY, 22h29m, eight stops) three holds were the review
grading wording the writer prompts REQUIRE, and every MUST-FIX on checks 2 and
4 was a stop for SMD to clear by hand, though each was a defect the lane could
settle itself by deleting what no entry supports or correcting one sentence
against its page. A chronology must run from one restart to
delivery with no SMD touch. So, in order:

1. A model finding on check 3 (the system's own language) whose subject is
   the firm's required wording (``jobs.wording``) is dropped: it is the
   firm's sentence, never a defect. One that also names system vocabulary is
   kept (the code check finds that anyway).
2. A check 2 finding whose subject is the merge prompt's "The records differ
   on this point." is the house style reporting a real disagreement: it goes
   to the notes as a NOTE. Any other check 2 MUST-FIX stays MUST.
3. A check 4 MUST-FIX (a summary line no entry supports) whose quoted text
   matches exactly ONE summary line (the tables before the entries, the Prior
   Medical History block) has that line deleted from ``final-chronology.md``.
   Deletion only: nothing is written. Otherwise it stays MUST.
4. REMOVED 2026-11-18. There was one repair round here: the update lane's
   finisher rewrote entries for the check 2/4 MUST-FIX left, then the audit
   loop re-graded them. The full chronology is now read entry by entry and
   corrected from its pages before this review (``stages/entry_audit``), and
   nothing edits an entry after that read. A check 2/4 MUST-FIX goes to
   steps 5 and 6 (disclosed in the notes); code's entry dated after today
   holds.

BEFORE those (2026-10-24, the stop audit rows 11 to 13), on the cited page
(``review_evidence``): a check 3 MUST-FIX whose quoted words the cited page
prints verbatim is the record's own wording, a NOTE; a model check 1 MUST-FIX
on one entry is decided by the cited page's patient identifiers: the
client's, a NOTE; another person's, or no one's, the entry is left out by
rule and disclosed (``review-left-out.json`` and the Review Notes), never
naming that person. Code's entry dated after today joins step 4, where the
finisher re-reads the date on its page; it settles only when no entry is
dated after today any more. ``review-policy.json`` counts every step (row 14:
the residual check 2/4 after the repair is measured on replays).

AND TWO MORE (2026-10-24, Pamplona: a job held at this review on 18 MUST-FIX
after the paid audit confirmed 2,468 of 2,468 claims; 7 were the system's
own words in the entries, 11 were check 2 disagreements the repair round
left). Each settles by deletion or disclosure, never by a written word:

3b. After step 3 and before step 4 (``review_settle.scrub_system``): every check 3 MUST-FIX
    left, code's or the model's, has each quoted phrase deleted from the
    entries body only (between "## Medical Chronology" and the next "## "
    heading, so Limitations and the Exhibit List are never touched). Inside
    a provider heading's parenthetical the parenthetical goes and the
    provider's name stays; elsewhere the sentence holding it goes, its
    citation kept while another sentence it covers remains. A date line is
    never edited, and an entry's only cited paragraph is never deleted (the
    finding stays MUST). Settled, a NOTE ("removed before delivery, system
    wording"), only when every phrase was in the body before and none is
    after; ``entries_scoped_final.md`` gets the same edits, best effort.
5.  After step 4 (``review_settle.disclose_differ``): a model check 2 MUST-FIX that quotes
    two statements, or cites two pages, each resolving to a live claim
    whose latest audit row is SUPPORTED (or SUPPORTED_WIDENED), two of them
    on different pages, is two confirmed record statements that disagree: a
    NOTE that says the records differ. Any other check 2 MUST-FIX left with
    a cite (a side that is a diagnosis-table row, a heading, a quote the
    claims do not carry) is FLAGGED for the attorney: a NOTE that says only
    that the review read the statements as possibly disagreeing and names
    the pages to check, never that anything was confirmed. 2026-10-25, the
    Captain: the Operator finishes the chronology end to end, so a
    disagreement it cannot resolve is the attorney's to weigh in the notes,
    not a stop for a person to clear; replayed on Pamplona's recorded
    review, 4 of its 18 were this class.

6.  Last (``review_settle.flag_residual``, 2026-10-28, the Captain: a joint chronology
    held on one provider heading after every other gate passed, the third
    chronology stopped this way): every MUST-FIX still left on checks 2 to 4
    is flagged for the attorney in the Review Notes and the chronology is
    delivered. A finding about wording, a summary line or a disagreement is
    never a stop. A provider heading the reviewer quotes whole and that names
    no provider becomes the firm's "(provider not stated in the record)".

Code integrity findings (an empty chronology, a cited exhibit not delivered,
an entry dated after today) and a check 1 finding the page's identifiers did
not settle (another person's records) are never touched: they hold.
Nothing here invents client text: every step only deletes or re-grades.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .. import wording
from ..medchron.audit import claims as CL
from ..proof import checks, review
from .base import StageRun
from ..text_rules import EXHIBIT_PAGE_MENTION as _EXHIBIT_PAGE, US_DATE as _DATE  # noqa: F401 - review_settle reads them here

#: A double-quoted span a reviewer quotes (straight or curly quotes).
_QUOTED = re.compile(r"\"([^\"]{8,})\"|“([^”]{8,})”")
_DATED = re.compile(r"^\d{2}/\d{2}/\d{4}")
_CHECK = re.compile(r"^\s*(\d{1,2})\b")
#: Any double-quoted span (check 3 quotes short words: "the auditor").
_ANY_QUOTE = re.compile(r"\"([^\"]{3,})\"|“([^”]{3,})”")
#: The policy's counts per review (``_record``).
POLICY_RECORD = "review-policy.json"


def check_no(f: review.Finding) -> int | None:
    m = _CHECK.match(f.check or "")
    return int(m.group(1)) if m else None


def _text(f: review.Finding) -> str:
    return f"{f.finding} {f.cite}"


@dataclass
class Outcome:
    """The findings as the review will record them, and what was done."""

    findings: list[review.Finding]
    dropped: list[review.Finding] = field(default_factory=list)
    deleted_lines: list[str] = field(default_factory=list)
    repaired: int = 0
    changed: bool = False
    left_out: int = 0
    renamed: int = 0


def _names_system(f: review.Finding) -> bool:
    t = _text(f)
    return bool(checks.system_vocabulary(t) or checks.extraction_vocabulary(t)) or "chunk" in t.lower()


def wording_and_notes(found: list[review.Finding]) -> tuple[list[review.Finding], list[review.Finding]]:
    """Steps 1 and 2: (the findings kept, the check 3 findings dropped)."""
    kept: list[review.Finding] = []
    dropped: list[review.Finding] = []
    for f in found:
        n = check_no(f)
        if f.by == "model" and n == 3 and wording.subjects(_text(f)) and not _names_system(f):
            dropped.append(f)
            continue
        if f.by == "model" and n == 2 and wording.names_records_differ(_text(f)):
            f = replace(f, grade=review.NOTE)
        kept.append(f)
    return kept, dropped


# ---- step 3: a summary line no entry supports, deleted ----------------------------
def _norm(s: str) -> str:
    return " ".join(s.replace("*", "").replace("|", " ").split()).lower()


def summary_lines(md: str) -> list[int]:
    """Indices of the lines a check 4 finding may delete: the summary tables
    before the entries and the Prior Medical History block at their head.
    Never a heading, a table's header or rule, an entry's date or provider
    line, or anything in the entries themselves."""
    out: list[int] = []
    body = header = False
    for i, ln in enumerate(md.splitlines()):
        s = ln.strip()
        if s.startswith("## "):
            if body:
                break  # past the entries' section: the Exhibit List and Limitations are not summaries
            body, header = s == CL.BODY_START, False
            continue
        if i == 0 or not s:
            continue
        if body:
            if _DATED.match(s):
                break  # the entries start
            # The Prior Medical History heading and its disclosures stay.
            if s.lower() != "prior medical history" and not s.startswith("["):
                out.append(i)
        elif s.startswith("|"):
            if not header or set(s) <= set("|-: "):
                header = True  # a table's header row, and its rule when it has one
                continue
            out.append(i)
    return out


def delete_supported(md: str, f: review.Finding) -> tuple[str, str | None]:
    """(md, the line deleted) when the finding's quoted text matches exactly
    one summary line; (md, None) otherwise."""
    quotes = [a or b for a, b in _QUOTED.findall(_text(f))]
    if not quotes:
        return md, None
    lines = md.splitlines(keepends=True)
    hits = {i for i in summary_lines(md) for q in quotes if _norm(q) and _norm(q) in _norm(lines[i])}
    if len(hits) != 1:
        return md, None
    i = hits.pop()
    gone = lines[i].rstrip("\n")
    del lines[i]
    return re.sub(r"\n{3,}", "\n\n", "".join(lines)), gone


# ---- step 4: removed ---------------------------------------------------------------
#: The review's repair round once took this callable (the audit loop to its
#: gate); the round is gone (2026-11-18) and ``review.run`` still names it.
Audit = Callable[[StageRun], int]


def _phrases(f: review.Finding) -> list[str]:
    """The words a check 3 finding says are the system's: the quoted span of
    a code finding, every quoted span of a model finding."""
    if f.by == "code":
        if "system marker" in f.finding:
            return []
        m = re.search(r'"([^"]+)"', f.finding)
        return [m.group(1)] if m else []
    return [a or b for a, b in _ANY_QUOTE.findall(_text(f))]


def on_record(sr: StageRun, doc: Path, out: Outcome, pages: Any, stats: dict[str, int]) -> None:
    """Steps 0a and 0b (2026-10-24, the stop audit rows 11 and 12), on the
    cited pages (``review_evidence``):

    * a check 3 MUST-FIX (code or model) whose every quoted phrase is printed
      verbatim on the page each sentence carrying it cites is the record's
      wording: a NOTE;
    * a model check 1 MUST-FIX on one entry: the client's page, or a page
      naming no one, makes it a NOTE and keeps the entry (a record the map
      read is never dropped on the review's suspicion alone); only a page
      naming another person has the entry left out by rule and disclosed
      (never naming that person)."""
    from . import review_evidence as EV

    md = before = doc.read_text(encoding="utf-8")
    out.findings += EV.reader_findings(sr, md, out.findings)
    for k, f in enumerate(out.findings):
        if f.grade != review.MUST:
            continue
        n = check_no(f)
        if n == 3 and (f.by == "model" or f.cite == "the chronology"):
            ph = _phrases(f)
            if ph and all(EV.verbatim_on_record(md, p, pages) for p in ph):
                out.findings[k] = replace(
                    f, grade=review.NOTE, finding=f"the record's own wording, printed on the cited page: {f.finding}"
                )
                stats["system_words_on_record"] += 1
        elif n == 1 and f.by == "model":
            block = EV.entry_for(md, _text(f))
            if block is None:
                continue
            cited = EV.cited_pages(block[2], _text(f))
            kind = EV.whose_cited(sr, pages, cited)
            if kind != "other":
                # The client's page, or a page naming no one: the entry is a
                # record the map read, and suspicion alone never drops it.
                whom = "which is the client's" if kind == "client" else "which names no other person; the entry is kept"
                out.findings[k] = replace(
                    f, grade=review.NOTE, finding=f"checked against the cited page, {whom}: {f.finding}"
                )
                stats["identity_client" if kind == "client" else "identity_unnamed"] += 1
                continue
            reason = EV.LEFT_OUT_REASON
            EV.leave_out(sr, doc, block, cited, reason)
            md = doc.read_text(encoding="utf-8")
            out.changed = True
            date_line = block[2].split("\n", 1)[0].strip()
            out.findings[k] = replace(
                f, grade=review.NOTE, finding=f"left out before delivery by rule: the entry dated {date_line} {reason}"
            )
            stats["identity_left_out"] += 1
            out.left_out += 1
            sr.log(f"review: the {date_line} entry left out by rule (its cited page: {kind})")
    if stats["identity_left_out"]:
        # The summary tables count the entries: computed again from those left.
        from . import review_tables

        doc.write_text(review_tables.rederive(sr, before, md), encoding="utf-8")
        sr.log("review: Treatment Timeline and Diagnostic Highlights re-derived from the remaining entries")


def _repairable(f: review.Finding) -> bool:
    """A MUST-FIX the removed repair round once settled (the model's check 2
    or 4, code's entry dated after today), still counted in
    ``review-policy.json`` as pending and residual."""
    if f.grade != review.MUST:
        return False
    if f.by == "model":
        return check_no(f) in (2, 4)
    return check_no(f) == 5 and "future date" in f.check


def settle(
    sr: StageRun,
    doc: Path | None,
    found: list[review.Finding],
    *,
    repair: bool = False,
    verify: Any = None,
    images: Any = None,
    audit: Audit | None = None,
    today: date | None = None,
) -> Outcome:
    """Every step in order. ``doc`` is the full build's ``final-chronology.md``
    (None on an update, which has its own repair before this review and only
    gets steps 1 and 2). ``repair``, ``verify``, ``images``, ``audit`` and
    ``today`` fed the removed repair round; callers still pass them and they
    change nothing."""
    del repair, verify, images, audit, today
    from . import review_settle

    kept, dropped = wording_and_notes(found)
    out = Outcome(kept, dropped)
    if doc is None or not doc.is_file():
        review_settle.flag_residual(out, {})
        return out
    stats: dict[str, int] = dict.fromkeys(
        (
            "system_words_on_record",
            "identity_client",
            "identity_unnamed",
            "identity_left_out",
            "summary_deleted",
            "system_scrubbed",
            "differ_disclosed",
            "differ_flagged",
            "residual_flagged",
        ),
        0,
    )
    from . import review_evidence as EV

    pages = EV.Pages(sr)
    try:
        on_record(sr, doc, out, pages, stats)
    finally:
        pages.close()
    md = doc.read_text(encoding="utf-8")
    for k, f in enumerate(out.findings):
        if f.by == "model" and f.grade == review.MUST and check_no(f) == 4:
            md, gone = delete_supported(md, f)
            if gone is not None:
                out.deleted_lines.append(gone)
                out.findings[k] = replace(
                    f, grade=review.NOTE, finding=f"removed before delivery, no entry supports it: {f.finding}"
                )
    if out.deleted_lines:
        doc.write_text(md, encoding="utf-8")
        out.changed = True
        _sync_entries(sr, out.deleted_lines)
        stats["summary_deleted"] = len(out.deleted_lines)
        sr.log(f"review: {len(out.deleted_lines)} summary line(s) no entry supports, deleted")
    review_settle.scrub_system(sr, doc, out, stats)
    pend = [k for k, f in enumerate(out.findings) if _repairable(f)]
    before_rounds = {str(check_no(out.findings[k])): 0 for k in pend}
    for k in pend:
        before_rounds[str(check_no(out.findings[k]))] += 1
    # Step 4, the one repair round, is gone (2026-11-18): the review reads and
    # settles in code, and no edit to an entry follows the entry read
    # (``stages/entry_audit``). What it once repaired is disclosed by steps 5
    # and 6, and code's entry dated after today holds.
    review_settle.disclose_differ(sr, doc, out, stats)
    review_settle.flag_residual(out, stats)
    _record(sr, out, pend, before_rounds, stats)
    return out


def _record(sr: StageRun, out: Outcome, pend: list[int], before: dict[str, int], stats: dict[str, int]) -> None:
    """What the policy did, counted (the stop audit row 14: the residual
    check 2/4 MUST-FIX after the one repair round is measured on replays
    before the rule changes). Logged, and written to ``review-policy.json``
    in the run dir."""
    residual: dict[str, int] = {}
    for k in pend:
        f = out.findings[k]
        if f.grade == review.MUST:
            residual[str(check_no(f))] = residual.get(str(check_no(f)), 0) + 1
    rec = {
        **stats,
        "repair_pending_by_check": before,
        "repair_settled": out.repaired,
        "repair_residual_by_check": residual,
        "must_fix_left": sum(1 for f in out.findings if f.grade == review.MUST),
    }
    sr.log(
        "review policy: "
        + ", ".join(f"{k}={v}" for k, v in stats.items())
        + f"; repair pending {before}, settled {out.repaired}, residual {residual}"
    )
    rd = sr.slug_dir / "runs" / sr.unit.unit
    rd.mkdir(parents=True, exist_ok=True)
    (rd / POLICY_RECORD).write_text(json.dumps(rec, indent=1), encoding="utf-8")


def _sync_entries(sr: StageRun, gone: list[str]) -> None:
    """Best effort, as the audit's repair does: the same line out of
    ``entries_scoped_final.md`` (the Prior Medical History block lives there
    too), so a rebuild does not bring it back."""
    p = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"
    if not p.is_file():
        return
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    keep = [ln for ln in lines if ln.rstrip("\n") not in gone]
    if len(keep) != len(lines):
        p.write_text(re.sub(r"\n{3,}", "\n\n", "".join(keep)), encoding="utf-8")


def rebill(sr: StageRun) -> bool:
    """The independent review's N2 (2026-10-24): billing_chart and
    billing_docx run before this review and take their provider rows from the
    Treatment Timeline, so an entry the review leaves out kept its provider's
    row and its matched bills in the worksheet and the grand total. Both run
    again ($0, in-process) on the chronology as it now stands: a provider with
    no entry left loses its row, and a bill that then matches no row is shown
    as printed under its own subtotal, outside the grand total. False when
    either refuses (nothing is filed). True with nothing to do when billing
    never ran for this unit."""
    from . import billing_chart as _bc, billing_docx as _bd

    sfx = f"-{sr.unit.unit}" if sr.job.joint else ""
    if not (sr.slug_dir / f"billing_chart{sfx}.json").is_file():
        return True
    if _bc.run(sr) != 0:
        return False
    # The worksheet as it was goes first: billing_docx writes none when no row
    # is left, and a stale one must not be filed.
    glob = str(sr.cfg.get("delivery", "worksheet_glob") or "* - Medical Billing Worksheet *.docx")
    for old in (sr.slug_dir / "out" / sr.unit.unit).glob(glob):
        old.unlink()
    if _bd.run(sr) != 0:
        return False
    sr.log("review: billing chart and worksheet rebuilt from the chronology as it now stands")
    return True


def timeline_providers(doc: Path | None) -> set[str]:
    """The provider cells of the chronology's Treatment Timeline (empty when
    there is no document or no timeline)."""
    if doc is None or not doc.is_file():
        return set()
    text = doc.read_text(encoding="utf-8")
    if "## Treatment Timeline" not in text:
        return set()
    from .billing_chart import Matcher

    return {r["provider"] for r in Matcher.timeline(text)}


def needs_rebill(pol: Outcome, before: set[str], doc: Path | None) -> bool:
    """True when the review took a provider off the Treatment Timeline: an
    entry left out, or a timeline row deleted under check 4 (the re-review's
    N2 gap, 2026-10-24: the worksheet kept that provider's row)."""
    return bool(pol.left_out) or bool(before - timeline_providers(doc))


def package(
    sr: StageRun,
    rd: Path,
    found: list[review.Finding],
    rows: list[dict[str, Any]],
    *,
    update: bool,
    notes_tag: str,
    repair: bool = True,
    **inject: Any,
) -> tuple[list[review.Finding], list[dict[str, Any]] | None]:
    """The findings after ``review_policy`` (drop, note, delete, repair once),
    and the manifest rows the review decides on: re-rendered and re-manifested
    when the chronology changed, so what is filed is what was settled. Rows
    None when the re-render fails (nothing is filed; review.EXIT_RERENDER)."""
    from . import limitations as _limitations, manifest as _manifest, render as _render

    doc = None if update else rd / "final-chronology.md"
    spine_before = timeline_providers(doc)
    pol = settle(sr, doc, found, repair=repair, **inject)
    if pol.dropped:
        sr.log(
            f"review: {len(pol.dropped)} finding(s) on the firm's required wording dropped: "
            + "; ".join(f.finding[:80] for f in pol.dropped)
        )
    # build_doc wrote Records Reviewed and Limitations before this review; an
    # entry the review left out (``review-left-out.json``) is disclosed there
    # only once the section is written again from the files as they stand.
    # Full build only (an update has no ``doc``); a refresh that changes the
    # section re-renders even when nothing else changed.
    # A provider heading the scrub shortened leaves the timeline under its old
    # name too, so needs_rebill covers it (and pol.renamed says so outright).
    if (needs_rebill(pol, spine_before, doc) or pol.renamed) and not rebill(sr):
        sr.log("review: the billing worksheet did not rebuild after the timeline changed; nothing was filed")
        return pol.findings, None
    if doc is not None and doc.is_file() and _limitations.refresh(doc, sr.slug_dir, sr.unit.unit, sr.log, sr.cfg):
        sr.log("review: Records Reviewed and Limitations rewritten from the files as they stand")
        pol.changed = True
    if not pol.changed:
        return pol.findings, rows
    if _render.run(sr) != 0 or _manifest.run(sr) != 0:
        sr.log("review: the settled chronology did not re-render; nothing was filed")
        return pol.findings, None
    mpath = sr.slug_dir / "out" / sr.unit.unit / "upload_manifest.json"
    new = [m for m in json.loads(mpath.read_text(encoding="utf-8")) if notes_tag not in m["name"]]
    sr.log(f"review: the chronology was settled before delivery and re-rendered ({len(new)} file(s))")
    return pol.findings, new


#: How ``stages/audit_loop`` records a claim it removed at the audit gate.
GATE_DROP = "dropped at the audit gate"


def gate_drops(out: Path) -> list[str]:
    """The notes section for every claim the audit gate removed (2026-10-23:
    a claim still not SUPPORTED at the gate is dropped instead of holding the
    job, and nothing is dropped without the attorney being told)."""
    p = out / "audit-dropped-claims.json"
    if not p.is_file():
        return []
    rows = [r for r in json.loads(p.read_text(encoding="utf-8")).get("claims") or [] if r.get("how") == GATE_DROP]
    if not rows:
        return []
    lines = ["", "## Sentences removed before delivery", ""]
    for r in rows:
        where = f"Exhibit {r.get('exhibit')}" + (f", p. {r.get('page_spec')}" if r.get("page_spec") else "")
        lines.append(
            f"- A sentence citing {where} was removed because the audit could not confirm it on the cited page: "
            f'"{str(r.get("claim") or "").strip()}"'
        )
    return lines
