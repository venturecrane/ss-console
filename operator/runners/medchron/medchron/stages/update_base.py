"""`update_base`: the delivered chronology, pulled read-only and PROVEN. $0.

The first stage of an update into the delivered chronology (``dag.
UPDATE_STAGES``). It reads the matter's listing (``list_matter``), finds the
delivered chronology by the firm's own name for it (``update/locate.py``),
pulls that document and every delivered exhibit in its folder into
``delivered/``, rebuilds the chronology's markdown from the document
(``update/docx_md.docx_to_md``), and renders it again with the seat's own
renderer. A markdown that renders to the delivered document with ZERO
differing paragraphs (style, bold runs and text) becomes
``runs/<unit>/update/base.md`` and the plan records ``renderer: "seat"``: the
update carries every delivered entry from it byte-identical. A chronology
another renderer produced (the laptop pipeline's) fails that exact proof on
formatting alone; then the words-only proof (``docx_md.prove_text``) decides,
and when every paragraph reads the same the plan records ``renderer:
"foreign"`` and the entries are carried as text.

Holds (exit 2, a ``update_held:`` line naming why) when the delivered
chronology cannot be placed, when its Exhibit List names an exhibit its
folder does not hold, or when the words-only proof also finds a difference:
an update that cannot reproduce the delivered document's text cannot promise
to carry it. The hold line gives counts and paragraph indexes, never the
chronology's words. A pull that fails for a retryable reason is exit 1.

The delivered exhibits are copied to ``out/<unit>/`` as well: the audit of the
new claims renders cited pages from there (a new claim may cite a delivered
exhibit), and the pre-delivery review checks every citation
against the exhibits the firm will hold.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ..update import body as B, docx_md, locate as L
from .base import StageRun, read_json
from .download import _mint_with_retry
from .render import render_text

HELD = "update_held: "


def update_dir(sr: StageRun) -> Path:
    return sr.slug_dir / "runs" / sr.unit.unit / "update"


def headings(cfg: Any) -> set[str]:
    return {str(h) for h in (cfg.get("format", "subsections") or [])} | {"Discharge / Discontinuation in Care"}


def plan(sr: StageRun) -> dict[str, Any]:
    return read_json(update_dir(sr) / "update_plan.json", {})


def _pull(sr: StageRun, rows: list[dict[str, Any]], dest: Path) -> list[str]:
    """Pull each row's bytes to `dest/<name>`; returns the names that failed.
    Read-only: a mint and an https GET per file, nothing written to the matter."""
    dest.mkdir(parents=True, exist_ok=True)
    failed = []
    for i in range(0, len(rows), 8):
        batch = rows[i : i + 8]
        minted = {m["id"]: m for m in _mint_with_retry(sr, [r["id"] for r in batch])}
        for r in batch:
            url = (minted.get(r["id"]) or {}).get("url")
            target = dest / r["name"]
            if target.is_file() and r.get("size") and target.stat().st_size == int(r["size"]):
                continue
            try:
                if not url:
                    raise RuntimeError((minted.get(r["id"]) or {}).get("error") or "no url")
                sr.seat.fetch(url, target, r.get("size"))
            except Exception as exc:  # noqa: BLE001 - one file's failure is named in the exit line; the pass is retried whole
                target.unlink(missing_ok=True)
                failed.append(f"{r['name']} ({str(exc)[:80]})")
    return failed


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _locate(sr: StageRun) -> dict[str, Any]:
    spec = sr.job.update
    template = str(
        sr.cfg.get("delivery", "chronology_name_template") or "{CLIENT} - Medical Chronology {MM-DD-YY}.docx"
    )
    return L.locate(
        sr.manifest(),
        json.loads((sr.slug_dir / "folders.json").read_text(encoding="utf-8")),
        template=template,
        client=sr.unit.client_name,
        file_id=spec.chronology_file_id if spec else None,
    )


def _prove(sr: StageRun, docx: Path, ud: Path) -> tuple[str | None, str, list[Any]]:
    """(base markdown or None, renderer, diffs of the proof that decided).

    The exact proof first (style, bold runs and text): a chronology the seat
    rendered passes it and its entries are carried byte-identical
    (``renderer: "seat"``). When it fails, the words-only proof on a markdown
    rebuilt without bold markup: a chronology another renderer produced passes
    it and its entries are carried as text (``renderer: "foreign"``). The diffs
    returned on a hold are the words-only proof's: what a reader would see
    differ."""

    def render(text: str, dst: Path) -> int:
        return render_text(text, dst, cfg=sr.cfg, incident_date=sr.job.incident_date, log=lambda _m: None)

    md = docx_md.docx_to_md(docx, headings(sr.cfg))
    (ud / "base.rebuilt.md").write_text(md, encoding="utf-8")
    if not docx_md.prove(md, docx, render, ud / "prove")["diffs"]:
        return md, "seat", []
    plain = docx_md.docx_to_md(docx, headings(sr.cfg), plain=True)
    (ud / "base.rebuilt.text.md").write_text(plain, encoding="utf-8")
    r = docx_md.prove_text(plain, docx, render, ud / "prove-text")
    return (plain if not r["diffs"] else None), "foreign", r["diffs"]


def _where(diffs: list[Any]) -> str:
    """Where the documents differ, as paragraph indexes only: the hold reason
    travels to the job record and on to people, so it never quotes a word of
    the chronology (it once logged five delivered/rebuilt paragraph pairs of
    medical text into the job's reason)."""
    ix = [str(int(d[0])) for d in diffs]
    return ", ".join(ix[:12]) + (f" and {len(ix) - 12} more" if len(ix) > 12 else "")


def run(sr: StageRun) -> int:
    if sr.job.update is None:
        sr.log("update_base runs only on an update into the delivered chronology")
        return 1
    ud = update_dir(sr)
    ud.mkdir(parents=True, exist_ok=True)
    try:
        where = _locate(sr)
    except L.LocateHold as exc:
        sr.log(HELD + str(exc))
        return 2
    dd = sr.slug_dir / "delivered"
    failed = _pull(sr, [where["chronology"], *where["exhibits"]], dd)
    if failed:
        sr.log(f"the delivered package could not be pulled in full: {'; '.join(failed[:5])}")
        return 1
    docx = dd / where["chronology"]["name"]
    md, renderer, diffs = _prove(sr, docx, ud)
    # The local proof record (on the seat's data root, never in the job's
    # reason) keeps the differing paragraphs for whoever finishes the hold.
    (ud / "base_proof.json").write_text(
        json.dumps(
            {
                "proven": md is not None,
                "renderer": renderer,
                "differing_paragraphs": len(diffs),
                "diffs": [list(map(str, d)) for d in diffs[:20]],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    if md is None:
        sr.log(
            HELD + f"the delivered chronology's text does not re-render to itself ({len(diffs)} differing "
            f"paragraph(s), at index {_where(diffs)}); its entries cannot be carried"
        )
        return 2
    listed = B.delivered_numbers(md)
    have = {e["exhibit"] for e in where["exhibits"]}
    if listed - have:
        sr.log(HELD + f"the Exhibit List names exhibit(s) {sorted(listed - have)} that its folder does not hold")
        return 2
    where["renderer"] = renderer
    return _stage(sr, ud, dd, where, md, docx)


def _stage(sr: StageRun, ud: Path, dd: Path, where: dict[str, Any], md: str, docx: Path) -> int:
    out = sr.slug_dir / "out" / sr.unit.unit
    out.mkdir(parents=True, exist_ok=True)
    for e in where["exhibits"]:
        src = dd / e["name"]
        e["sha256"] = _sha(src)
        shutil.copyfile(src, out / e["name"])
    where["chronology"]["sha256"] = _sha(docx)
    where["top_exhibit"] = max([e["exhibit"] for e in where["exhibits"]] + list(B.delivered_numbers(md)) + [0])
    (ud / "base.md").write_text(md, encoding="utf-8")
    (ud / "update_plan.json").write_text(json.dumps(where, indent=1), encoding="utf-8")
    _, ents = B.entries_of(md)
    how = (
        "re-renders to itself"
        if where["renderer"] == "seat"
        else "was rendered by another renderer; its text re-renders to itself and its entries are carried as text"
    )
    sr.log(
        f"BASE PROVEN (renderer: {where['renderer']}): {where['chronology']['name']} {how}; {len(ents)} delivered entries, "
        f"{len(where['exhibits'])} delivered exhibit(s), {len(where['others'])} other file(s) in its folder left alone"
    )
    return 0
