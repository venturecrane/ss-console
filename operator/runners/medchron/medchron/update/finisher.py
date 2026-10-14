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
  that exists nowhere else is never dropped.
* ``provider`` applies only when the page it cites, a page the entry cites,
  states the name in its text: the header is not a claim the audit reads, so
  the page's text is the independent read (as for a redate).
* Every applied edit whose new sentence the audit does not grade SUPPORTED
  is reverted (``failed``), and its finding stays open.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..audit import claims as CL
from . import body as B, review_repair as RR
from .values import numbers

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
        src = by_id.get(e.fid)
        if src is None:
            e.outcome = e.outcome or f"the edit names no finding of this part ({e.fid or 'none'})"
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
    body = by[e.anchor].split("\n", 2)[2] if by[e.anchor].count("\n") >= 2 else ""
    missing = sorted(numbers(body) - numbers(by[s]))
    if missing:
        return f"the survivor {s} does not carry {', '.join(missing[:5])}; content that exists nowhere else is kept"
    e.survivor, e.reason = s, e.reason or f"duplicates {s}"
    return ""


def _words(name: str) -> list[str]:
    return [w.lower() for w in _NAME_WORD.findall(name) if w.lower().strip(".") not in _CRED]


def check_provider(e: RR.Finding, ents: list[RR.Entry], evidence: RR.Evidence | None) -> str:
    """'' when the corrected provider name is stated on a page the entry cites."""
    ent = next((x for x in ents if x.anchor == e.anchor), None)
    if ent is None:
        return f"{e.anchor} is not an added entry"
    name = e.corrected.strip()
    if not name or "\n" in name or "|" in name or "(Exhibit" in name:
        return "the corrected provider is not one name"
    if e.exhibit is None or e.page is None or (e.exhibit, e.page) not in ent.cites():
        return "the provider is grounded on a page the entry does not cite"
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


def precheck(edits: list[RR.Finding], ents: list[RR.Entry], delivered: list[str], ev: RR.Evidence | None) -> None:
    for e in edits:
        if e.outcome:
            continue
        if e.action == "drop_entry":
            e.outcome = check_drop(e, ents, delivered)
        elif e.action == "provider":
            e.outcome = check_provider(e, ents, ev)


# ---- the audit's verdict on what changed ----------------------------------------
def _flat(t: str) -> str:
    return re.sub(r"\s+", " ", CL.CITE.sub("", t)).strip()


def failed(edits: list[RR.Finding], unsupported: list[dict[str, Any]]) -> list[RR.Finding]:
    """The applied text edits the audit did not support: an edit whose text
    holds an unsupported claim, or every text edit when a claim cannot be
    traced to one edit (nothing unsupported stays on a guess)."""
    text_edits = [e for e in edits if e.outcome == "applied" and e.action in ("replace", "add")]
    bad: list[RR.Finding] = []
    for c in unsupported:
        claim = _flat(str(c.get("claim") or ""))
        own = [e for e in text_edits if claim and (claim in _flat(e.corrected) or _flat(e.corrected) in claim)]
        bad += own or text_edits
    seen: set[int] = set()
    return [e for e in bad if not (id(e) in seen or seen.add(id(e)))]


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
            f.outcome = "the finisher made no edit" + (f": {left[f.fid]}" if left.get(f.fid) else "")
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


def delivered_of(base_md: str) -> list[str]:
    return B.entries_of(base_md)[1]
