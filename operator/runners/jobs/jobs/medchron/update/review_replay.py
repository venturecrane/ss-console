"""Replay a recorded update review under today's $0 rules, read only. No
model call, no write.

WHY (2026-11-17). The class 2 rule (``update/elsewhere``) and the bounded
re-check (``update/recheck``) were built from one held update whose reviewer
was wrong on all 8 findings it held on. Before a seat runs them, the ops
workspace replays them on a COPY of that job's directory and reads, per
recorded finding, what the rules now decide::

    python -m jobs.medchron.update.review_replay <job dir> [--firm-config PATH] [--json]

For every round 1 must-fix (``review_repair.json`` ``frozen``, with the
outcome round 1's finish gave it) and every finding the review held on
(``open``) it reports: closed by the class 2 rule (by which arm, naming the
covering entry), sent to the re-check, or left open and why. Then what the
re-check would now send (findings, pages, projected dollars) beside what the
recorded round 2 sent (the pages the old plan showed, and its recorded calls
and dollars). Anchors are read on the job's document as it is now (the held
document), as the stage reads them. Only the class 2 rule is replayed; the
other $0 rules edit the document and run on the job's own resume.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import frozen as FZ, recheck as RC, review_parts as RP, review_repair as RR


def _round_one(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The frozen rows with the outcome round 1's finish gave each (a later
    round overwrites the row's own copy). Round 1's must-fix are its MUST
    findings on added entries, in the order they were frozen."""
    rows = [r for r in rec.get("frozen") or [] if isinstance(r, dict)]
    first = next((r for r in rec.get("rounds") or [] if isinstance(r, dict) and r.get("round") == 1), {})
    must = [
        f
        for f in first.get("findings") or []
        if isinstance(f, dict) and f.get("severity") == RR.MUST and not str(f.get("anchor") or "").startswith("D")
    ]
    if len(must) != len(rows):
        return rows
    return [{**r, "finding": {**r["finding"], "outcome": str(m.get("outcome") or "")}} for r, m in zip(rows, must)]


def _closed(f: dict[str, Any]) -> bool:
    o = str(f.get("outcome") or "")
    return o == "applied" or o.startswith("settled")


def _cover_line(c: Any, why: str) -> str:
    return f"closed by the class 2 rule ({c.arm}, covered by {c.anchor}: {c.where})" if c else f"left open: {why}"


def _old_pages(rows: list[dict], open_: list[int], ents: list[RR.Entry], keep: set[int], npages, already) -> int:
    targets = FZ.locate([rows[i] for i in open_], ents)
    return sum(len(p.pages) for p in RP.plan([ents[i] for i in sorted(targets)], keep, npages, already))


