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
(when its controls held) plus any authored `drops[-unit].json`. A page the
chronology cites is never a classifier drop: the audit verified the claim
against that paper, so it stays in the exhibit and the classifier's call on it
is logged, not obeyed (Pamplona, 2026-10-24: one cited page called non-record
held a 4,418-page job). An AUTHORED drop of a cited page still refuses,
because a person removing evidence is a question. Summary-table
Reference cells carry their citation WITHOUT parentheses and are remapped
through the same path (nine delivered units once shipped their tables on
pre-strip numbers). A stripped exhibit is built by deleting pages from a copy
and saving (per-page insert_pdf reached 38 GB resident on a 2,141-page
production); the original stays beside it as `.orig`, a second apply refuses,
and the audit's render cache is cleared because every page number just moved.

An apply killed part way (the daemon requeues a signal-killed runner; an OOM
on a 4,000-page matter is the case) resumes rather than refusing. Before it
replaces anything, apply copies the chronology beside itself as
``final-chronology.md.prestrip``, and removes that copy only once the strip
result is written. While the copy exists the apply is unfinished: it reads
the document from the copy and each exhibit from its ``.orig``, and an
exhibit already replaced is kept as done only if its current file matches the
stripped result BY CONTENT (``_same_doc``); anything else still refuses. The
chronology is rewritten from the copy, so the rewrite is the same every time.

Rules since 2026-10-24, each for a legitimate record that used to stop a run:

* A citation to a page outside its exhibit (beyond the last page, or p. 0)
  passes through unchanged and is logged; the audit grades it
  PAGE_OUT_OF_RANGE. It used to crash the stage (KeyError), which is terminal.
* An exhibit whose moved cited page fails the content check after the delete
  (a form or annotated PDF re-renders) is left unstripped, its citations
  unchanged. The original paper with its original numbers is always true;
  the strip only ever removes non-record pages, so keeping them needs no
  disclosure. Only the falsifier refuses.
* The falsifier skips a cited page whose neighbour is byte-identical to it (a
  fax that repeats a page): the offset lands on the same paper and can prove
  nothing either way. Identity is measured exactly, apart from ``page_match``,
  so a blind ``page_match`` still fires.
* Scanned-classifier labels whose controls failed are not used, and the
  scanned pages stay; the run goes on.
* Each refusal has its own exit code (``EXIT_*``), so the reason the driver
  prints is the true one.
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

#: Exit codes, one per cause (dag.py gives each its reason). Falsify: the content
#: check cannot fail. Apply: the exhibits were already stripped once.
EXIT_BLIND = 1
#: An AUTHORED drop removes every page a citation rests on (a person's act).
EXIT_CITED_DROP = 2
#: An authored citation edit's text is not in the document.
EXIT_EDIT_NOT_FOUND = 3
#: nonrecord.json is missing: classify_nonrecord has not run.
EXIT_NO_CLASSIFIER = 4


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


def identical(a: Any, b: Any, dpi: int = 60) -> bool:
    """Exactly the same page: text equal and every pixel equal. Measured apart
    from ``page_match`` (no tolerance), so the falsifier's skip of a repeated
    fax page cannot be satisfied by a blind ``page_match``."""
    try:
        if a.get_text() != b.get_text():
            return False
        pa, pb = a.get_pixmap(dpi=dpi), b.get_pixmap(dpi=dpi)
    except Exception:  # noqa: BLE001 - a page the rasteriser cannot draw is not provably identical
        return False
    return (pa.width, pa.height) == (pb.width, pb.height) and pa.samples == pb.samples


def cited(doc_md: str) -> dict[int, set[int]]:
    """Every page the chronology cites, in prose and in summary-table cells."""
    out: dict[int, set[int]] = {}
    for m in CITE.finditer(doc_md):
        if m.group(2):
            out.setdefault(int(m.group(1)), set()).update(expand(m.group(2)))
    for m in TABLE_CITE.finditer(doc_md):
        out.setdefault(int(m.group(2)), set()).update(expand(m.group(3)))
    return out


def drop_set(
    sr: StageRun, paths: dict[int, Path], cites: dict[int, set[int]] | None = None
) -> dict[int, set[int]] | None:
    d = sr.slug_dir
    sfx = f"-{sr.unit.unit}" if sr.job.joint else ""
    nr = read_json(nonrecord_path(sr), None)
    if nr is None:
        sr.log("nonrecord.json not found; classify_nonrecord runs first")
        return None
    scanned = read_json(d / f"scanned_labels{sfx}.json", None)
    authored = read_json(d / f"drops{sfx}.json", {"manual_drops": {}, "citation_edits": []})
    drops: dict[int, set[int]] = {}
    if scanned and not scanned.get("controls_ok"):
        why = scanned.get("skipped") or "the classifier's controls failed"
        sr.log(f"scanned_labels not used ({why}); every scanned page stays in the exhibits")
    for ex in sorted(paths):
        s = set(nr[str(ex)]["drop_pages"])
        if scanned and scanned.get("controls_ok"):
            s |= {p for p, _lab in (scanned.get("nonrecord") or {}).get(str(ex), [])}
        kept = s & (cites or {}).get(ex, set())
        if kept:
            sr.log(
                f"Exhibit {ex}: kept {len(kept)} cited page(s) the classifier called non-record: {compress(sorted(kept))}"
            )
            s -= kept
        for p, _ in (authored.get("manual_drops") or {}).get(str(ex), []):
            s.add(p)
        drops[ex] = s
    return drops


