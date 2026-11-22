"""One source tree's page decisions over generated matters, as JSON.

Run by ``test_update_set_aside_matches_base.py``, once per tree, each in its
own interpreter:

    python update_set_aside_driver.py <tree> <matters> <out.json>

Every matter is invented from a fixed seed, so both trees see the same files.
It goes through the real ``update_pages`` (pictures, the page question) and
``update_select``; only the vision pass is stood in for, by writing each
page's own words, or the skip mark ``update_pages`` asked for.
"""

from __future__ import annotations

import json
import random
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])

import jobs

if not Path(jobs.__file__ or "").resolve().is_relative_to(Path(sys.argv[1]).resolve()):
    raise SystemExit(f"measured the wrong tree: {jobs.__file__}")

import pymupdf

from jobs.medchron import pages_record as PR
from jobs.medchron.update import charged, pageid as PI
from jobs.stages import update_pages, update_select

WORDS = (
    "neck pain collision cervical spasm rotation tenderness therapy ibuprofen recheck lumbar strain radiograph disc "
    "normal guarded motion gait reflex sensation brace exercise modified duty lifting follow clinic weeks provider"
).split()
REPORT = "Independent medical examination report\nPrepared for counsel on review of the enclosed records of treatment."
BILL = "Itemized statement. Charges 120.00 payment 0.00 balance 120.00 " * 3
FAILED = "[page transcription failed]"


def _visit(rng: random.Random) -> str:
    dated = f"Date of service: {rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/2026\n" if rng.random() > 0.2 else ""
    body = "\n".join(" ".join(rng.choice(WORDS) for _ in range(9)) for _ in range(rng.randint(4, 8)))
    return (
        f"{dated}Provider: Roe, Jane (M.D.)\n{body}\nPain rated {rng.randint(1, 9)} of 10. Pulse {rng.randint(60, 99)}."
    )


def _vary(rng: random.Random, t: str) -> str:
    """The page as another copy has it: stamped, retyped, missing a line, one value or word changed."""
    k = rng.randint(0, 5)
    if k == 1:
        return f"Printed 11/{rng.randint(1, 28):02d}/2026 by records\n" + t
    if k == 2:
        return t.replace("Pulse", "Heart rate")
    if k == 3:
        return "\n".join(x for x in t.split("\n") if not x.startswith("Pain"))
    if k == 4:
        return t + "\nillegible stamp"
    return t.replace(" of 10", " of 10 today") if k == 5 else t


def _rebreak(rng: random.Random, pages: list[str]) -> list[str]:
    lines, out = "\n".join(pages).split("\n"), []
    while lines:
        n = rng.randint(4, 9)
        out.append("\n".join(lines[:n]))
        lines = lines[n:]
    return out


def _document(rng: random.Random, pool: list[str]) -> list[str]:
    k = rng.randint(1, 8)
    s = rng.randint(0, len(pool) - k)
    pages = [_vary(rng, p) if rng.random() < 0.4 else p for p in pool[s : s + k]]
    if rng.random() < 0.3:
        pages = _rebreak(rng, pages)
    if rng.random() < 0.2:
        pages.insert(rng.randint(0, len(pages)), "")
    if rng.random() < 0.15:
        pages.append(BILL)
    return [REPORT, *pages] if rng.random() < 0.15 else pages


def _reading(rng: random.Random, t: str) -> str:
    """What a transcription of the page says: as printed, a word or a line off, or nothing."""
    r = rng.random()
    if r < 0.01:
        return FAILED
    return _vary(rng, t) if r < 0.25 and t else t


def _rescan(path: Path, old: Path, cut: int, pages: list[str], rng: random.Random) -> list[str]:
    """The same paper scanned again (a mark apart, never pixel for pixel), some
    of its forms filled in with another value. Returns what its pages say."""
    out = [re.sub(r"Pulse \d+", f"Pulse {rng.randint(100, 140)}", t) if rng.random() < 0.4 else t for t in pages[cut:]]
    with pymupdf.open(str(old)) as src, pymupdf.open() as new:
        new.insert_pdf(src, from_page=cut)
        for page in new:
            page.insert_text((500, 780), "copy", fontsize=6)
        new.save(str(path))
    return out


def _pdf(path: Path, pages: list[str], rng: random.Random) -> None:
    with pymupdf.open() as doc:
        for t in pages:
            page = doc.new_page()
            page.insert_text((30, 60), t, fontsize=11)
            k = sum(map(ord, t.split("\n")[0])) if t else 0  # the form's own layout: a box where this form has one
            if t:
                page.draw_rect(pymupdf.Rect(60 + k % 200, 300 + k % 260, 320 + k % 170, 380 + k % 300), fill=(0.3,) * 3)
            if t and rng.random() < 0.15:
                page.insert_text((500, 780), "copy", fontsize=6)  # the same page, not pixel for pixel
        doc.save(str(path))


