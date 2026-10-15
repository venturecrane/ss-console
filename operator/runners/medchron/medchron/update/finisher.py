"""The update's FINISHER: the edits that settle an independent review's
findings, parsed, checked and applied. $0 (``stages/update_review.py`` makes
the paid calls).

WHY (2026-10-14). The laptop's chronology updates were delivered clean
because an Opus agent, after the pipeline, read an independent review's
findings against the page images and corrected every MUST-FIX by hand,
looping until the reviewer found nothing. The seat encoded each such
judgment as a rule, and each real file exposed a new case. This module is
the seat's half of that agent: the finisher model proposes edits, and an
edit reaches the document only when it is checked here and then audited.

* The finisher's reply is ONE JSON object of ``edits``, each naming the
  finding (``F<n>``) it settles and one action: the review-repair actions
  (``replace``, ``drop``, ``redate``, ``add``, ``drop_entry``, applied by
  ``review_repair.apply`` with its grounding rules) plus ``provider``, the
  entry's provider line.
* ``drop_entry`` names its ``survivor``: an added (``N<k>``) or delivered
  (``D<k>``) entry that already holds every value of the dropped one. Content
  that exists nowhere else is never dropped. A must-fix duplicate whose review
  names the one entry it repeats never reaches the finisher: it is folded in
  code first (``update/duplicates.py``, 2026-10-15).
* ``provider`` applies only when the page it cites, a page the entry cites,
  states the name in its text: the header is not a claim the audit reads, so
  the page's text is the independent read (as for a redate).
* Every applied edit whose new sentence the audit does not grade SUPPORTED
  is reverted (``failed``), and its finding stays open; an unsupported claim
  no edit wrote is left to the audit loop, which settles it before its gate.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..audit import claims as CL
from . import body as B, illegible as IL, review_repair as RR
from .values import content_numbers, numbers

_DROP_SURVIVOR = re.compile(r"\b([ND]\d+)\b")
_NAME_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
_CRED = {"md", "do", "dc", "pa", "np", "pt", "dpt", "rn", "aprn", "fnp", "mph", "phd", "lac", "ot", "otr"}


class EditsUnparsed(ValueError):
    """The finisher's reply is not one complete JSON object."""


def parse_edits(text: str, by_id: dict[str, RR.Finding]) -> tuple[list[RR.Finding], dict[str, str]]:
    """(edits, {finding id: why it is left}) from the finisher's reply. Each
    edit carries the severity, class and problem of the finding it settles;
    an edit naming no finding of this part is kept, refused."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise EditsUnparsed("no JSON object in the reply")
    try:
        obj = json.loads(text[a : b + 1])
    except json.JSONDecodeError as exc:
        raise EditsUnparsed(f"the reply is not JSON: {exc.msg}") from exc
    if not isinstance(obj, dict) or obj.get("complete") is not True or not isinstance(obj.get("edits"), list):
        raise EditsUnparsed("the reply is not a complete edits object")
    edits: list[RR.Finding] = []
    for item in obj["edits"]:
        e = RR._one({**item, "severity": "MUST"} if isinstance(item, dict) else item)
        said = str(item.get("action") or "").strip().lower() if isinstance(item, dict) else ""
        if said in ("", "decline", "none"):  # a finding the finisher will not edit, said so
            e.action, e.outcome = "none", f"declined by the finisher: {e.reason or 'no action and no reason given'}"
        src = by_id.get(e.fid)
        if src is None:
            e.outcome = f"the edit names no finding of this part ({e.fid or 'none'})"
        else:
            e.severity, e.klass, e.problem = src.severity, src.klass, src.problem
        edits.append(e)
    left: dict[str, str] = {}
    for item in obj.get("unresolved") or []:
        if isinstance(item, dict) and isinstance(item.get("finding"), str):
            left[item["finding"]] = str(item.get("why") or "")[:300]
    return edits, left


# ---- checks the review-repair applier does not make ---------------------------
def _survivor(e: RR.Finding) -> str:
    if e.survivor:
        return e.survivor
    m = _DROP_SURVIVOR.search(e.reason)
    return m.group(1) if m else ""


def check_drop(e: RR.Finding, ents: list[RR.Entry], delivered: list[str]) -> str:
    """'' when the dropped entry's every value is in its named survivor."""
    by = {x.anchor: x.text for x in ents} | {f"D{i}": t for i, t in enumerate(delivered, 1)}
    s = _survivor(e)
    if not s or s not in by or s == e.anchor:
        return "a duplicate is dropped only when the edit names the entry that holds its content"
    if e.anchor not in by:
        return f"{e.anchor} is not an added entry"
    if why := B.other_date(by[e.anchor], by[s]):
        return f"{e.anchor} is not dropped into {s}: {why}"
    body = by[e.anchor].split("\n", 2)[2] if by[e.anchor].count("\n") >= 2 else ""
    missing = sorted(content_numbers(body) - content_numbers(by[s]))
    if missing:
        return f"the survivor {s} does not carry {', '.join(missing[:5])}; content that exists nowhere else is kept"
    e.survivor, e.reason = s, e.reason or f"duplicates {s}"
    return ""