def _remap_doc(
    doc_md: str, drops: dict[int, set[int]], remap: dict[int, dict[int, int]], log: Any
) -> tuple[str, list[tuple[int, int, int]], list[tuple[int, str, str]]]:
    """(remapped document, (exhibit, old page, new page) checks, problems).

    A page outside its exhibit (beyond the last page, or 0) passes through
    unchanged and is logged: the audit grades it PAGE_OUT_OF_RANGE. So does a
    citation to an exhibit that is not on disk."""
    checks: list[tuple[int, int, int]] = []
    problems: list[tuple[int, str, str]] = []

    def remap_spec(ex: int, spec: str) -> str | None:
        if ex not in remap:
            log(f"  Exhibit {ex} p.{spec}: no such exhibit file; citation left unchanged")
            return None
        pages = expand(spec)
        outside = [p for p in pages if p not in remap[ex] and p not in drops[ex]]
        if outside:
            log(f"  Exhibit {ex} p.{spec}: page(s) {compress(outside)} outside the exhibit; passed through unchanged")
        kept = [p for p in pages if p in remap[ex]]
        if not kept and not outside:
            problems.append((ex, spec, "every cited page was dropped"))
            return None
        new = [remap[ex][p] for p in kept]
        checks.extend((ex, o, nw) for o, nw in zip(kept, new))
        return compress(new + outside)

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

    return TABLE_CITE.sub(sub_table, CITE.sub(sub, doc_md)), checks, problems


def prestrip_path(cpath: Path) -> Path:
    """The pre-strip copy of the chronology an unfinished apply resumes from."""
    return cpath.with_name(cpath.name + ".prestrip")


def _sources(paths: dict[int, Path]) -> dict[int, Path]:
    """Each exhibit's pre-strip file: its ``.orig`` where one exists."""
    out: dict[int, Path] = {}
    for ex, p in paths.items():
        o = Path(str(p) + ".orig")
        out[ex] = o if o.exists() else p
    return out


def _same_doc(a: Path, b: Path, pages: list[int]) -> bool:
    """Is ``b`` the stripped result ``a``? Same page count, every page's text
    equal, and each cited page equal by ``page_match`` (text and pixels)."""
    import pymupdf

    with pymupdf.open(str(a)) as x, pymupdf.open(str(b)) as y:
        if len(x) != len(y) or any(x[i].get_text() != y[i].get_text() for i in range(len(x))):
            return False
        return all(1 <= n <= len(x) and page_match(x[n - 1], y[n - 1])[0] is True for n in pages)


def _verify(
    sr: StageRun,
    ex: int,
    path: Path,
    drops: set[int],
    rel: list[tuple[int, int]],
    falsify: bool,
    source: Path | None = None,
) -> tuple[int, int, list[list[Any]], Path]:
    """(mismatches, discriminating checks, unverifiable rows, the stripped copy)
    for one exhibit: the copy with its drops deleted, each cited page checked
    by content against the original. Under ``falsify`` the new page is offset
    by one, and a cited page whose neighbour is byte-identical is skipped (a
    repeated fax page), as is an exhibit's last page (the offset lands past
    the end): neither offset compares any content, so neither proves anything.
    The caller removes or keeps the copy."""
    import pymupdf

    src = pymupdf.open(str(source or path))
    tmp = Path(str(path) + ".stripped")
    tmp.unlink(missing_ok=True)
    cp = pymupdf.open(str(source or path))
    cp.delete_pages([p - 1 for p in sorted(drops)])
    cp.save(str(tmp), garbage=4, deflate=True)
    cp.close()
    dst = pymupdf.open(str(tmp))
    miss = counted = 0
    unverifiable: list[list[Any]] = []
    for o, n in rel:
        t = n + 1 if falsify else n
        if falsify and not 1 <= t <= len(dst):
            # The offset runs past the exhibit's last page: there is no page to
            # compare, so it is not comparable. Counting it as a mismatch would
            # "prove" the check with no content comparison at all.
            continue
        if falsify and identical(dst[n - 1], dst[t - 1]):
            continue
        counted += 1
        if t < 1 or t > len(dst):
            miss += 1
            continue
        ok, r = page_match(src[o - 1], dst[t - 1])
        changed = (t != o) or any(p < o for p in drops)
        if ok is None or (ok is False and r >= 0.99 and not changed and not falsify):
            unverifiable.append([ex, o, changed, "render failed" if ok is None else f"pixel {r:.4f} on unmoved page"])
            if changed:
                miss += 1
            continue
        if not ok:
            miss += 1
    sr.log(
        f"  Ex{ex}: {len(src)} -> {len(dst)} pages, {len(rel)} cited refs  [{'OK' if miss == 0 else f'{miss} MISMATCH'}]"
    )
    dst.close()
    src.close()
    return miss, counted, unverifiable, tmp


