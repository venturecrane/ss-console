"""The update review's DUPLICATE findings, decided in code. $0, no model.

WHY (2026-10-15). On two real matters the review loop never converged, and
duplicates were the part it structurally could not settle: the finisher's
``drop_entry`` is refused (``finisher.check_drop``) whenever the survivor
lacks a value the duplicate carries, the same rule compose used to keep it,
so an added entry repeating another ADDED entry with values of its own was
refused round after round. The review's own text names the entry repeated
(every round-1 duplicate on one matter named exactly one other anchor in its
``problem``; ``survivor`` was empty), so the decision needs no model:

* the survivor is the finding's ``survivor``, else the ONE other entry anchor
  its ``problem`` names (none, or several: left to the finisher);
* survivor ``D<j>`` (a delivered visit): the added entry becomes the fold
  under ``D<j>`` of only the paragraphs carrying values ``D<j>`` lacks
  (``entry_edits.fold_text``, as ``finisher.redirect_drop`` does), or is
  dropped when it carries nothing new (its exhibit, if left uncited, is a
  re-filed copy: ``finisher.refiled``); an entry already folded under another
  delivered visit of the same date is re-homed under ``D<j>`` the same way;
* survivor ``N<j>`` (another added entry): the paragraphs carrying values
  ``N<j>`` lacks, and any that keep a new exhibit cited, are appended to
  ``N<j>`` as they stand (citations kept), then the duplicate is dropped; a
  duplicate that carries nothing new is a plain drop. No value is lost: the
  survivor afterwards carries every content number of both.

An entry is never dropped or folded into a survivor dated otherwise (its
own date line would be lost: ``body.other_date``); that finding goes to the
finisher and the re-check with why. A pair naming each other folds once; a survivor already dropped this pass is
skipped and recorded (the finding stays open for the re-check). The moved
paragraphs keep their citations and are audited with the finisher's edits
(``stages/update_review.finish``).
"""

from __future__ import annotations

import copy
import re

from . import body as B, entry_edits as EE, review_repair as RR
from .values import content_numbers, numbers

_ANCHOR = re.compile(r"\b([ND]\d+)(?:\.[cu]\d+)?\b")


def is_duplicate(f: RR.Finding) -> bool:
    """A MUST duplicate finding on an added entry."""
    return f.severity == RR.MUST and "duplicate" in f.klass.lower() and f.anchor.startswith("N")


def survivor_of(f: RR.Finding) -> str:
    """The entry a duplicate finding names as the one it repeats: its
    ``survivor``, else the ONE other entry anchor its ``problem`` names."""
    if f.survivor:
        return f.survivor.split(".")[0]
    own = f.anchor.split(".")[0]
    named = {m.group(1) for m in _ANCHOR.finditer(f.problem)} - {own}
    return named.pop() if len(named) == 1 else ""


def _body(text: str) -> str:
    return text.split("\n", 2)[2] if text.count("\n") >= 2 else ""


def _fold_body(text: str) -> str:
    """The paragraphs of a fold entry below its "Additional findings" line."""
    parts = text.split("\n\n", 2)
    return parts[2] if len(parts) == 3 else ""


def _edit(f: RR.Finding, anchor: str, survivor: str, outcome: str) -> RR.Finding:
    e = copy.copy(f)
    e.anchor, e.survivor, e.action, e.outcome = anchor, survivor, "drop_entry", outcome
    e.reason = f"duplicates {survivor}; decided in code from the review's finding"
    e.removed = []
    return e


def resolve(
    md: str, findings: list[RR.Finding], keep: set[int], headings: set[str], delivered: list[str]
) -> tuple[str, list[RR.Finding], dict[str, str]]:
    """(the document with every decidable duplicate folded or dropped, the
    edits made (one per duplicate finding decided, its ``fid`` kept), {dropped
    or folded entry anchor: where its content went}). Anchors are the
    document's as given; the caller maps the findings left to the new one."""
    ents = RR.anchor_entries(md, keep, headings)
    by = {e.anchor: e for e in ents}
    texts = {e.anchor: e.text for e in ents}
    gone: dict[str, str] = {}  # dropped anchor -> the entry its content went to
    folded: dict[str, str] = {}  # anchor -> the delivered entry it now folds under
    edits: list[RR.Finding] = []
    for f in findings:
        if not is_duplicate(f):
            continue
        a, s = f.anchor.split(".")[0], survivor_of(f)
        if a not in by or not s or s == a:
            continue  # nothing to decide in code: the finisher sees it
        f.survivor = s
        if a in gone:  # a second finding on an entry already folded
            edits.append(_edit(f, a, s, f"settled: {a} was already folded into {gone[a]} this pass"))
        elif gone.get(s) == a:  # the pair names each other: folded once, the other way
            edits.append(_edit(f, a, s, f"settled: the pair was folded once this pass ({s} into {a})"))
        elif s in gone:
            edits.append(_edit(f, a, s, f"skipped: the survivor {s} was already folded into {gone[s]} this pass"))
        elif a in folded and (folded[a] == s or not s.startswith("D")):
            word = "settled" if folded[a] == s else "skipped"
            edits.append(_edit(f, a, s, f"{word}: {a} already folds under {folded[a]} this pass"))
        elif s.startswith("D"):
            edits.append(_onto_delivered(f, a, s, texts, delivered, keep, headings, gone, folded))
        else:
            edits.append(_onto_added(f, a, s, texts, keep, headings, gone, folded))
    if not gone and not folded:
        return md, edits, {}
    out, _ = RR._rebuild(md, ents, texts, set(gone), [])
    return out, edits, gone | folded