def matter(seed: int, tmp: Path) -> dict[str, list[str]]:
    """Lay one matter out under `tmp`; returns {document id: its pages' words}."""
    rng = random.Random(seed)
    pool = [_visit(rng) for _ in range(12)]
    ud = tmp / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    for x in ("delivered", "raw", "text"):
        (tmp / x).mkdir()
    exhibits = []
    for n in range(1, rng.randint(2, 3)):
        name = f"Exhibit {n} - Example Clinic (Medical Records).pdf"
        _pdf(tmp / "delivered" / name, [_vary(rng, p) for p in rng.sample(pool, rng.randint(1, 5))], rng)
        exhibits.append({"exhibit": n, "name": name, "sha256": f"x{n}"})
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": exhibits, "top_exhibit": len(exhibits)}))
    docs, rows = {}, []
    for n in range(rng.randint(2, 5)):
        rid, pages = f"d{n}", _document(rng, pool)
        name = f"{rid} " + ("expert report" if rng.random() < 0.08 else "records")
        again = rng.random() if docs else 1.0
        if again < 0.4:  # the same paper again: scanned again, or with other bytes; whole, or part of it
            src = rng.choice(sorted(docs))
            cut = rng.randint(0, len(docs[src]) - 1)
        if again < 0.25:
            pages = _rescan(tmp / "raw" / f"{rid}.pdf", tmp / "raw" / f"{src}.pdf", cut, docs[src], rng)
        elif again < 0.4:
            with pymupdf.open(str(tmp / "raw" / f"{src}.pdf")) as old, pymupdf.open() as new:
                new.insert_pdf(old, from_page=cut)
                new.save(str(tmp / "raw" / f"{rid}.pdf"))
            pages = docs[src][cut:]
        else:
            _pdf(tmp / "raw" / f"{rid}.pdf", pages, rng)
        docs[rid] = pages
        rows.append(
            {"id": rid, "ok": True, "path": str(tmp / "raw" / f"{rid}.pdf"), "sha256": rid, "ext": ".pdf", "name": name}
        )
    (tmp / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp / "scan_queue.json").write_text(json.dumps([{"id": r["id"], "name": r["name"], "scan": True} for r in rows]))
    (tmp / "extracted.jsonl").write_text(
        "".join(json.dumps({"id": d, "pages": len(p)}) + "\n" for d, p in docs.items())
    )
    return {d: [_reading(rng, t) for t in p] for d, p in docs.items()}


def decide(seed: int, tmp: Path) -> dict:
    texts, log = matter(seed, tmp), []
    total = sum(map(len, texts.values()))
    unit = SimpleNamespace(unit="alpha")
    job = SimpleNamespace(units=[SimpleNamespace(surname="Example", dob="")])
    sr = SimpleNamespace(slug_dir=tmp, unit=unit, job=job, log=log.append)
    try:
        if update_pages.run(sr) != 0:  # type: ignore[arg-type]
            raise RuntimeError(log[-1])
        skip = json.loads((tmp / "vision_skip.json").read_text())
        for d, pages in texts.items():
            read = [PI.SKIPPED if n in skip.get(d, []) else t for n, t in enumerate(pages, 1)]
            body = "".join(f"[p.{n}] (machine transcription)\n{t}\n" for n, t in enumerate(read, 1))
            (tmp / "text" / f"{d}.txt").write_text(body)
        asked = charged.asked_pages(tmp, "alpha", total)
        code: int | str = update_select.run(sr)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - a crash is an answer to compare, not this driver's failure
        return {"exit": f"{type(exc).__name__}: {exc}", "rows": {}}
    is_charged = getattr(PR, "charged", lambda r: r["status"] in PR.READ_STATUSES)
    pictures, rows = json.loads((tmp / "runs/alpha/update/page_identity.json").read_text())["pages"], {}
    for r in PR.read(tmp / "runs" / "alpha"):
        ev = r["evidence"]
        exact = bool((ev.get("cover") or {}).get("exact"))
        near = (pictures.get(r["doc"]) or {}).get(str(r["page"]), ["", "", 0, ""])[3] == "near"
        rows[f"{r['doc']} {r['page']}"] = [
            r["status"],
            r["class"],
            bool(is_charged(r)),
            ev.get("suspected"),
            exact,
            near,
        ]
    out = {"exit": code, "rows": rows, "asked": asked, "charged": charged.charged_pages(tmp, "alpha", total)}
    out["capped"] = charged.cap_notes(tmp, "alpha")
    PR.path(tmp / "runs" / "alpha").unlink()
    (tmp / "runs" / "alpha" / charged.ASKED).unlink()
    out["fallback"] = charged.charged_pages(tmp, "alpha", total)  # what a run with no page record charges
    return out


def main() -> None:
    out = {}
    for seed in range(int(sys.argv[2])):
        with tempfile.TemporaryDirectory() as td:
            out[seed] = decide(seed, Path(td))
    Path(sys.argv[3]).write_text(json.dumps(out))


if __name__ == "__main__":
    main()
