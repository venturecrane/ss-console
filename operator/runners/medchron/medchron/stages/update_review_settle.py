"""The update review settles by the chronology review's rules before it can
hold (2026-10-25). $0: deletion, disclosure and re-grading only, no model call.

WHY. The update's independent review (``stages/update_review``) runs BEFORE
the shared pre-delivery ``review``, and it held on every must-fix its
finisher could not correct. The chronology lane's own review stopped holding
on two classes this week (``review_policy`` steps 3b and 5): the system's own
words in the entries are deleted (``review_settle.scrub_system``), and a
disagreement between statements the audit confirmed is disclosed, or flagged
for the attorney (``review_settle.disclose_differ``). The Captain's rule: the
Operator finishes the chronology end to end, and a stop that needs SMD's hand
is a lane defect. So the same two rules run here, on the update's open
must-fix findings, before the hold. The functions are the chronology's own,
reused, never copied; this module only translates an update finding into the
shape they read:

* class 6 (process or system wording) quoting the words: a check 3 finding
  on its entry. The scrub runs on a scratch copy, one finding at a time, and
  is kept only when it changes no audited claim's words (every claim left is
  one the audit already graded), leaves every new exhibit cited, and drops no
  value; otherwise the finding stays open.
* class 1 or 8 (a wrong value, a paired field) on a claim the audit
  CONFIRMED on its own cited page, where the reviewer relied on a different
  page: two pages of the record disagree. It becomes a check 2 finding naming
  both pages, and ``disclose_differ`` discloses it (both sides confirmed) or
  flags it for the attorney.

A finding no rule settles still holds, as before. Every settled finding is
recorded in the review record (``settled_by_rule``) and written to
``out/<unit>/update-review-settled.json``, which the pre-delivery review puts
in the attorney's notes file (``review._notes`` via ``notes``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..audit import claims as CL
from ..proof import review
from ..update import review_repair as RR, values as V
from . import review_policy, review_settle
from .base import StageRun, read_json
from .update_base import headings

SETTLED = "update-review-settled.json"
NOTES_HEADING = "Independent review of the update"
_KLASS = re.compile(r"^\s*(\d+)")


def plain(f: RR.Finding) -> str:
    where = f" (Exhibit {f.exhibit}, page {f.page})" if f.exhibit and f.page else ""
    return f"entry {f.anchor or 'unnamed'}{where}: {f.problem or f.klass}"


def hold_line(open_: list[RR.Finding]) -> str:
    first = "; ".join(plain(f) for f in open_[:4])
    return (
        f"update_held: the independent review of the update left {len(open_)} must-fix finding(s) the finisher "
        f"could not correct against the pages; SMD finishes it: {first}"
    )[:480]


def _no(f: RR.Finding) -> str:
    m = _KLASS.match(f.klass or "")
    return m.group(1) if m else ""


def _keep(sr: StageRun) -> set[int]:
    from . import update_review as UR

    return UR._keep(sr)


def _entries(sr: StageRun, doc: Path) -> dict[str, RR.Entry]:
    md = doc.read_text(encoding="utf-8")
    return {e.anchor: e for e in RR.anchor_entries(md, _keep(sr), headings(sr.cfg))}


def _date(e: RR.Entry) -> str:
    return e.text.split("\n", 1)[0].strip()


def _system(f: RR.Finding, e: RR.Entry | None) -> review.Finding | None:
    """A class 6 finding as the chronology review's check 3 on its entry."""
    if e is None or _no(f) != "6":
        return None
    return review.Finding(review.MUST, "3 the system's own language", f.problem, f"entry {_date(e)}", "model")


def _differ(f: RR.Finding, e: RR.Entry | None, confirmed: list[tuple[str, dict[str, Any]]]) -> review.Finding | None:
    """A wrong value on a claim the audit confirmed on its own page, read by
    the reviewer on a different page, as the chronology review's check 2."""
    if e is None or _no(f) not in ("1", "8") or not f.exhibit or not f.page:
        return None
    claim = next((c for c in e.claims if c.anchor == f.anchor), None)
    if claim is None or (f.exhibit, f.page) in {(claim.exhibit, p) for p in claim.pages}:
        return None
    when = _date(e)
    if not any(
        w == when
        and int(c["exhibit"]) == claim.exhibit
        and set(CL.parse_pages(c["page_spec"]) or [1]) & set(claim.pages)
        for w, c in confirmed
    ):
        return None
    words = (f.problem or f.klass).replace('"', "'").replace("“", "'").replace("”", "'")
    text = (
        f"{words}; the entry cites Exhibit {claim.exhibit} p. {claim.pages[0]}, "
        f"and the review relied on Exhibit {f.exhibit} p. {f.page}"
    )
    return review.Finding(review.MUST, "2 entries that contradict each other", text, f"entry {when}", "model")


