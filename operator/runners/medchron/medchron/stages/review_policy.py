"""What the full build's pre-delivery review does with its findings before it
decides: drop, note, delete, or repair once. $0 except the one repair round.

WHY (2026-10-23). The review could only hold (``proof/review.py``). On the
Curtis chronology (3CH59XBY, 22h29m, eight stops) three holds were the review
grading wording the writer prompts REQUIRE, and every MUST-FIX on checks 2 and
4 was a stop for SMD to clear by hand, though each was a defect the lane could
settle itself by deleting what no entry supports or correcting one sentence
against its page. Chris's Pamplona chronology must run from one restart to
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

Check 1 (another person's records) and code integrity findings (an empty
chronology, a cited exhibit not delivered) are never touched: they hold.
Nothing here invents client text: steps 1-3 only delete or re-grade, and step
4's sentences are the update lane's grounded, audited corrections.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
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


def _names_system(f: review.Finding) -> bool:
    t = _text(f)
    return bool(checks.system_vocabulary(t)) or "chunk" in t.lower()


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
    from ..update import finisher as FN, review_repair as RR
    from . import audit_loop, update_review as UR
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
    c = UR._ctx(sr, images, lambda _x, _p: None, verify)
    c.rnd = 1
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


def settle(
    sr: StageRun,
    doc: Path | None,
    found: list[review.Finding],
    *,
    repair: bool,
    verify: Any = None,
    images: Any = None,
    audit: Audit | None = None,
) -> Outcome:
    """Every step in order. ``doc`` is the full build's ``final-chronology.md``
    (None on an update, which has its own repair before this review and only
    gets steps 1 and 2)."""
    kept, dropped = wording_and_notes(found)
    out = Outcome(kept, dropped)
    if doc is None or not doc.is_file():
        return out
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
        sr.log(f"review: {len(out.deleted_lines)} summary line(s) no entry supports, deleted")
    pend = [
        k for k, f in enumerate(out.findings) if f.by == "model" and f.grade == review.MUST and check_no(f) in (2, 4)
    ]
    if repair and pend:
        before = doc.read_text(encoding="utf-8")
        done, why = repair_round(sr, doc, [out.findings[k] for k in pend], verify=verify, images=images, audit=audit)
        sr.log(f"review repair round: {why}")
        for j in done:
            k = pend[j]
            f = out.findings[k]
            out.findings[k] = replace(f, grade=review.NOTE, finding=f"corrected before delivery: {f.finding}")
        out.repaired = len(done)
        out.changed = out.changed or doc.read_text(encoding="utf-8") != before
    return out


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
    None when the re-render fails (nothing is filed; a rerun reviews again)."""
    from . import manifest as _manifest, render as _render

    doc = None if update else rd / "final-chronology.md"
    pol = settle(sr, doc, found, repair=True, **inject)
    if pol.dropped:
        sr.log(
            f"review: {len(pol.dropped)} finding(s) on the firm's required wording dropped: "
            + "; ".join(f.finding[:80] for f in pol.dropped)
        )
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
