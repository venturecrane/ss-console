"""`classify_nonrecord` ($0) and `classify_scanned` (paid, transcription
tier): which exhibit pages are not records.

The firm wants the actual records only, so out come the retrieval vendor's
order forms, release-of-information boilerplate, signed authorizations, the
vendor's computer-generated indexes and AI summaries. Classification is
STRUCTURAL: each class is a document type with a distinctive fingerprint
(`nonrecord.page_classes` in the firm config, most distinctive first), not a
word that might appear anywhere; a vendor letterhead stamped on every page
is not a page class. A page that collides with a citation is reported as one:
treat every collision as a classification error until the page is looked at.

Pages with no text layer cannot be classified by text. The scanned pass looks
at the ones near the head of a source-file segment, where vendor paperwork
sits, and a falsifier rides in every batch: authored control pages (one
known ORDER, one known INDEX, from `<install_root>/controls/controls.json`) and
the run's own record control (`record_control[-unit].json`, written by
`decisions.control`). If the model does not call those correctly, its other
answers are not trusted: controls_ok is recorded and the strip refuses on it.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from anthropic import RequestTooLargeError

from .. import llm, prompts
from .base import StageRun, read_json

LABELS = ("ORDER", "REQUEST", "AUTH", "CERT", "INDEX", "RECORD", "BLANK")
NONRECORD_LABELS = {"ORDER", "REQUEST", "AUTH", "CERT", "INDEX"}
BATCH = 12
# The API caps a request at 32 MB; base64 page images are what fill it.
MAX_BATCH_BYTES = 20 * 2**20
HEAD_PAGES = 3
MIN_TEXT = 40


def resolve_exhibit_files(outdir: Path) -> dict[int, Path]:
    """Exhibit number -> pdf, resolved THROUGH page_map.json: a glob is
    ambiguous the moment a rebuild changes an exhibit's title (two files, one
    number), and it once picked the stale one."""
    byno: dict[int, list[Path]] = {}
    for p in sorted(outdir.iterdir()):
        m = re.match(r"Exhibit (\d+) - ", p.name)
        if m and p.suffix == ".pdf":
            byno.setdefault(int(m.group(1)), []).append(p)
    pm = read_json(outdir / "page_map.json", None)
    if isinstance(pm, list) and all(isinstance(e, dict) and "title" in e and "exhibit" in e for e in pm):
        out: dict[int, Path] = {}
        for e in pm:
            p = outdir / (re.sub(r"[/:]", "-", e["title"]) + ".pdf")
            if p.is_file():
                out[e["exhibit"]] = p
            elif len(byno.get(e["exhibit"], [])) == 1:
                out[e["exhibit"]] = byno[e["exhibit"]][0]
            else:
                raise FileNotFoundError(f"exhibit {e['exhibit']} in page_map.json has no unambiguous file in {outdir}")
        return out
    dupes = {n: ps for n, ps in byno.items() if len(ps) > 1}
    if dupes:
        raise FileNotFoundError("multiple files share an exhibit number and no page_map.json disambiguates")
    return {n: ps[0] for n, ps in byno.items()}


def page_classes(cfg: Any) -> list[tuple[str, list[re.Pattern]]]:
    return [
        (str(c["name"]), [re.compile(str(x), re.I | re.S | re.M) for x in (c.get("patterns") or [])])
        for c in (cfg.get("nonrecord", "page_classes") or [])
    ]


def classify_text(t: str, classes: list[tuple[str, list[re.Pattern]]]) -> str | None:
    for name, pats in classes:
        if any(p.search(t) for p in pats):
            return name
    return None


def blocks(pairs: list[tuple[int, str]]) -> list[list]:
    out: list[list] = []
    for p, c in pairs:
        if out and out[-1][0] == c and out[-1][2] == p - 1:
            out[-1][2] = p
        else:
            out.append([c, p, p])
    return out


def cited_pages(doc_md: str) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    for m in re.finditer(r"\(Exhibit (\d+)(?: - p\. ([0-9][0-9,\s\-]*))?", doc_md):
        s = out.setdefault(int(m.group(1)), set())
        for part in (m.group(2) or "").strip().rstrip(",").split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                if a.strip().isdigit() and b.strip().isdigit():
                    s.update(range(int(a), int(b) + 1))
            elif part.isdigit():
                s.add(int(part))
    return out


def nonrecord_path(sr: StageRun) -> Path:
    return sr.slug_dir / (f"nonrecord-{sr.unit.unit}.json" if sr.job.joint else "nonrecord.json")


def run_nonrecord(sr: StageRun) -> int:
    import pymupdf

    outdir = sr.slug_dir / "out" / sr.unit.unit
    md = sr.slug_dir / "runs" / sr.unit.unit / "final-chronology.md"
    cites = cited_pages(md.read_text(encoding="utf-8"))
    paths = resolve_exhibit_files(outdir)
    if paths and not cites:
        sr.log(
            "the chronology parsed to ZERO exhibit citations; the cited-collision guard would compare against nothing"
        )
        return 1
    classes = page_classes(sr.cfg)
    result: dict[str, Any] = {}
    tot = drop = unk = coll = 0
    for ex in sorted(paths):
        doc = pymupdf.open(str(paths[ex]))
        pairs: list[tuple[int, str]] = []
        unknown: list[int] = []
        for i, pg in enumerate(doc, 1):
            t = pg.get_text()
            if len(t.strip()) < MIN_TEXT:
                unknown.append(i)
                continue
            c = classify_text(t, classes)
            if c:
                pairs.append((i, c))
        pages = {p for p, _ in pairs}
        c_hit = sorted(pages & cites.get(ex, set()))
        result[str(ex)] = {
            "pages": len(doc),
            "blocks": [{"class": c, "from": a, "to": b, "n": b - a + 1} for c, a, b in blocks(pairs)],
            "drop_pages": sorted(pages),
            "unknown": unknown,
            "cited_collision": c_hit,
        }
        tot, drop, unk, coll = tot + len(doc), drop + len(pages), unk + len(unknown), coll + len(c_hit)
        sr.log(
            f"Exhibit {ex}: {len(doc)} pages -> {len(pages)} non-record, {len(unknown)} unclassifiable (scan)"
            + (f"; !! CITED and marked non-record: {c_hit}" if c_hit else "")
        )
        doc.close()
    if tot:
        sr.log(
            f"{tot} pages | {drop} non-record ({100 * drop / tot:.1f}%) | {unk} scanned/unclassifiable | {coll} cited-collision"
        )
    nonrecord_path(sr).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


def _png(doc: Any, p: int, dpi: int = 110) -> str:
    return base64.standard_b64encode(doc[p - 1].get_pixmap(dpi=dpi).tobytes("png")).decode()


def file_heads(page_map: list[dict[str, Any]], n: int = HEAD_PAGES) -> dict[int, set[int]]:
    heads: dict[int, set[int]] = {}
    for e in page_map:
        s = heads.setdefault(e["exhibit"], set())
        for f in e.get("files") or []:
            if "start_page" in f:
                s.update(f["start_page"] + i for i in range(n))
    return heads


def _batches(pages: Iterable[tuple[str, str]]) -> Iterator[list[tuple[str, str]]]:
    """(label, png) pages grouped for one request each, bounded by BYTES as
    well as by count: twelve scanned pages at 110 dpi passed the API's request
    limit (413, live 2026-09-16), and the limit is on the request, not on the
    page count."""
    batch: list[tuple[str, str]] = []
    size = 0
    for lbl, png in pages:
        if batch and (len(batch) >= BATCH or size + len(png) > MAX_BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append((lbl, png))
        size += len(png)
    if batch:
        yield batch


def _classify_batch(
    sr: StageRun, labels: list[tuple[str, str]], model: str, system: str, results: dict[str, str]
) -> None:
    """One call for these (label, png) pages. A request the API refuses as too
    large is split in half and both halves sent; a single page the API refuses
    is left unclassifiable rather than ending the run."""
    content: list[dict[str, Any]] = []
    for lbl, png in labels:
        content.append({"type": "text", "text": f"page {lbl}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}})
    try:
        r = sr.doorway.call(
            "classify",
            model=model,
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": content}],
            timeout=300.0,
            custom_id=f"classify-{labels[0][0]}",
        )
    except RequestTooLargeError:
        if len(labels) == 1:
            sr.log(f"  page {labels[0][0]} is too large for one request even alone; left unclassifiable")
            return
        half = len(labels) // 2
        sr.log(f"  batch of {len(labels)} refused as too large; sending as {half} + {len(labels) - half}")
        _classify_batch(sr, labels[:half], model, system, results)
        _classify_batch(sr, labels[half:], model, system, results)
        return
    for line in r.text.strip().splitlines():
        m = re.match(r"\s*(\S+)\s*=\s*([A-Z]+)", line)
        if m:
            results[m.group(1)] = m.group(2)
    sr.log(f"  batch of {len(labels)} -> {r.usage.input_tokens} in / {r.usage.output_tokens} out")


def run_scanned(sr: StageRun) -> int:
    import pymupdf

    d = sr.slug_dir
    sfx = f"-{sr.unit.unit}" if sr.job.joint else ""
    nr = read_json(nonrecord_path(sr), None)
    if nr is None:
        sr.log("nonrecord.json not found; classify_nonrecord runs first")
        return 1
    outdir = d / "out" / sr.unit.unit
    paths = resolve_exhibit_files(outdir)
    heads = file_heads(read_json(outdir / "page_map.json", []))
    targets = [(ex, p) for ex in sorted(paths) for p in sorted(set(nr[str(ex)]["unknown"]) & heads.get(ex, set()))]
    # The authored controls are install-level (install_root: the laptop's data
    # root, the seat's run dir seeded from the vault), never per job. This
    # stat stays AHEAD of any zero-target shortcut on purpose: an install
    # without its falsifier refuses on its first job whatever the matter holds.
    ctl_path = sr.job.install_root / "controls" / "controls.json"
    rec_path = d / f"record_control{sfx}.json"
    if not (ctl_path.is_file() and rec_path.is_file()):
        sr.log(
            f"controls not authored: need {ctl_path} and {rec_path}; a classifier without its falsifier measures nothing"
        )
        return 1
    controls = [
        (sr.job.install_root / c["pdf"], int(c["page"]), str(c["label"]))
        for c in json.loads(ctl_path.read_text(encoding="utf-8"))
    ]
    rc = json.loads(rec_path.read_text(encoding="utf-8"))
    controls.append((paths[int(rc["exhibit"])], int(rc["page"]), "RECORD"))
    sr.log(f"{len(targets)} scanned page(s) to classify, +{len(controls)} control(s)")
    docs: dict[Path, Any] = {}

    def get(path: Path) -> Any:
        if path not in docs:
            docs[path] = pymupdf.open(str(path))
        return docs[path]

    model, system = llm.model_for(sr.cfg, "transcription"), prompts.load("classify-system", sr.cfg)
    results: dict[str, str] = {}
    allpages = [(f"Ex{e}p{p}", paths[e], p) for e, p in targets] + [
        (f"CTL{i}", path, p) for i, (path, p, _) in enumerate(controls)
    ]
    for batch in _batches((lbl, _png(get(path), p)) for lbl, path, p in allpages):
        _classify_batch(sr, batch, model, system, results)
    for doc in docs.values():
        doc.close()
    ok = True
    for i, (path, p, want) in enumerate(controls):
        got = results.get(f"CTL{i}", "?")
        if got != want:
            ok = False
        sr.log(f"  control {path.name[:22]} p.{p}: expected {want}, got {got}  [{'OK' if got == want else 'FAILED'}]")
    if not ok:
        sr.log("!! controls failed; these labels are not trusted")
    nonrec: dict[str, list[tuple[int, str]]] = {}
    for e, p in targets:
        lab = results.get(f"Ex{e}p{p}", "?")
        if lab in NONRECORD_LABELS:
            nonrec.setdefault(str(e), []).append((p, lab))
    (d / f"scanned_labels{sfx}.json").write_text(
        json.dumps({"labels": results, "nonrecord": nonrec, "controls_ok": ok}, indent=1), encoding="utf-8"
    )
    sr.log(f"scanned pages that are NOT records: {sum(len(v) for v in nonrec.values())}")
    return 0 if ok else 1
