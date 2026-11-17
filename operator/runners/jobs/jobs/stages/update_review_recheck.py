"""The update review's round 2: the narrow re-check and its one more finish,
bounded to what a second read can settle (``update/recheck.py`` decides which
findings, on which pages, at what projected cost). Paid only when it runs.

Split out of ``stages/update_review.py`` on 2026-11-17 (the module-size
ceiling) when the round was bounded; ``update_review._loop`` calls ``recheck``.
Before anything is paid again, round 1's open findings go through the $0 rules
(``update_review_settle``, which closes a class 2 omission another entry
already covers). Every finding not re-checked keeps its ``recheck`` reason in
the record and is settled by a rule or held, never dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import budget
from ..medchron.update import frozen as FZ, recheck as RC, review_parts as RP, review_repair as RR
from . import update_review as UR, update_review_settle as URS
from .update_base import headings


def settle_first(
    c: UR.Ctx, rec: dict[str, Any], rows: list[dict], frozen: list[RR.Finding], open_: list[int], doc: Path
):
    """Round 1's open findings settled by the $0 rules, on the document's
    current anchors. Returns the rows still open."""
    ents = RR.anchor_entries(doc.read_text(encoding="utf-8"), UR._keep(c.sr), headings(c.sr.cfg))
    now = {i: f for i in open_ if (f := RC.current(rows[i], ents)) is not None}
    URS.settle(c.sr, doc, list(now.values()), rec)
    for i, f in now.items():
        if f.outcome.startswith("settled"):
            frozen[i].outcome = f.outcome
            rows[i]["finding"], rows[i]["recheck"] = frozen[i].as_dict(), "not re-checked: settled by rule"
    return [i for i in open_ if not UR._closed(frozen[i])]


def gate(c: UR.Ctx, parts: list[RP.Part], n: int) -> tuple[str, dict[str, Any]]:
    """('' to run the re-check, else why it is skipped), and its projection,
    logged before any call: pages shown x the review tier's rate per page,
    against ``levers.update_recheck_cap_usd`` (default ``recheck.DEFAULT_CAP_USD``)."""
    pages, cap, p = sum(len(x.pages) for x in parts), RC.cap_usd(c.sr.cfg), UR._pricing()
    try:
        usd = RC.projected_usd(pages, len(parts), p.rate_for(c.model), UR.PAGE_TOKENS) if p else None
    except budget.BudgetError:
        usd = None
    shown = "not priced here" if usd is None else f"{usd:.2f} USD"
    c.sr.log(
        f"update review round {UR.RECHECK} (re-check): {n} finding(s) to re-check in {len(parts)} part(s), "
        f"{pages} page(s), projected {shown} against the cap of {cap:.2f} USD (levers.update_recheck_cap_usd)"
    )
    info = {"parts": len(parts), "pages": pages, "projected_usd": None if usd is None else round(usd, 2)}
    info["cap_usd"] = cap
    if not parts:
        return "no finding a second read could settle", info
    if usd is not None and usd > cap:
        return f"its projection of {usd:.2f} USD is over the cap of {cap:.2f} USD", info
    return "", info


def recheck(c: UR.Ctx, rec: dict[str, Any], md: str, base: list[str], doc: Path, ud: Path) -> int:
    """The narrow re-check of the frozen findings a second read can settle,
    and the one more finish of those it confirms (``update/frozen.py``)."""
    rows = rec["frozen"]
    frozen = [FZ.finding(r) for r in rows]
    open_ = [i for i, f in enumerate(frozen) if not UR._closed(f)]
    if open_ and not isinstance(rec.get("pending"), dict):  # a paid re-check already pending is read back as it was
        open_ = settle_first(c, rec, rows, frozen, open_, doc)
        md = doc.read_text(encoding="utf-8")
    if not open_:
        UR._save(ud / UR.RECORD, rec, "clean", doc)
        return 0
    c.rnd, c.calls, c.usage = UR.RECHECK, 0, []
    ents = RR.anchor_entries(md, UR._keep(c.sr), headings(c.sr.cfg))
    send, left, now = RC.pick(rows, open_, ents)
    for i, why in left.items():
        rows[i]["recheck"] = why
    parts = RC.parts(send, ents, c.npages, c.already)
    skip, info = gate(c, parts, len(send))
    held = [now.get(i) or frozen[i] for i in left]
    if skip:
        for i in send:
            rows[i]["recheck"] = f"not re-checked: {skip}"
        rec["rounds"].append({"round": UR.RECHECK, "kind": "re-check", "skipped": skip, **info, "left": len(open_)})
        c.sr.log(
            f"update review round {UR.RECHECK}: not run, {skip}; {len(open_)} must-fix left to the rules or the hold"
        )
        return UR._hold(c, rec, held + list(send.values()), doc, ud)
    for i in send:
        rows[i]["recheck"] = RC.SENT
    found, n = UR._review_once(c, rec, ud, md, base, None, UR.REVIEW, parts)
    return _finish(
        c, rec, (md, base, doc, ud), frozen, list(send), found, n, ents, held, info | {"not_sent": len(left)}
    )


def _finish(
    c: UR.Ctx,
    rec: dict,
    where: tuple,
    frozen: list,
    sent: list[int],
    found: list,
    n: int,
    ents: list,
    held: list,
    info: dict,
) -> int:
    """The re-check's findings against the sent rows, the one more finish,
    and the record: clean, held, or the audit loop's failure."""
    md, base, doc, ud = where
    rows = rec["frozen"]
    todo, unreviewed, confirms = FZ.triage(rows, sent, found, ents)
    keys = [FZ.key(f, ents) for f in found]  # before the finish moves any anchor
    settled, rc = 0, 0
    if todo:
        try:
            md, _edited, settled, rc = UR.finish(c, md, todo, base, doc)
        except UR.GradeFailed as exc:
            doc.write_text(md, encoding="utf-8")
            c.sr.log(f"update review: {exc}; nothing is reverted or kept, a rerun grades again")
            return 1
    rec.pop("pending", None)
    for i in sent:
        f = frozen[i]
        if f.klass == FZ.NOT_REVIEWED:
            f.outcome = f.outcome if unreviewed else "settled: its entries were reviewed on the re-check"
        elif i not in confirms:
            f.outcome = FZ.SETTLED
        elif all(UR._closed(g) for g in confirms[i]):
            f.outcome = "applied"
        else:
            f.outcome = "confirmed on re-check; " + "; ".join(dict.fromkeys(g.outcome for g in confirms[i]))[:400]
        rows[i]["finding"] = f.as_dict()
    still = [f for f in todo if not UR._closed(f)] + unreviewed + held
    advisory = sum(f.outcome == FZ.ADVISORY for f in found)
    rec["rounds"].append(
        {"round": UR.RECHECK, "kind": "re-check", "entries": n, "must": len(UR._must(found)), "calls": c.calls}
        | {"usage": c.usage, "confirmed": len(confirms), "advisory": advisory, "left": len(still)}
        | {"keys": keys, "findings": [f.as_dict() for f in found], **info}
    )
    c.sr.log(
        f"update review round {UR.RECHECK} (re-check of {len(sent)} open frozen finding(s)): {n} entr(ies) reviewed, "
        f"{len(confirms)} confirmed, {len(sent) - len(confirms)} settled as not raised again, {advisory} advisory; "
        f"{settled} settled by the finisher, {len(still)} must-fix left; {c.calls} call(s), {UR._dollars(c.usage)}"
    )
    if rc != 0:
        UR._save(ud / UR.RECORD, rec, "audit_failed", doc)
        return 3 if rc == 1 else 4
    if not still:
        UR._save(ud / UR.RECORD, rec, "clean", doc)
        return 0
    return UR._hold(c, rec, still, doc, ud)
