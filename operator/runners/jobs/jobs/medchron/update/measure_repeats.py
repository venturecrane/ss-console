"""What the page-hold and stray-citation rules do to one real update job.
$0: no model call, no write anywhere but stdout.

    python -m jobs.medchron.update.measure_repeats <job_dir> [--unit U]

`job_dir` is a COPY of a matter's job directory (the one holding
``delivered/``, ``text/`` and ``runs/<unit>/update/``). It reports, as JSON:

* pages: every page decision over the new documents in reading order, and
  how many READ pages each text rule suspected of being a copy
  (``select.SUSPECTED``; a text rule no longer sets a page aside, so there is
  no window to check);
* stray citations: over the job's filed new entries (``update/new_entries.md``),
  how many lines are only a citation before and after ``uncited.join_stray``,
  and every entry whose numbers, words or cited exhibits the join would
  change (it changes none by design; any is listed and the exit status is 1).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import body as B, pageid as PI, pageid_io as PG, select as S
from .uncited import cite_only, join_stray
from .values import numbers


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
    sizes = [(str(r["id"]), int(r.get("pages") or max(texts[str(r["id"])] or {0: ""}))) for r in rows]
    earlier, tally, why = S.Earlier(), Counter[str](), Counter[str]()
    for rid in PI.reading_order(sizes):
        image = PG.aside(ident.pages.get(rid) or {})
        for d, reason in S.classify_new(texts[rid], dict(sizes)[rid], seen, earlier, image, tally).values():
            why[f"{d}: {reason}"] += 1
    return {
        "documents": len(rows),
        "pages": sum(why.values()),
        "decisions": dict(why),
        "suspected_by_text": dict(tally),
    }


def _words(e: str) -> Counter[str]:
    return Counter(re.findall(r"[a-z]+", B.CITE.sub(" ", e).lower()))


def stray_report(ud: Path) -> dict[str, Any]:
    """The citation-only lines of the filed new entries, before and after the join."""
    p = ud / "new_entries.md"
    _, ents = B.split_body(p.read_text(encoding="utf-8") if p.is_file() else "")
    after = [join_stray(e) for e in ents]
    changed = [
        a.splitlines()[0]
        for e, a in zip(ents, after)
        if numbers(e) != numbers(a) or _words(e) != _words(a) or B.cited(e) != B.cited(a)
    ]
    return {
        "entries": len(ents),
        "citation_only_lines_before": sum(1 for e in ents for ln in e.splitlines() if cite_only(ln)),
        "citation_only_lines_after": sum(1 for e in after for ln in e.splitlines() if cite_only(ln)),
        "entries_whose_content_the_join_changes": changed,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--unit")
    a = ap.parse_args(argv)
    ud = _unit_dir(a.job_dir, a.unit) / "update"
    out = {"pages": pages_report(a.job_dir, ud), "stray_citations": stray_report(ud)}
    print(json.dumps(out, indent=1))
    return 1 if out["stray_citations"]["entries_whose_content_the_join_changes"] else 0


if __name__ == "__main__":
    sys.exit(main())
