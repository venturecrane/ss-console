"""What the visit-once rules do to one real update job. $0: no model call,
no write anywhere but stdout.

    python -m jobs.medchron.update.measure_repeats <job_dir> [--unit U] [--incident YYYY-MM-DD]

`job_dir` is a COPY of a matter's job directory (the one holding
``delivered/``, ``text/`` and ``runs/<unit>/update/``). It reports, as JSON:

* pages: how many pages of the new documents the page rules now judge
  already read or repeated, and how many of those only the text-window rule
  (``update/contained``) holds;
* entries: of the entries compose produced, how many the same-visit rules
  drop or fold (new against delivered, new against new), how many rows
  remain, and how many lines that are only a citation remain after
  ``uncited`` (against how many the job delivered);
* safety: every composed entry the rules dropped or reshaped, checked
  against THE SAFETY PROPERTY (``safety_violations``): each content value
  and finding term it states is in what stays on its date. Any violation is
  listed and the exit status is 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import body as B, collect as C, handwritten as H, pageid as PI, pageid_io as PG, repeats as R, select as S
from .uncited import cite_all, cite_only
from .values import content_numbers


class _NoConfig:
    """A firm config with no provider aliases: the canonical provider is
    the lane's own collapse of clinician variants (``stages.group.Canon``)."""

    def get(self, *_keys: str) -> None:
        return None


def _unit_dir(job: Path, unit: str | None) -> Path:
    runs = sorted(p for p in (job / "runs").glob("*") if (p / "update").is_dir())
    if unit:
        return job / "runs" / unit
    if len(runs) != 1:
        raise SystemExit(f"name the unit with --unit (found {[p.name for p in runs]})")
    return runs[0]


def _read(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _texts(job: Path, rid: str) -> dict[int, str]:
    tp = job / "text" / f"{rid}.txt"
    return S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else "")


def pages_report(job: Path, ud: Path) -> dict[str, Any]:
    """The page rules over the job's new documents, in reading order."""
    plan = _read(ud / "update_plan.json", {})
    base = ud / "base.md"
    listed = B.delivered_numbers(base.read_text(encoding="utf-8")) if base.is_file() else None
    read_by = [e for e in plan.get("exhibits") or [] if listed is None or e.get("exhibit") in listed]
    by_ex = {int(e["exhibit"]): job / "delivered" / str(e["name"]) for e in read_by}
    seen = S.seen_shingles([p for p in by_ex.values() if p.is_file()])
    rows = [r for r in _read(ud / "new_exhibits.json", []) if isinstance(r, dict) and r.get("id")]
    texts = {str(r["id"]): _texts(job, str(r["id"])) for r in rows}
    ident = PG.load(ud)
    cache: dict[int, list[str]] = {}

    def text_of(other: str, page: int) -> str:
        n = PG.delivered_exhibit(other)
        if n is None:
            return (texts.get(other) or {}).get(page) or ""
        if n not in cache:
            cache[n] = S.pdf_pages(by_ex[n]) if n in by_ex and by_ex[n].is_file() else []
        return cache[n][page - 1] if 0 < page <= len(cache[n]) else ""

    sizes = [(str(r["id"]), int(r.get("pages") or max(texts[str(r["id"])] or {0: ""}))) for r in rows]
    earlier, tally, why = S.Earlier(), Counter[str](), Counter[str]()
    for rid in PI.reading_order(sizes):
        image = PG.reasons(ident.pages.get(rid) or {}, texts[rid], text_of)
        for d, reason in S.classify_new(texts[rid], dict(sizes)[rid], seen, earlier, image, tally).values():
            why[f"{d}: {reason}"] += 1
    return {"documents": len(rows), "pages": sum(why.values()), "decisions": dict(why), "held_by_text": dict(tally)}


def safety_violations(composed: list[str], kept: list[str], base_ents: list[str]) -> list[dict[str, Any]]:
    """THE SAFETY PROPERTY, over every composed entry the rules dropped or
    reshaped: each content value (``values.content_numbers``) it states and
    everything it names (``repeats.findings``: finding terms, and the word
    each number is stated for) is in what stays on its date (the kept new
    entries and the delivered ones). [{entry, values, findings}] that is not."""
    stays: dict[str, list[str]] = {}
    for e in [*kept, *base_ents]:
        stays.setdefault(B.iso(e), []).append(e)
    out = []
    whole = set(kept)
    for e in composed:
        if e in whole:
            continue
        on = stays.get(B.iso(e), [])
        have_v = set().union(*(content_numbers(x) for x in on)) if on else set()
        have_w = set().union(*(R.vocab(x) for x in on)) if on else set()
        lost_v, lost_t = sorted(content_numbers(e) - have_v), sorted(R.findings(e) - have_w)
        if lost_v or lost_t:
            out.append({"entry": e.splitlines()[0], "values": lost_v, "findings": lost_t})
    return out


def entries_report(job: Path, rd: Path, ud: Path, incident: str) -> dict[str, Any]:
    """The collect rules over the entries compose produced for this job."""
    from ...stages.group import Canon

    base_md = (ud / "base.md").read_text(encoding="utf-8")
    _, base_ents = B.entries_of(base_md)
    year = int(incident[:4]) if incident[:4].isdigit() else 2000
    comp, _ = H.with_years(C.composed(rd), year)
    try:
        kept, rep = C.collect(comp, base_ents, incident, Canon(_NoConfig()))
    except C.CollectRefusal as exc:
        return {"refused": str(exc)}
    cited, _ = cite_all(kept, set())
    delivered = (ud / "new_entries.md").read_text(encoding="utf-8") if (ud / "new_entries.md").is_file() else ""
    return {
        "composed": len(comp),
        "rows_remaining": len(kept),
        "new_vs_delivered": {k: rep.get(k, 0) for k in (R.REWORDED, R.SAME_VISIT)},
        "new_vs_new": {k: rep.get(k, 0) for k in (R.REWORDED_NEW, R.FOLDED_NEW, R.REPEAT_NEW, R.SAME_DRAW)},
        "additional_findings_merged": rep.get(R.ADDITIONAL_MERGED, 0),
        "citation_only_lines": {
            "in_the_delivered_new_entries": sum(1 for ln in delivered.splitlines() if cite_only(ln)),
            "after_these_rules": sum(1 for e in cited for ln in e.splitlines() if cite_only(ln)),
        },
        "safety_violations": safety_violations(comp, kept, base_ents),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--unit")
    ap.add_argument("--incident", default="", help="YYYY-MM-DD; the job's incident date (for yearless headings)")
    a = ap.parse_args(argv)
    rd = _unit_dir(a.job_dir, a.unit)
    ud = rd / "update"
    incident = a.incident or str(_read(a.job_dir / "job" / "job.json", {}).get("incident_date") or "")
    out = {"pages": pages_report(a.job_dir, ud), "entries": entries_report(a.job_dir, rd, ud, incident)}
    print(json.dumps(out, indent=1))
    return 1 if out["entries"].get("safety_violations") else 0


if __name__ == "__main__":
    sys.exit(main())