def replay(sr: Any) -> dict[str, Any]:
    from ...stages import update_review as UR, update_review_settle as URS
    from ...stages.update_base import headings, update_dir
    from ...stages.update_review_io import already_pages, page_counts, page_text

    rec = json.loads((update_dir(sr) / UR.RECORD).read_text(encoding="utf-8"))
    doc = sr.slug_dir / "runs" / sr.unit.unit / "final-chronology.md"
    keep = UR._keep(sr)
    ents = RR.anchor_entries(doc.read_text(encoding="utf-8"), keep, headings(sr.cfg))
    ev, npages, already = page_text(sr), page_counts(sr.slug_dir / "out" / sr.unit.unit), already_pages(sr)
    rows = _round_one(rec)
    open_ = [i for i, r in enumerate(rows) if not _closed(r["finding"])]
    now = {i: f for i in open_ if (f := RC.current(rows[i], ents)) is not None}
    covered = dict(zip(now, URS.covers(sr, doc, list(now.values()), ev)))
    out_rows = []
    still = [i for i in open_ if covered.get(i, (None, ""))[0] is None]
    send, left, _ = RC.pick(rows, still, ents)
    for i, r in enumerate(rows):
        f = r["finding"]
        if i not in open_:
            what = f"closed in round 1: {f.get('outcome')}"
        elif covered.get(i, (None, ""))[0] is not None:
            what = _cover_line(*covered[i])
        else:
            why = "; ".join(w for w in (left.get(i), covered.get(i, (None, ""))[1]) if w)
            what = RC.SENT if i in send else f"left open (holds): {why}"
        out_rows.append({"anchor": f.get("anchor"), "klass": f.get("klass"), "page": _page(f), "replay": what})
    held = [RR.Finding(**f) for f in rec.get("open") or [] if isinstance(f, dict)]
    held_rows = [
        {"anchor": f.anchor, "klass": f.klass, "page": _page(f.as_dict()), "replay": _cover_line(c, why)}
        for f, (c, why) in zip(held, URS.covers(sr, doc, held, ev))
    ]
    parts = RC.parts(send, ents, npages, already)
    pages = sum(len(p.pages) for p in parts)
    p = UR._pricing()
    try:
        usd = RC.projected_usd(pages, len(parts), p.rate_for(UR._model(sr.cfg)), UR.PAGE_TOKENS) if p else None
    except Exception:  # noqa: BLE001 - an unpriced model reads as "not priced", never a failed replay
        usd = None
    r2 = next((r for r in rec.get("rounds") or [] if isinstance(r, dict) and r.get("round") == 2), {})
    return {
        "frozen": out_rows,
        "held": held_rows,
        "recheck_now": {"findings": len(send), "parts": len(parts), "pages": pages, "projected_usd": usd}
        | {"cap_usd": RC.cap_usd(sr.cfg)},
        "recheck_recorded": {
            "pages_shown": _old_pages(rows, open_, ents, keep, npages, already),
            "calls": r2.get("calls"),
            "dollars": UR._dollars(list(r2.get("usage") or [])) if r2 else None,
        },
    }


def _page(f: dict[str, Any]) -> str:
    return f"Exhibit {f.get('exhibit')} p. {f.get('page')}" if f.get("exhibit") and f.get("page") else "no page named"


def lines(r: dict[str, Any]) -> list[str]:
    out = ["ROUND 1 MUST-FIX (frozen):"]
    out += [f"  {x['anchor']} [{x['klass']}] {x['page']}: {x['replay']}" for x in r["frozen"]]
    out += ["HELD ON (open):"]
    out += [f"  {x['anchor']} [{x['klass']}] {x['page']}: {x['replay']}" for x in r["held"]] or ["  none"]
    n, o = r["recheck_now"], r["recheck_recorded"]
    usd = "not priced here" if n["projected_usd"] is None else f"{n['projected_usd']:.2f} USD"
    out.append(
        f"RE-CHECK NOW: {n['findings']} finding(s), {n['parts']} part(s), {n['pages']} page(s), projected {usd} "
        f"(cap {n['cap_usd']:.2f} USD)"
    )
    out.append(f"RE-CHECK RECORDED: {o['pages_shown']} page(s) by the old plan, {o['calls']} call(s), {o['dollars']}")
    closed = sum(x["replay"].startswith("closed by the class 2 rule") for x in r["held"])
    out.append(f"HELD FINDINGS THE CLASS 2 RULE CLOSES: {closed} of {len(r['held'])}")
    return out


def _stage_run(job_dir: Path, firm_config: str | None) -> Any:
    from .. import config as config_mod, job as job_mod
    from ...stages.base import StageRun

    job = job_mod.load(job_dir)
    if len(job.units) != 1:
        raise SystemExit(f"{job_dir}: an update has one unit, this job has {len(job.units)}")

    def no_seat() -> Any:
        raise RuntimeError("the replay never opens the seat")

    return StageRun(
        job, config_mod.load(firm_config), job.units[0], job.data_root / job.slug, {}, lambda _m: None, no_seat
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.medchron.update.review_replay", description=(__doc__ or "").split("\n")[0])
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--firm-config", default=None, help="the firm config (default: the seat's, as the driver reads it)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = replay(_stage_run(a.job_dir, a.firm_config))
    print(json.dumps(r, indent=1) if a.json else "\n".join(lines(r)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