def _onto_delivered(
    f: RR.Finding,
    a: str,
    s: str,
    texts: dict[str, str],
    delivered: list[str],
    keep: set[int],
    headings: set[str],
    gone: dict[str, str],
    folded: dict[str, str],
) -> RR.Finding:
    k = int(s[1:]) - 1
    if not 0 <= k < len(delivered):
        return _edit(f, a, s, f"the survivor {s} is not a delivered entry")
    if why := B.other_date(texts[a], delivered[k]):
        return _edit(f, a, s, f"{a} is not folded under {s}: {why}")
    # An entry already folded under another delivered visit of this date (this
    # pass, or round 1's finish) is RE-HOMED under the one named: its fold
    # block is what it carries (a real re-check named D133 for a fold under
    # D132 and both findings were skipped, holding the update).
    body = _fold_body(texts[a]) if EE.is_fold(texts[a]) else _body(texts[a])
    lacks = content_numbers(body) - content_numbers(delivered[k])
    paras = [p for p in EE._paras(body, headings) if content_numbers(p) & lacks]
    if not paras:  # a copy of the delivered visit: dropped (a re-filed exhibit, if any)
        e = _edit(f, a, s, "applied")
        e.removed = sorted(numbers(texts[a]))
        gone[a] = s
        folded.pop(a, None)
        return e
    why = EE._cited(paras, keep)
    if why:
        return _edit(f, a, s, f"the content {s} lacks cannot be folded under it: {why}")
    e = _edit(f, a, s, "applied")
    e.action, e.corrected = "append_to_delivered", "\n\n".join(paras)
    e.removed = sorted(numbers(texts[a]) - numbers(e.corrected))
    texts[a] = EE.fold_text(delivered[k], e.corrected, headings)
    folded[a] = s
    return e


def _onto_added(
    f: RR.Finding,
    a: str,
    s: str,
    texts: dict[str, str],
    keep: set[int],
    headings: set[str],
    gone: dict[str, str],
    folded: dict[str, str],
) -> RR.Finding:
    if s not in texts:
        return _edit(f, a, s, f"the survivor {s} is not an added entry")
    if s in folded:
        return _edit(f, a, s, f"skipped: the survivor {s} now folds under {folded[s]} this pass")
    if why := B.other_date(texts[a], texts[s]):
        return _edit(f, a, s, f"{a} is not folded into {s}: {why}")
    body = _body(texts[a])
    lacks = content_numbers(body) - content_numbers(texts[s])
    rest = {n for x, t in texts.items() if x not in gone and x != a for n in B.cited(t)}
    orphans = B.cited(texts[a]) - rest
    paras = [p for p in EE._paras(body, headings) if content_numbers(p) & lacks or B.cited(p) & orphans]
    why = EE._cited(paras, keep) if paras else ""
    if why:
        return _edit(f, a, s, f"the content {s} lacks cannot be moved into it: {why}")
    if B.cited(texts[a]) - rest - {n for p in paras for n in B.cited(p)}:
        return _edit(f, a, s, "dropping the entry would leave a new exhibit uncited")
    if paras:
        texts[s] = texts[s].rstrip("\n") + "\n\n" + "\n\n".join(paras)
    e = _edit(f, a, s, "applied")
    e.corrected = "\n\n".join(paras)
    e.removed = sorted(numbers(texts[a]) - numbers(texts[s]))
    gone[a] = s
    return e


def remap(old_md: str, new_md: str, moved: dict[str, str], keep: set[int], headings: set[str]) -> dict[str, str]:
    """{old entry anchor: its anchor in `new_md`} for every entry `resolve`
    did not drop or fold; a folded or dropped entry maps to nothing."""
    old = RR.anchor_entries(old_md, keep, headings)
    new = RR.anchor_entries(new_md, keep, headings)
    used: set[int] = set()
    out: dict[str, str] = {}
    for e in old:
        if e.anchor in moved:
            continue
        hit = next((i for i, n in enumerate(new) if i not in used and n.text == e.text), None)
        if hit is None:  # a survivor that took paragraphs: its text opens with its old text
            hit = next((i for i, n in enumerate(new) if i not in used and n.text.startswith(e.text)), None)
        if hit is not None:
            used.add(hit)
            out[e.anchor] = new[hit].anchor
    return out