def redirect_drop(
    e: RR.Finding, edits: list[RR.Finding], ents: list[RR.Entry], delivered: list[str], headings: set[str]
) -> None:
    """A duplicate the finisher named a survivor for that lacks some of its
    content, turned into the edit that loses nothing (2026-10-14: 8 of
    one matter's and 5 of another's must-fix sat on "the survivor does not carry"
    refusals, round after round):

    * survivor ``D<j>`` (a delivered visit): ``append_to_delivered`` with the
      added entry's paragraphs that carry what ``D<j>`` lacks, the action the
      prompt names for exactly this case;
    * survivor ``N<j>`` holding nothing the dropped entry lacks: the richer
      entry is kept and ``N<j>`` is dropped instead, unless another edit of
      this round already touches ``N<j>``.

    Anything else is left as it was: ``check_drop`` refuses it."""
    from . import entry_edits as EE

    by = {x.anchor: x.text for x in ents}
    s = _survivor(e)
    mine = by.get(e.anchor, "")
    them = delivered[int(s[1:]) - 1] if re.fullmatch(r"D\d+", s) and 0 < int(s[1:]) <= len(delivered) else by.get(s, "")
    if B.other_date(mine, them):  # a visit on another date is never folded away (``check_drop`` says so)
        return
    body = mine.split("\n", 2)[2] if mine.count("\n") >= 2 else ""
    if re.fullmatch(r"D\d+", s) and 0 < int(s[1:]) <= len(delivered):
        lacks = content_numbers(body) - content_numbers(delivered[int(s[1:]) - 1])
        paras = [p for p in EE._paras(body, headings) if content_numbers(p) & lacks]
        if paras:
            e.action, e.survivor, e.corrected = "append_to_delivered", s, "\n\n".join(paras)
            e.reason = e.reason or f"repeats {s}; its content {s} lacks goes under it"
    elif (
        s in by
        and content_numbers(by[s]) <= content_numbers(mine)
        and not any(x.anchor.split(".")[0] == s for x in edits if x is not e)
    ):
        e.anchor, e.survivor = s, e.anchor
        e.reason = f"duplicates {e.survivor}, which carries all of its content"


def _words(name: str) -> list[str]:
    return [w.lower() for w in _NAME_WORD.findall(name) if w.lower().strip(".") not in _CRED]


def check_provider(
    e: RR.Finding, ents: list[RR.Entry], evidence: RR.Evidence | None, shown: dict[str, list[tuple[int, int]]]
) -> str:
    """'' when the corrected provider name is stated, in the page's text, on a
    page of a document the entry cites (`shown`: the pages the review read
    for it). A handwritten ledger row names no one; the typed statement of the
    same document does (a real update, 2026-10-14: 27 provider lines a round
    were refused because the statement page was not one the row cited)."""
    ent = next((x for x in ents if x.anchor == e.anchor), None)
    if ent is None:
        return f"{e.anchor} is not an added entry"
    name = e.corrected.strip()
    if not name or "\n" in name or "|" in name or "(Exhibit" in name:
        return "the corrected provider is not one name"
    if e.exhibit is None or e.page is None or (e.exhibit, e.page) not in shown.get(e.anchor, ent.cites()):
        return "the provider is grounded on a page of no document the entry cites"
    text = evidence(e.exhibit, e.page) if evidence else None
    if text is None:
        return f"no page text to check the provider against (Exhibit {e.exhibit} p. {e.page})"
    have, want = set(_words(text)), _words(name)
    if not want or any(w not in have for w in want):
        return f"Exhibit {e.exhibit} p. {e.page} does not state {name}"
    return ""


def set_provider(md: str, edits: list[RR.Finding], ents: list[RR.Entry]) -> str:
    """The document with each checked provider edit on its entry's provider
    line (the part before ``|``); the date line and category stay."""
    out = md
    for e in edits:
        if e.action != "provider" or e.outcome:
            continue
        ent = next(x for x in ents if x.anchor == e.anchor)
        lines = ent.text.split("\n")
        if len(lines) < 2 or out.count(ent.text) != 1:
            e.outcome = "the entry's provider line is not found once"
            continue
        head = lines[1]
        lines[1] = e.corrected.strip() + (" | " + head.split("|", 1)[1].strip() if "|" in head else "")
        out = out.replace(ent.text, "\n".join(lines), 1)
        e.removed = sorted(numbers(head) - numbers(lines[1]))
        e.outcome = "applied"
    return out


def precheck(
    edits: list[RR.Finding],
    ents: list[RR.Entry],
    delivered: list[str],
    ev: RR.Evidence | None,
    shown: dict[str, list[tuple[int, int]]] | None = None,
    keep: set[int] | None = None,
    headings: set[str] | None = None,
) -> None:
    from . import entry_edits as EE

    for e in edits:
        if not e.outcome and e.action == "drop_entry" and check_drop(e, ents, delivered):
            redirect_drop(e, edits, ents, delivered, headings or set())
    for e in edits:
        if e.outcome:
            continue
        if e.action == "drop_entry":
            e.outcome = check_drop(e, ents, delivered)
        elif e.action == "provider":
            e.outcome = check_provider(e, ents, ev, shown or {})
        elif e.action == "new_entry":
            e.outcome = EE.check_new_entry(e, keep or set(), headings or set(), ev)
        elif e.action == "append_to_delivered":
            e.outcome = EE.check_fold(e, ents, delivered, keep or set(), headings or set())