def _scrub_one(sr: StageRun, doc: Path, g: review.Finding) -> review.Finding | None:
    """``scrub_system`` on a scratch copy for one finding; the copy replaces
    the document only when the scrub settled it and every claim left is one
    the audit already graded, every exhibit stays cited, and no value is lost."""
    before = doc.read_text(encoding="utf-8")
    tmp = doc.with_name(".update-review-scrub.md")
    tmp.write_text(before, encoding="utf-8")
    side = sr.slug_dir / "runs" / sr.unit.unit / "entries_scoped_final.md"  # scrub_system's best-effort sync
    kept = side.read_bytes() if side.is_file() else None
    out = review_policy.Outcome([g])
    try:
        review_settle.scrub_system(sr, tmp, out, {"system_scrubbed": 0})
        after = tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)
        if kept is not None:
            side.write_bytes(kept)
    if out.findings[0].grade == review.MUST or after == before:
        return None
    keep = _keep(sr)
    old = CL.extract_claims(CL.body_of(before), keep)
    new = CL.extract_claims(CL.body_of(after), keep)
    if {c["key"] for c in new} - {c["key"] for c in old}:
        return None  # a claim's words changed: it would go to the filing unaudited
    if {c["exhibit"] for c in old} - {c["exhibit"] for c in new} or V.lost(CL.body_of(before), CL.body_of(after)):
        return None
    doc.write_text(after, encoding="utf-8")
    return out.findings[0]


def settle(sr: StageRun, doc: Path, open_: list[RR.Finding], rec: dict[str, Any]) -> list[RR.Finding]:
    """The open must-fix findings no rule settles (they hold). Each settled
    one is marked, recorded in `rec` and in ``update-review-settled.json``."""
    if not open_ or not doc.is_file():
        return open_
    done: dict[int, review.Finding] = {}
    ents = _entries(sr, doc)
    for i, f in enumerate(open_):
        g = _system(f, ents.get(f.anchor.split(".")[0]))
        note = _scrub_one(sr, doc, g) if g is not None else None
        if note is not None:
            done[i] = note
    ents = _entries(sr, doc)
    confirmed = review_settle.confirmed_claims(sr, doc.read_text(encoding="utf-8"))
    cand = {
        i: g
        for i, f in enumerate(open_)
        if i not in done and (g := _differ(f, ents.get(f.anchor.split(".")[0]), confirmed)) is not None
    }
    if cand:
        out = review_policy.Outcome(list(cand.values()))
        review_settle.disclose_differ(sr, doc, out, {"differ_disclosed": 0, "differ_flagged": 0})
        done |= {i: g for i, g in zip(cand, out.findings) if g.grade != review.MUST}
    if not done:
        return open_
    for i, g in done.items():
        open_[i].outcome = f"settled by rule before delivery: {g.finding}"[:400]
    rows = [{"grade": g.grade, "finding": g.finding, "cite": g.cite} for g in done.values()]
    rec["settled_by_rule"] = list(rec.get("settled_by_rule") or []) + rows
    out_dir = sr.slug_dir / "out" / sr.unit.unit
    (out_dir / SETTLED).write_text(json.dumps(rec["settled_by_rule"], indent=1), encoding="utf-8")
    sr.log(f"update review: {len(done)} must-fix settled by the chronology review's rules (scrubbed or disclosed)")
    return [f for i, f in enumerate(open_) if i not in done]


def resume_held(sr: StageRun, rec: dict[str, Any], record: Path, doc: Path, sha: Any) -> int:
    """A held update review on resume: its open findings settled again by
    today's rules, no model call. 0 when none is left (the record says clean),
    else 2 with the hold line."""
    left = settle(sr, doc, [RR.Finding(**f) for f in rec.get("open") or []], rec)
    rec["status"] = "held" if left else "clean"
    rec["open"] = [f.as_dict() for f in left]
    rec["doc_sha_out"] = sha(doc.read_text(encoding="utf-8"))
    record.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    if left:
        sr.log(hold_line(left))
        return 2
    sr.log("update review: every finding the earlier review held on is settled by rule; nothing is paid again")
    return 0


def notes(out_dir: Path) -> list[str]:
    """The attorney notes section for what the rules settled here."""
    rows = [r for r in read_json(out_dir / SETTLED, []) or [] if isinstance(r, dict)]
    if not rows:
        return []
    return ["", f"## {NOTES_HEADING}", ""] + [
        f"- {r.get('grade')}: {r.get('finding')}" + (f" ({r['cite']})" if r.get("cite") else "") for r in rows
    ]


__all__ = ["SETTLED", "hold_line", "notes", "plain", "resume_held", "settle"]