def _new_counts(paths: dict[int, Path], drops: dict[int, set[int]]) -> tuple[dict[int, dict[int, int]], dict[int, int]]:
    import pymupdf

    remap: dict[int, dict[int, int]] = {}
    newcount: dict[int, int] = {}
    for ex, path in paths.items():
        with pymupdf.open(str(path)) as doc:
            kept = [p for p in range(1, len(doc) + 1) if p not in drops[ex]]
        remap[ex], newcount[ex] = {p: i for i, p in enumerate(kept, 1)}, len(kept)
    return remap, newcount


def _run(sr: StageRun, mode: str) -> int:
    d = sr.slug_dir
    unit = sr.unit.unit
    sfx = f"-{unit}" if sr.job.joint else ""
    outdir = d / "out" / unit
    paths = resolve_exhibit_files(outdir)
    authored = read_json(d / f"drops{sfx}.json", {"manual_drops": {}, "citation_edits": []})
    cpath = d / "runs" / unit / "final-chronology.md"
    # An unfinished apply resumes from the pre-strip document and exhibits.
    resume = prestrip_path(cpath).is_file()
    doc_md = (prestrip_path(cpath) if resume else cpath).read_text(encoding="utf-8")
    src = _sources(paths) if resume else paths
    for e in authored.get("citation_edits") or []:
        if e["find"] not in doc_md:
            sr.log(f"!! citation edit not found: {e['find']}")
            return EXIT_EDIT_NOT_FOUND
        doc_md = doc_md.replace(e["find"], e["replace"])
    drops = drop_set(sr, paths, cited(doc_md))
    if drops is None:
        return EXIT_NO_CLASSIFIER
    apply, falsify = mode == "apply", mode == "falsify"
    n_drop = sum(len(v) for v in drops.values())
    sr.log(f"{unit}: dropping {n_drop} page(s)")
    if falsify and n_drop == 0:
        # The falsifier proves the remap CHECK can fail. With nothing to
        # strip there is no remap, so there is nothing for it to prove.
        sr.log("FALSIFIER: no pages to drop, so no remap to falsify; not applicable")
        return 0
    remap, newcount = _new_counts(src, drops)
    out_md, checks, problems = _remap_doc(doc_md, drops, remap, sr.log)
    if problems:
        for ex, spec, why in problems:
            sr.log(f"!! Exhibit {ex} p.{spec}: {why}")
        return EXIT_CITED_DROP
    sr.log(f"  remapped {len(set(checks))} distinct page reference(s)")
    v = _check_all(sr, (paths, src), drops, checks, apply=apply, falsify=falsify, resume=resume)
    if v is None:
        return EXIT_BLIND
    bad, counted, unverifiable, unstripped, staged = v
    if falsify:
        if checks and not counted:
            sr.log(
                "FALSIFIER: no cited page has a comparable neighbour (each is byte-identical to the next page, "
                "or is its exhibit's last page); nothing to falsify, not applicable"
            )
            return 0
        sr.log(
            f"FALSIFIER: {bad} mismatch(es) with a one-page offset over {counted} discriminating check(s). "
            + ("Check can fail; trustworthy." if bad or not checks else "!! CHECK CANNOT FAIL")
        )
        return 0 if (bad or not checks) else EXIT_BLIND
    if unstripped:
        remap, newcount = _new_counts(src, drops)
        out_md, checks, _ = _remap_doc(doc_md, drops, remap, lambda _line: None)
    sr.log(
        f"all {len(checks)} verifiable cited page references verified identical"
        + (f" ({len(unverifiable)} unmoved page(s) unverifiable, reported)" if unverifiable else "")
        + (f"; {len(unstripped)} exhibit(s) left unstripped" if unstripped else "")
    )
    if not apply:
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
        return 0
    result = {"drops": {str(k): sorted(p) for k, p in drops.items()}, "new_page_counts": newcount}
    result |= {"unverifiable": unverifiable, "unstripped": unstripped}
    _commit(sr, paths, staged, (cpath, out_md), (d / f"strip_result{sfx}.json", result))
    _clear_render_cache(sr, outdir)
    prestrip_path(cpath).unlink()  # last: the apply is finished
    sr.log(f"wrote stripped exhibits and {cpath.name}")
    return 0