# ---- the audit's verdict on what changed ----------------------------------------
def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", CL.CITE.sub("", t)).strip()


def failed(edits: list[RR.Finding], unsupported: list[dict[str, Any]]) -> tuple[list[RR.Finding], int]:
    """(the applied text edits the audit did not support, how many
    unsupported claims no edit wrote). An edit is reverted only when its own
    text holds an unsupported claim. A claim no edit wrote (a diagnosis claim
    a redate moved, a sentence beside a dropped one) is left to the audit
    loop, which repairs or drops it before its gate: reverting every edit
    for it undid 58 corrections in one real round (2026-10-14)."""
    text_edits = [
        e
        for e in edits
        if e.outcome == "applied" and e.action in ("replace", "add", "new_entry", "append_to_delivered")
    ]
    bad: list[RR.Finding] = []
    untraced = 0
    for c in unsupported:
        claim = _flat(str(c.get("claim") or ""))
        own = [e for e in text_edits if claim and (claim in _flat(e.corrected) or _flat(e.corrected) in claim)]
        bad += own
        untraced += 0 if own else 1
    seen: set[int] = set()
    return [e for e in bad if not (id(e) in seen or seen.add(id(e)))], untraced


def settle(findings: list[RR.Finding], edits: list[RR.Finding], left: dict[str, str]) -> int:
    """Each finding's outcome from its edits: "applied" when one applied (its
    removed values carried, for proof 10), else why not. Returns how many
    findings an applied edit settled."""
    n = 0
    for f in findings:
        mine = [e for e in edits if e.fid == f.fid]
        done = [e for e in mine if e.outcome == "applied"]
        if done:
            f.outcome, n = "applied", n + 1
            f.removed = sorted({v for e in done for v in e.removed})
        elif mine:
            f.outcome = "; ".join(dict.fromkeys(e.outcome for e in mine))[:400]
        else:
            f.outcome = "declined by the finisher: " + (left.get(f.fid) or "no edit and no reason given")
    return n


def refiled(
    edits: list[RR.Finding], ents: list[RR.Entry], delivered: list[str], md_after: str, new: set[int]
) -> dict[int, str]:
    """{new exhibit: "Exhibit H"} for each new exhibit an applied drop of a
    copy of a DELIVERED visit left uncited: the exhibit is a re-filed copy of
    delivered pages (the words of ``select.delivered_home``'s path), its
    number is kept, it is not filed again, and H is the delivered exhibit the
    surviving entry cites."""
    still = {n for e in B.entries_of(md_after)[1] for n in B.cited(e)}
    by = {x.anchor: x.text for x in ents}
    out: dict[int, str] = {}
    for e in edits:
        if e.action != "drop_entry" or e.outcome != "applied" or not e.survivor.startswith("D"):
            continue
        k = int(e.survivor[1:]) - 1
        homes = sorted(B.cited(delivered[k])) if 0 <= k < len(delivered) else []
        for n in sorted(B.cited(by.get(e.anchor, "")) & new - still):
            if homes:
                out[n] = f"Exhibit {homes[0]}"
    return out


#: The words a claim uses for what a handwritten form shows but no reader can
#: settle (``prompts/update-finish-system.md``). A claim stating them is settled:
#: the review does not raise the same unreadable mark again (2026-10-14: about
#: 30 of 40 reverted corrections were column marks and handwritten numbers the
#: grader could not read either).
AS_READ = ("marked as shown on the page", "as written on the page")
#: Findings an as-read claim still answers to: a wrong visit, a copy, words
#: about the process are not a reading.
_STILL_RAISED = ("duplicate", "misattribut", "process", "uncited", "out-of-order", "date")


def settled_as_read(found: list[RR.Finding], ents: list[RR.Entry]) -> tuple[list[RR.Finding], int]:
    """(the findings minus those on a claim stated "as shown / as written on
    the page", how many were settled that way)."""
    claims = {c.anchor: c.text.lower() for e in ents for c in e.claims + e.uncited}
    noted = {e.anchor for e in ents if IL.has_note(e.text)}
    keep: list[RR.Finding] = []
    for f in found:
        text = claims.get(f.anchor, "")
        if any(p in text for p in AS_READ) and not any(w in f.klass.lower() for w in _STILL_RAISED):
            continue
        # An entry carrying the firm's illegible note (``update/illegible.py``):
        # what it does not say is what the audit could not confirm on the
        # handwriting, and its page is cited for the reader.
        if f.anchor.split(".")[0] in noted and any(w in f.klass.lower() for w in IL.SETTLES):
            continue
        # The note itself is the firm's required wording, never process wording.
        if f.anchor in noted and "process" in f.klass.lower():
            continue
        keep.append(f)
    return keep, len(found) - len(keep)


def delivered_of(base_md: str) -> list[str]:
    return B.entries_of(base_md)[1]
