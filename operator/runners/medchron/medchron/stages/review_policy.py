"""What the full build's pre-delivery review does with its findings before it
decides: drop, note, delete, or repair once. $0 except the one repair round.

WHY (2026-10-23). The review could only hold (``proof/review.py``). On the
Curtis chronology (3CH59XBY, 22h29m, eight stops) three holds were the review
grading wording the writer prompts REQUIRE, and every MUST-FIX on checks 2 and
4 was a stop for SMD to clear by hand, though each was a defect the lane could
settle itself by deleting what no entry supports or correcting one sentence
against its page. A chronology must run from one restart to
delivery with no SMD touch. So, in order:

1. A model finding on check 3 (the system's own language) whose subject is
   the firm's required wording (``medchron.wording``) is dropped: it is the
   firm's sentence, never a defect. One that also names system vocabulary is
   kept (the code check finds that anyway).
2. A check 2 finding whose subject is the merge prompt's "The records differ
   on this point." is the house style reporting a real disagreement: it goes
   to the notes as a NOTE. Any other check 2 MUST-FIX stays MUST.
3. A check 4 MUST-FIX (a summary line no entry supports) whose quoted text
   matches exactly ONE summary line (the tables before the entries, the Prior
   Medical History block) has that line deleted from ``final-chronology.md``.
   Deletion only: nothing is written. Otherwise it stays MUST.
4. ONE repair round for the check 2/4 MUST-FIX left, with the update lane's
   finisher (``update/finisher.py`` via ``stages/update_review``): anchored to
   the entry the finding cites, every entry anchored ``N<k>`` (an empty
   delivered base), each sentence it writes audited on its page and reverted
   when unsupported, then the audit loop to its gate. A finding the round
   settled is a NOTE ("corrected before delivery"); one it did not stays
   MUST and the job holds (an integrity stop).

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
    NOTE that says the records differ. A side that is no confirmed claim (a
    diagnosis-table row, a heading) keeps it MUST.

Code integrity findings (an empty chronology, a cited exhibit not delivered)
are never touched: they hold.
Nothing here invents client text: steps 1-3b and 5 only delete or re-grade,
and step 4's sentences are the update lane's grounded, audited corrections.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .. import wording
from ..audit import claims as CL
from ..proof import checks, review
from .base import StageRun

#: A double-quoted span a reviewer quotes (straight or curly quotes).
_QUOTED = re.compile(r"\"([^\"]{8,})\"|“([^”]{8,})”")
_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_EXHIBIT_PAGE = re.compile(r"(?i)\bExhibit\s+(\d+)(?:\s*[,-]?\s*p(?:age|p)?\.?\s*(\d+))?")
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


# ---- step 4: one repair round -------------------------------------------------------
def _provider(entry_text: str) -> str:
    """The provider an entry's second line names, lower case ('' when none)."""
    lines = entry_text.split("\n")
    return lines[1].split("|")[0].strip().lower() if len(lines) > 1 else ""


def anchor(f: review.Finding, ents: list[Any]) -> tuple[str, int | None, int | None] | None:
    """(the entry anchor ``N<k>``, exhibit, page) the finding is on, from the
    date (and the provider, when the date has several entries) it cites; None
    when no single entry is named. The page is the one the finding cites, else
    the entry's own first cited page of that exhibit."""
    t = _text(f).lower()
    for d in dict.fromkeys(_DATE.findall(f.cite) + _DATE.findall(f.finding)):
        same = [e for e in ents if e.text.startswith(d)]
        if len(same) > 1:
            same = [e for e in same if _provider(e.text) and _provider(e.text) in t]
        if len(same) != 1:
            continue
        e = same[0]
        m = _EXHIBIT_PAGE.search(f.cite) or _EXHIBIT_PAGE.search(f.finding)
        ex = int(m.group(1)) if m else None
        page = int(m.group(2)) if m and m.group(2) else None
        own = [c for c in e.claims if ex is None or c.exhibit == ex]
        if page is None and own:
            ex, page = own[0].exhibit, own[0].pages[0]
        return e.anchor, ex, page
    return None


Audit = Callable[[StageRun], int]


def repair_round(
    sr: StageRun,
    doc: Path,
    musts: list[review.Finding],
    *,
    verify: Any = None,
    images: Any = None,
    audit: Audit | None = None,
) -> tuple[set[int], str]:
    """The update lane's finisher, once, over the full build's chronology:
    (the indices into `musts` it settled, a note for the log). The finisher's
    sentences are graded on their pages and reverted when unsupported; the
    audit loop then runs to its gate. Nothing settles on a grading refusal or
    an audit that does not pass."""
    from ..update import review_repair as RR
    from . import update_review as UR
    from .update_base import headings

    keep, heads = UR._keep(sr), headings(sr.cfg)
    md = doc.read_text(encoding="utf-8")
    ents = RR.anchor_entries(md, keep, heads)
    todo: list[RR.Finding] = []
    idx: list[int] = []
    for i, f in enumerate(musts):
        at = anchor(f, ents)
        if at is None:
            continue
        g = RR.Finding(RR.MUST, f.check, at[0], at[1], at[2], f.finding, "none", "", "")
        g.fid = f"F{len(todo) + 1}"
        todo.append(g)
        idx.append(i)
    if not todo:
        return set(), "no finding names one entry to correct"
    from . import review_evidence as EV

    # The page text a redate is checked against (2026-10-24: an entry dated
    # after today is re-read on its page; with no text, no date moves).
    pages = EV.Pages(sr)
    c = UR._ctx(sr, images, lambda x, p: pages.text(x, p) or None, verify)
    c.rnd = 1
    try:
        return _finish(sr, doc, md, musts, todo, idx, ents, c, audit)
    finally:
        pages.close()


