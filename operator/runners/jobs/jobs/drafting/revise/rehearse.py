"""The $0 rehearsal of revise mode, on real inputs, against an answer key.

Law 18: nothing runs first on a client seat. Before the lane takes a real
revision, this runs the lane's own checks over a revision whose right answer
is known (the hand revision of 2026-11-04), with no model call and no write:

    python -m jobs.drafting.revise.rehearse \\
        --request request.txt          # the attorney's email, his words only
        --base filed-before.docx       # the brief as delivered (or .md)
        --edited filed-0453.docx       # the hand revision (or .md)
        --checklist answer-key.json    # {"items": [...]} in checklist.py's shape
        [--inputs $JOBS_DRAFTING_INPUTS --class mediation_brief]   # base proof + formatting
        [--out DIR]

It prints, per item, ``met`` / ``not_met`` / ``read`` and why, and every
invariant finding; it exits 0 only when every item code can check is met and
no invariant is broken. A rehearsal that fails on the hand revision is a
defect in the CHECKS (or the answer key), found at no cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import checklist as ck, checks, core, prior, sections as sec


def _md(path: Path) -> str:
    return prior.docx_to_md(path) if path.suffix.lower() == ".docx" else path.read_text(encoding="utf-8")


def rehearse(
    request_text: str,
    base: Path,
    edited: Path,
    answer_key: dict[str, Any],
    *,
    fmt: dict[str, Any] | None = None,
    cls: str = "mediation_brief",
    out: Path | None = None,
) -> dict[str, Any]:
    base_md, edited_md = _md(base), _md(edited)
    report: dict[str, Any] = {"base_proof": None}
    if fmt is not None and base.suffix.lower() == ".docx":
        from .. import render

        work = out or base.parent
        diffs = prior.prove(base_md, base, lambda md, p: render.render(md, cls, p, fmt)[0], work)
        report["base_proof"] = diffs or "proven"
    p_secs = sec.split(base_md)
    items = ck.validate(answer_key, request_text, p_secs)
    e_secs = sec.align(p_secs, edited_md)
    statuses, findings, _notes = core.evaluate(p_secs, e_secs, items, request_text, None)
    for st, it in zip(statuses, items):
        if it.kind != "formatting":
            continue
        if edited.suffix.lower() != ".docx":
            st.status, st.how, st.detail = "read", "not checked", "formatting needs the Word file"
            continue
        skip = len(sec.tables(p_secs[0].body))
        size = float((fmt or {}).get("size_pt") or 12)
        probs = checks.check_docx(edited, it.params["layout"], size, skip)
        st.status, st.how, st.detail = ("not_met" if probs else "met"), "code", "; ".join(probs) or "in the Word file"
    report.update(
        items=[{**it.to_dict(), **st.to_dict()} for it, st in zip(items, statuses)],
        findings=[{"section": s, "finding": f} for s, f in findings],
    )
    code_bad = [s for s in statuses if s.status == "not_met" and s.how == "code"]
    report["ok"] = not code_bad and not findings and report["base_proof"] in (None, "proven")
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "rehearsal.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.drafting.revise.rehearse", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--request", required=True, type=Path)
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--edited", required=True, type=Path)
    ap.add_argument("--checklist", required=True, type=Path)
    ap.add_argument("--inputs", default=None, help="the drafting firm-inputs dir: proves the base and reads size_pt")
    ap.add_argument("--class", dest="cls", default="mediation_brief")
    ap.add_argument("--out", default=None, type=Path)
    a = ap.parse_args(argv)
    fmt = None
    if a.inputs:
        from .. import firm as firm_mod

        fmt = firm_mod.load(a.inputs).data["format"]
    try:
        rep = rehearse(
            a.request.read_text(encoding="utf-8"),
            a.base,
            a.edited,
            json.loads(a.checklist.read_text(encoding="utf-8")),
            fmt=fmt,
            cls=a.cls,
            out=a.out,
        )
    except ck.ChecklistError as exc:
        print(f"answer key refused: {exc}", file=sys.stderr)
        return 2
    print(f"base proof: {rep['base_proof'] or 'not run (no --inputs)'}")
    for it in rep["items"]:
        print(f"  {it['id']:<4} {it['status']:<8} {it['kind']:<20} {it['detail']}")
    for f in rep["findings"]:
        print(f"  FINDING {f['finding']}")
    print("REHEARSAL OK" if rep["ok"] else "REHEARSAL REFUSED")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
