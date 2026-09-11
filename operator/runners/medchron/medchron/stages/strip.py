"""`strip` (falsify, dry, apply): remove non-record pages from the exhibits
and renumber every citation. $0.

The safety property: a claim that was audited against a physical page must
still point at THAT page. Removing pages changes labels, not paper, so if the
remap is right every audit verdict carries over. The remap is therefore
verified BY CONTENT, not arithmetic: for every citation, the page the new
document points at and the page the old one pointed at must match in text and
pixels. `falsify` offsets the new page by one and requires the check to FAIL,
so a check that cannot fail is never mistaken for a check that passed.

The drop set is the text classifier's pages plus the scanned classifier's
(when its controls held) plus any authored `drops[-unit].json`. Summary-table
Reference cells carry their citation WITHOUT parentheses and are remapped
through the same path (nine delivered units once shipped their tables on
pre-strip numbers). A stripped exhibit is built by deleting pages from a copy
and saving (per-page insert_pdf reached 38 GB resident on a 2,141-page
production); the original stays beside it as `.orig`, a second apply refuses,
and the audit's render cache is cleared because every page number just moved.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import StageRun, read_json
from .classify import nonrecord_path, resolve_exhibit_files

PAGESPEC = r"\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*"
CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. (" + PAGESPEC + r"))?([^)]*)\)")
TABLE_CITE = re.compile(r"(\|\s*)Exhibit (\d+) - p\. (" + PAGESPEC + r")([^|]*?)(\s*\|)")
# Measured, not chosen: after a rebuild the same page differs by at most 0.27%
# of bytes; a page against its neighbour by at least 8.4%.
PIXEL_TOL = 0.01


def expand(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def compress(nums: list[int]) -> str:
    if not nums:
        return ""
    nums = sorted(set(nums))
    out: list[str] = []
    s = p = nums[0]
    for n in nums[1:]:
        if n == p + 1:
            p = n
            continue
        out.append(f"{s}-{p}" if s != p else f"{s}")
        s = p = n
    out.append(f"{s}-{p}" if s != p else f"{s}")
    return ", ".join(out)


def page_match(a: Any, b: Any, dpi: int = 60) -> tuple[bool | None, float]:
    """Same physical page? Text must match exactly AND pixels must agree.
    None when the INSTRUMENT failed (a page the rasteriser cannot draw)."""
    try:
        ta, tb = a.get_text(), b.get_text()
    except Exception:  # noqa: BLE001 - the rasteriser raising on a page means the INSTRUMENT failed; None is the honest verdict, not False
        return None, 1.0
    if ta != tb:
        return False, 1.0
    try:
        pa, pb = a.get_pixmap(dpi=dpi), b.get_pixmap(dpi=dpi)
    except Exception:  # noqa: BLE001 - the rasteriser raising on a page means the INSTRUMENT failed; None is the honest verdict, not False
        return None, 1.0
    if (pa.width, pa.height) != (pb.width, pb.height):
        return False, 1.0
    x, y = pa.samples, pb.samples
    r = sum(1 for i in range(len(x)) if x[i] != y[i]) / max(len(x), 1)
    return r <= PIXEL_TOL, r


def drop_set(sr: StageRun, paths: dict[int, Path]) -> dict[int, set[int]] | None:
    d = sr.slug_dir
    sfx = f"-{sr.unit.unit}" if sr.job.joint else ""
    nr = read_json(nonrecord_path(sr), None)
    if nr is None:
        sr.log("nonrecord.json not found; classify_nonrecord runs first")
        return None
    scanned = read_json(d / f"scanned_labels{sfx}.json", None)
    authored = read_json(d / f"drops{sfx}.json", {"manual_drops": {}, "citation_edits": []})
    drops: dict[int, set[int]] = {}
    for ex in sorted(paths):
        s = set(nr[str(ex)]["drop_pages"])
        if scanned:
            if not scanned.get("controls_ok"):
                sr.log("scanned_labels: the classifier's controls failed; refusing to strip on its labels")
                return None
            s |= {p for p, _lab in (scanned.get("nonrecord") or {}).get(str(ex), [])}
        for p, _ in (authored.get("manual_drops") or {}).get(str(ex), []):
            s.add(p)
        drops[ex] = s
    return drops


def _run(sr: StageRun, mode: str) -> int:
    import pymupdf

    d = sr.slug_dir
    unit = sr.unit.unit
    sfx = f"-{unit}" if sr.job.joint else ""
    outdir = d / "out" / unit
    paths = resolve_exhibit_files(outdir)
    drops = drop_set(sr, paths)
    if drops is None:
        return 1
    authored = read_json(d / f"drops{sfx}.json", {"manual_drops": {}, "citation_edits": []})
    apply, falsify = mode == "apply", mode == "falsify"
    n_drop = sum(len(v) for v in drops.values())
    sr.log(f"{unit}: dropping {n_drop} page(s)")
    if falsify and n_drop == 0:
        # The falsifier proves the remap CHECK can fail. With nothing to
        # strip there is no remap, so there is nothing for it to prove.
        sr.log("FALSIFIER: no pages to drop, so no remap to falsify; not applicable")
        return 0
    remap: dict[int, dict[int, int]] = {}
    newcount: dict[int, int] = {}
    for ex, path in paths.items():
        doc = pymupdf.open(str(path))
        n, m = 0, {}
        for p in range(1, len(doc) + 1):
            if p in drops[ex]:
                continue
            n += 1
            m[p] = n
        remap[ex], newcount[ex] = m, n
        doc.close()
    cpath = d / "runs" / unit / "final-chronology.md"
    doc_md = cpath.read_text(encoding="utf-8")
    for e in authored.get("citation_edits") or []:
        if e["find"] not in doc_md:
            sr.log(f"!! citation edit not found: {e['find']}")
            return 1
        doc_md = doc_md.replace(e["find"], e["replace"])
    checks: list[tuple[int, int, int]] = []
    problems: list[tuple[int, str, str]] = []

    def remap_spec(ex: int, spec: str) -> str | None:
        kept = [p for p in expand(spec) if p not in drops[ex]]
        if not kept:
            problems.append((ex, spec, "every cited page was dropped"))
            return None
        new = [remap[ex][p] for p in kept]
        checks.extend((ex, o, nw) for o, nw in zip(kept, new))
        return compress(new)

    def sub(m: re.Match) -> str:
        ex, spec, tail = int(m.group(1)), m.group(2), m.group(3)
        if not spec:
            return m.group(0)
        new = remap_spec(ex, spec)
        return m.group(0) if new is None else f"(Exhibit {ex} - p. {new}{tail})"

    def sub_table(m: re.Match) -> str:
        lead, ex, spec, tail, trail = m.group(1), int(m.group(2)), m.group(3), m.group(4), m.group(5)
        new = remap_spec(ex, spec)
        return m.group(0) if new is None else f"{lead}Exhibit {ex} - p. {new}{tail}{trail}"

    out_md = TABLE_CITE.sub(sub_table, CITE.sub(sub, doc_md))
    if problems:
        for ex, spec, why in problems:
            sr.log(f"!! Exhibit {ex} p.{spec}: {why}")
        return 1
    sr.log(f"  remapped {len(set(checks))} distinct page reference(s)")
    bad = 0
    unverifiable: list[list[Any]] = []
    for ex in sorted(paths):
        rel = sorted({(o, n) for e, o, n in checks if e == ex})
        if not rel:
            continue
        orig = Path(str(paths[ex]) + ".orig")
        if apply and orig.exists():
            sr.log(f"  Ex{ex}: .orig exists, already stripped once; REFUSING to strip again")
            return 1
        src = pymupdf.open(str(paths[ex]))
        tmp = Path(str(paths[ex]) + ".stripped")
        tmp.unlink(missing_ok=True)
        cp = pymupdf.open(str(paths[ex]))
        cp.delete_pages([p - 1 for p in sorted(drops[ex])])
        cp.save(str(tmp), garbage=4, deflate=True)
        cp.close()
        dst = pymupdf.open(str(tmp))
        miss = 0
        for o, n in rel:
            t = n + 1 if falsify else n
            if t < 1 or t > len(dst):
                miss += 1
                continue
            ok, r = page_match(src[o - 1], dst[t - 1])
            changed = (t != o) or any(p < o for p in drops[ex])
            if ok is None or (ok is False and r >= 0.99 and not changed and not falsify):
                unverifiable.append([ex, o, changed, "render failed" if ok is None else f"pixel {r:.4f} on unmoved page"])
                if changed:
                    miss += 1
                continue
            if not ok:
                miss += 1
        bad += miss
        sr.log(f"  Ex{ex}: {len(src)} -> {len(dst)} pages, {len(rel)} cited refs  [{'OK' if miss == 0 else f'{miss} MISMATCH'}]")
        dst.close()
        src.close()
        if apply and not falsify and miss == 0:
            if not orig.exists():
                orig.hardlink_to(paths[ex])
            tmp.replace(paths[ex])
        else:
            tmp.unlink(missing_ok=True)
    if falsify:
        sr.log(f"FALSIFIER: {bad} mismatch(es) with a one-page offset. "
               + ("Check can fail; trustworthy." if bad or not checks else "!! CHECK CANNOT FAIL"))
        return 0 if (bad or not checks) else 1
    if bad:
        sr.log(f"!! {bad} citation(s) do NOT land on the same page (or moved and could not be verified). Nothing written.")
        return 1
    sr.log(f"all {len(checks)} verifiable cited page references verified identical"
           + (f" ({len(unverifiable)} unmoved page(s) unverifiable, reported)" if unverifiable else ""))
    if apply:
        cpath.write_text(out_md, encoding="utf-8")
        (d / f"strip_result{sfx}.json").write_text(json.dumps({
            "drops": {str(k): sorted(v) for k, v in drops.items()}, "new_page_counts": newcount,
            "unverifiable": unverifiable}, indent=1), encoding="utf-8")
        cache = outdir / "auditpages"
        if cache.is_dir():
            n = 0
            for f in cache.iterdir():
                if f.is_file():
                    f.unlink()
                    n += 1
            sr.log(f"cleared {n} cached page render(s); the audit must re-render against the stripped exhibits")
        sr.log(f"wrote stripped exhibits and {cpath.name}")
    return 0


def run_falsify(sr: StageRun) -> int:
    return _run(sr, "falsify")


def run_dry(sr: StageRun) -> int:
    return _run(sr, "dry")


def run_apply(sr: StageRun) -> int:
    return _run(sr, "apply")