def _finish(
    sr: StageRun,
    doc: Path,
    md: str,
    musts: list[review.Finding],
    todo: list[Any],
    idx: list[int],
    ents: list[Any],
    c: Any,
    audit: Audit | None,
) -> tuple[set[int], str]:
    """The finisher's edits on the document, graded, then the audit loop."""
    from ..update import finisher as FN
    from . import audit_loop, update_review as UR

    try:
        edits, left = UR._finish_calls(c, todo, ents, [])
        new_md, _ = UR._apply(c, md, edits, [])
        bad, _ = FN.failed(edits, c.verify(md, new_md)) if new_md != md else ([], 0)
        if bad:
            ids = {id(e) for e in bad}
            for e in edits:
                if id(e) in ids:
                    e.outcome = "reverted: the audit did not find the edited sentence on its page"
                elif e.outcome == "applied":
                    e.outcome = ""
            new_md, _ = UR._apply(c, md, edits, [])
    except UR.GradeFailed as exc:
        doc.write_text(md, encoding="utf-8")
        return set(), f"the grader returned no verdict ({exc}); nothing kept"
    FN.settle(todo, edits, left)
    if new_md == md:
        why = "; ".join(dict.fromkeys(g.outcome for g in todo if g.outcome))[:300]
        return set(), "the finisher changed nothing" + (f" ({why})" if why else "")
    doc.write_text(new_md, encoding="utf-8")
    rc = (audit or audit_loop.run)(sr)
    if rc != 0:
        return set(), f"the audit after the repair exited {rc}; nothing is counted settled"
    done = {idx[j] for j, g in enumerate(todo) if UR._closed(g)}
    return done, f"{len(done)} of {len(musts)} settled by the finisher, {c.calls} call(s)"


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
            kind = EV.whose(sr, [pages.text(ex, pg) for ex, pg in cited])
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


def _future_still(md: str, f: review.Finding, today: date | None) -> bool:
    """True when the entry a future-date finding names is still dated after
    today: its date still heads an entry and that date is after today."""
    if today is None:
        return False
    for d in _DATE.findall(f.finding + " " + f.cite):
        mo, dy, yr = (int(x) for x in d.split("/"))
        try:
            when = date(yr, mo, dy)
        except ValueError:
            continue
        if when > today and any(ln.strip() == d for ln in md.splitlines()):
            return True
    return False


def _repairable(f: review.Finding) -> bool:
    """A MUST-FIX the one repair round may settle: the model's check 2 or 4,
    and code's entry dated after today (a misread year the finisher re-reads
    on the page, 2026-10-24)."""
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
    repair: bool,
    verify: Any = None,
    images: Any = None,
    audit: Audit | None = None,
    today: date | None = None,
) -> Outcome:
    """Every step in order. ``doc`` is the full build's ``final-chronology.md``
    (None on an update, which has its own repair before this review and only
    gets steps 1 and 2)."""
    kept, dropped = wording_and_notes(found)
    out = Outcome(kept, dropped)
    if doc is None or not doc.is_file():
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
    from . import review_settle

    review_settle.scrub_system(sr, doc, out, stats)
    pend = [k for k, f in enumerate(out.findings) if _repairable(f)]
    before_rounds = {str(check_no(out.findings[k])): 0 for k in pend}
    for k in pend:
        before_rounds[str(check_no(out.findings[k]))] += 1
    if repair and pend:
        before = doc.read_text(encoding="utf-8")
        done, why = repair_round(sr, doc, [out.findings[k] for k in pend], verify=verify, images=images, audit=audit)
        sr.log(f"review repair round: {why}")
        after = doc.read_text(encoding="utf-8")
        for j in done:
            k = pend[j]
            f = out.findings[k]
            if f.by == "code" and _future_still(after, f, today):
                continue  # the date is still after today: unsettled, it holds
            out.findings[k] = replace(f, grade=review.NOTE, finding=f"corrected before delivery: {f.finding}")
            out.repaired += 1
        out.changed = out.changed or after != before
    review_settle.disclose_differ(sr, doc, out, stats)
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


def package(
    sr: StageRun,
    rd: Path,
    found: list[review.Finding],
    rows: list[dict[str, Any]],
    *,
    update: bool,
    notes_tag: str,
    **inject: Any,
) -> tuple[list[review.Finding], list[dict[str, Any]] | None]:
    """The findings after ``review_policy`` (drop, note, delete, repair once),
    and the manifest rows the review decides on: re-rendered and re-manifested
    when the chronology changed, so what is filed is what was settled. Rows
    None when the re-render fails (nothing is filed; review.EXIT_RERENDER)."""
    from . import limitations as _limitations, manifest as _manifest, render as _render

    doc = None if update else rd / "final-chronology.md"
    pol = settle(sr, doc, found, repair=True, **inject)
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
    if pol.left_out and not rebill(sr):
        sr.log("review: the billing worksheet did not rebuild after an entry was left out; nothing was filed")
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
