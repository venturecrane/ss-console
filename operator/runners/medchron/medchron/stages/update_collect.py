"""`update_collect` ($0) and `update_summary` (paid, one call): from the
composed entries to the AUDIT document.

`update_collect` keeps what the new documents add (``update/collect.py``),
compacts the new exhibits' numbers before the audit can key a claim to them,
renames their files to match, and writes the audit document over
``runs/<unit>/final-chronology.md``: the new entries only. Nothing new to add
holds with ``update_held:`` (the delivered chronology already lists every visit
the new documents record), naming how many documents were read.

`update_summary` runs only when the update adds a pre-incident visit: the
Prior Medical History block is rewritten from EVERY pre-incident entry,
delivered and added, with the summarize stage's own prompt, model tier and
containment rule (a sentence may cite only pages its source entries cited),
and put at the head of the audit document, so each of its sentences is
audited like any new claim.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import llm, prompts
from ..update import body as B, collect as C
from .base import StageRefusal, StageRun, read_json
from .group import Canon
from .summarize import beyond_source
from .update_base import HELD, update_dir


def _rename(
    out: Path, before: list[dict[str, Any]], after: list[dict[str, Any]], uncited: list[dict], ud: Path
) -> None:
    """Exhibit files to their compacted titles, through temporary names (two
    exhibits may swap numbers); an uncited one leaves out/. Rerunnable."""
    old = {r["exhibit"]: r["title"] for r in before}
    staged = []
    for r in after:
        src = out / f"{old[r['composed_as']]}.pdf"
        dst = out / f"{r['title']}.pdf"
        if src == dst or not src.is_file():
            continue
        tmp = out / f".renumber-{r['composed_as']}.pdf"
        os.replace(src, tmp)
        staged.append((tmp, dst))
    for tmp, dst in staged:
        os.replace(tmp, dst)
    (ud / "uncited").mkdir(exist_ok=True)
    for r in uncited:
        src = out / f"{old[r['composed_as']]}.pdf"
        if src.is_file():
            os.replace(src, ud / "uncited" / src.name)


def _page_map(out: Path, keep: list[dict[str, Any]], provisional: dict[int, str]) -> None:
    """Exhibit -> its one source file, for the text-mode audit's page index.
    The file name is the one compose read it under (the units file row)."""
    rows = [
        {
            "exhibit": r["exhibit"],
            "title": r["title"],
            "total_pages": r["pages"],
            "files": [{"file": f"{provisional[r['composed_as']]}.pdf", "start_page": 1, "pages": r["pages"]}],
        }
        for r in keep
    ]
    (out / "page_map.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def run_collect(sr: StageRun) -> int:
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    out = sr.slug_dir / "out" / sr.unit.unit
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    _, base_ents = B.entries_of(base_md)
    try:
        comp = C.composed(rd)
        entries, rep = C.collect(comp, base_ents, sr.job.incident_date, Canon(sr.cfg))
    except C.CollectRefusal as exc:
        raise StageRefusal(str(exc)) from exc
    provisional = read_json(ud / "new_exhibits.json", [])
    entries, keep, uncited, remap = C.compact(entries, provisional)
    if not keep:
        sr.log(
            HELD + f"nothing new to add: the {len(provisional)} new document(s) record no visit the delivered "
            f"chronology does not already list ({rep})"
        )
        return 2
    _rename(out, provisional, keep, uncited, ud)
    _page_map(out, keep, {r["exhibit"]: r["title"] for r in provisional})
    (ud / "new_exhibits.final.json").write_text(json.dumps(keep, indent=1), encoding="utf-8")
    (ud / "new_exhibits.uncited.json").write_text(json.dumps(uncited, indent=1), encoding="utf-8")
    (ud / "remap.json").write_text(json.dumps({str(k): v for k, v in remap.items()}, indent=1), encoding="utf-8")
    (ud / "new_index.json").write_text(json.dumps(C.index_rows(rd), indent=1), encoding="utf-8")
    (ud / "new_entries.md").write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    (ud / "compose_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    (rd / "final-chronology.md").write_text(C.audit_document(base_md, entries, keep), encoding="utf-8")
    sr.log(
        f"audit document: {len(entries)} new entries, {len(keep)} new exhibit(s) cited, {len(uncited)} uncited; {rep}"
    )
    return 0


def run_summary(sr: StageRun) -> int:
    ud, rd = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit
    inc = sr.job.incident_date
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    _, base_ents = B.entries_of(base_md)
    _, new_ents = B.split_body("\n\n" + (ud / "new_entries.md").read_text(encoding="utf-8"))
    added_pre = [e for e in new_ents if B.iso(e) < inc]
    if not added_pre:
        sr.log("no pre-incident visit added; the delivered Prior Medical History stands")
        return 0
    pre = sorted([e for e in base_ents if B.iso(e) < inc] + added_pre, key=B.iso)
    r = sr.doorway.call(
        "summarize",
        model=llm.model_for(sr.cfg, "judgment"),
        system=prompts.load("summarize-system", sr.cfg),
        max_tokens=16000,
        messages=[{"role": "user", "content": "\n\n".join(pre)}],
        effort="high",
        timeout=240.0,
        custom_id="update-summarize",
    )
    block = r.text.strip()
    bad = beyond_source("\n".join(pre), block)
    if bad or not B.PMH_HEAD.match(block.splitlines()[0] if block else ""):
        sr.log(
            f"REJECTED: the rewritten Prior Medical History {'reaches beyond its source: ' + str(bad[:5]) if bad else 'does not open with its heading'}"
        )
        return 1
    (ud / "pmh.md").write_text(block + "\n", encoding="utf-8")
    keep = read_json(ud / "new_exhibits.final.json", [])
    (rd / "final-chronology.md").write_text(C.audit_document(base_md, new_ents, keep, block), encoding="utf-8")
    sr.log(f"Prior Medical History rewritten over {len(pre)} pre-incident entries ({len(added_pre)} added)")
    return 0