def _commit(
    sr: StageRun,
    paths: dict[int, Path],
    staged: dict[int, Path],
    doc: tuple[Path, str],
    result: tuple[Path, dict[str, Any]],
) -> None:
    """Replace the exhibits and rewrite the chronology. The pre-strip copy is
    written (atomically) before anything is replaced, so a kill at any point
    after it leaves an apply that resumes from the same inputs."""
    cpath, out_md = doc
    pre = prestrip_path(cpath)
    if not pre.is_file():
        part = pre.with_name(pre.name + ".part")
        part.write_text(cpath.read_text(encoding="utf-8"), encoding="utf-8")
        part.replace(pre)
    for ex, tmp in staged.items():
        orig = Path(str(paths[ex]) + ".orig")
        if not orig.exists():
            orig.hardlink_to(paths[ex])
        tmp.replace(paths[ex])
    cpath.write_text(out_md, encoding="utf-8")
    result[0].write_text(json.dumps(result[1], indent=1), encoding="utf-8")
    sr.log(f"  replaced {len(staged)} exhibit(s)")


def _check_all(
    sr: StageRun,
    files: tuple[dict[int, Path], dict[int, Path]],
    drops: dict[int, set[int]],
    checks: list[tuple[int, int, int]],
    *,
    apply: bool,
    falsify: bool,
    resume: bool = False,
) -> tuple[int, int, list[list[Any]], list[dict[str, Any]], dict[int, Path]] | None:
    """Verify every exhibit with a cited page: (mismatches, discriminating
    checks, unverifiable rows, exhibits left unstripped, staged copies). None
    when apply finds an exhibit already stripped once (outside a resume) or,
    resuming, an exhibit that is neither its original nor the stripped result
    by content. Outside ``falsify`` an
    exhibit with a mismatch is left whole (its drops emptied), because the same
    paper with its original numbers is always true."""
    bad = counted = 0
    unverifiable: list[list[Any]] = []
    unstripped: list[dict[str, Any]] = []
    staged: dict[int, Path] = {}
    paths, src = files
    for ex in sorted(paths):
        rel = sorted({(o, n) for e, o, n in checks if e == ex})
        if not rel:
            continue
        orig = Path(str(paths[ex]) + ".orig")
        if apply and orig.exists() and not resume:
            sr.log(f"  Ex{ex}: .orig exists, already stripped once; REFUSING to strip again")
            return None
        miss, n_counted, unver, tmp = _verify(sr, ex, paths[ex], drops[ex], rel, falsify, src[ex])
        bad, counted = bad + miss, counted + n_counted
        unverifiable += unver
        done = _resumed(sr, ex, (orig, paths[ex], tmp), miss, [n for _o, n in rel]) if apply and resume else False
        if done is None:
            tmp.unlink(missing_ok=True)
            return None
        if done:
            tmp.unlink(missing_ok=True)
            continue
        if falsify or miss:
            tmp.unlink(missing_ok=True)
        else:
            staged[ex] = tmp
        if miss and not falsify:
            sr.log(
                f"  Ex{ex}: {miss} moved cited page(s) failed the content check after the delete; this exhibit is "
                f"left unstripped and its citations unchanged"
            )
            unstripped.append({"exhibit": ex, "reason": f"{miss} moved cited page(s) failed the content check"})
            drops[ex] = set()
    return bad, counted, unverifiable, unstripped, staged


def _resumed(sr: StageRun, ex: int, files: tuple[Path, Path, Path], miss: int, pages: list[int]) -> bool | None:
    """Resuming an apply: True when the interrupted run already replaced this
    exhibit with the stripped result (checked by content, never by the marker),
    False when it is still the original (it is stripped now), None when it is
    neither, a real defect that refuses."""
    orig, current, tmp = files
    if not orig.exists() or orig.samefile(current):
        return False
    if not miss and _same_doc(tmp, current, pages):
        sr.log(f"  Ex{ex}: already stripped by the interrupted apply (verified by content); kept")
        return True
    sr.log(f"  Ex{ex}: .orig exists and the exhibit is not the stripped result by content; REFUSING")
    return None


def _clear_render_cache(sr: StageRun, outdir: Path) -> None:
    cache = outdir / "auditpages"
    if cache.is_dir():
        n = 0
        for f in cache.iterdir():
            if f.is_file():
                f.unlink()
                n += 1
        sr.log(f"cleared {n} cached page render(s); the audit must re-render against the stripped exhibits")


def run_falsify(sr: StageRun) -> int:
    return _run(sr, "falsify")


def run_dry(sr: StageRun) -> int:
    return _run(sr, "dry")


def run_apply(sr: StageRun) -> int:
    return _run(sr, "apply")
