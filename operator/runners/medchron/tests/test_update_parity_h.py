"""Parity fix H: page identity by PICTURE, before the vision pass (2026-10-14).

A real update filed scanned copies of pages of other documents, and a re-file
of a delivered exhibit, as new exhibits: the text rules compare words, and a
scanned page has none until it is transcribed (and a delivered scan never
has any). Every test fails on the code before the fix (the branch head
885b21a1, which has no ``update_pages`` stage and no ``update/pageid``) and
passes after. Every page is an invented record rendered to an image-only
page, the way a scanner makes one."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf

LINES = [
    "Example Family Clinic progress note",
    "Chief complaint: neck pain after the collision",
    "Assessment: cervical strain, improving slowly",
    "Plan: home exercise program and recheck",
    "Medications reviewed and reconciled today",
    "Imaging ordered: cervical spine radiographs",
    "Work status: modified duty, no lifting",
    "Follow up with physical therapy twice weekly",
]


def _record(i: int, date: str | None = None, pain: int | None = None) -> str:
    """One invented visit page; `i` varies the layout so two records never
    look alike, `date` and `pain` vary only the values (a form filled in on
    another day)."""
    body = [LINES[(i + k) % len(LINES)] for k in range(3 + i % 5)]
    head = [
        f"Date of service {date or f'0{1 + i % 9}/1{i % 10}/2026'}",
        f"Pain rated {pain if pain is not None else 2 + i % 7} of 10",
    ]
    return "\n".join(head + body + ["-" * (20 + 7 * i)] * (1 + i % 3))


def _scan(text: str, jpeg: int | None = None) -> bytes:
    """A page image as a scanner makes it: the text rendered, no text layer.
    `jpeg` re-encodes it at that quality (a re-filed copy of the same scan)."""
    with pymupdf.open() as src:
        page = src.new_page()
        page.insert_text((72, 90), text, fontsize=12)
        k = len(text)  # the form's own layout: a stamp and a ruled box where this form has them
        top = 260 + 37 * (k % 11)
        page.draw_rect(
            pymupdf.Rect(60 + 23 * (k % 13), top, 300 + 19 * (k % 7), top + 40 + 9 * (k % 5)), fill=(0.3, 0.3, 0.3)
        )
        page.draw_rect(pymupdf.Rect(330, 620 - 15 * (k % 9), 540, 700), color=(0, 0, 0), width=2 + k % 3)
        pix = page.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY)
    return pix.tobytes("jpeg", jpg_quality=jpeg) if jpeg else pix.tobytes("png")


def _scan_pdf(images: list[bytes]) -> bytes:
    with pymupdf.open() as out:
        for img in images:
            page = out.new_page()
            page.insert_image(page.rect, stream=img)
        return out.tobytes()


def _seed(tmp: Path, delivered: dict[int, list[bytes]], docs: dict[str, list[bytes]]) -> SimpleNamespace:
    """A slug dir after the pull: the delivered exhibits, the plan naming
    them, and the new documents in pull order (all scans)."""
    ud = tmp / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    for d in ("delivered", "raw", "text"):
        (tmp / d).mkdir()
    exhibits = []
    for n, imgs in delivered.items():
        name = f"Exhibit {n} - Example Family Clinic (Medical Records).pdf"
        (tmp / "delivered" / name).write_bytes(_scan_pdf(imgs))
        exhibits.append({"exhibit": n, "name": name, "sha256": hashlib.sha256(_scan_pdf(imgs)).hexdigest()})
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": exhibits, "top_exhibit": max(delivered or {0: 0})}))
    rows = []
    for rid, imgs in docs.items():
        pdf = tmp / "raw" / f"{rid}.pdf"
        pdf.write_bytes(_scan_pdf(imgs))
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        rows.append({"id": rid, "ok": True, "path": str(pdf), "sha256": sha, "ext": ".pdf", "name": f"{rid} records"})
    (tmp / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp / "scan_queue.json").write_text(json.dumps([{"id": r["id"], "name": r["name"], "scan": True} for r in rows]))
    log: list[str] = []
    return SimpleNamespace(
        slug_dir=tmp,
        unit=SimpleNamespace(unit="alpha"),
        job=SimpleNamespace(units=[SimpleNamespace(surname="Example", dob="")]),
        log=log.append,
        logged=log,
    )


def _pages_run(sr: SimpleNamespace) -> dict:
    from medchron.stages import update_pages as UP

    assert UP.run(sr) == 0, sr.logged  # type: ignore[arg-type]
    return json.loads((sr.slug_dir / "vision_skip.json").read_text())


def _queued(sr: SimpleNamespace) -> dict[str, list[int]]:
    """The pages the vision pass would send, per document."""
    from medchron.stages import vision as V

    queue = json.loads((sr.slug_dir / "scan_queue.json").read_text())
    src = {
        json.loads(x)["id"]: json.loads(x)["path"]
        for x in (sr.slug_dir / "raw_manifest.jsonl").read_text().split("\n")
        if x
    }
    (sr.slug_dir / "partial").mkdir(exist_ok=True)
    _, pending = V._gather(sr, queue, src, sr.slug_dir / "ocr_results.jsonl")  # type: ignore[arg-type]
    out: dict[str, list[int]] = {}
    for rec, pno, _ in pending:
        out.setdefault(rec["id"], []).append(pno)
    return out


def _transcribe(sr: SimpleNamespace, texts: dict[str, list[str]], skip: dict) -> None:
    """What the vision pass leaves: each page's words, or the skip mark."""
    from medchron.update.pageid import SKIPPED

    for rid, pages in texts.items():
        body = "".join(
            f"[p.{n}] (machine transcription)\n{SKIPPED if n in skip.get(rid, []) else t}\n"
            for n, t in enumerate(pages, 1)
        )
        (sr.slug_dir / "text" / f"{rid}.txt").write_text(body)


def _select(sr: SimpleNamespace) -> dict:
    from medchron.stages import update_select as US

    assert US.run(sr) == 0, sr.logged  # type: ignore[arg-type]
    return json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "selection.json").read_text())


def test_a_refiled_scan_of_a_delivered_page_is_already_read_and_never_sent_to_vision(tmp_path: Path) -> None:
    a, b, c, d, e = (_record(i) for i in range(5))
    sr = _seed(tmp_path, {1: [_scan(a), _scan(b), _scan(c)]}, {"new": [_scan(d), _scan(b), _scan(e)]})
    skip = _pages_run(sr)
    assert skip == {"new": [2]}
    assert _queued(sr) == {"new": [1, 3]}  # the re-filed scan is never paid for
    _transcribe(sr, {"new": [d, b, e]}, skip)
    sel = _select(sr)
    (ex,) = sel["exhibits"]
    assert ex["read"] == [1, 3]
    assert ex["excluded"] == {"already read in a delivered exhibit": 1}


def test_a_scanned_extract_is_not_an_exhibit_and_is_cited_to_the_whole(tmp_path: Path) -> None:
    whole = [_record(i) for i in range(5, 11)]
    imgs = [_scan(t) for t in whole]
    delivered = [_record(i) for i in range(20, 22)]
    sr = _seed(
        tmp_path,
        {1: [_scan(t) for t in delivered]},
        # The extracts arrive FIRST in pull order: the whole one is still the exhibit.
        {"extract": imgs[1:4], "refile": [_scan(t) for t in delivered], "whole": imgs},
    )
    skip = _pages_run(sr)
    assert skip == {"extract": [1, 2, 3], "refile": [1, 2]}
    assert _queued(sr) == {"whole": [1, 2, 3, 4, 5, 6]}
    _transcribe(sr, {"extract": whole[1:4], "refile": delivered, "whole": whole}, skip)
    sel = _select(sr)
    by_id = {r["id"]: r for r in sel["exhibits"]}
    assert set(by_id) == {"whole"}
    assert by_id["whole"]["read"] == [1, 2, 3, 4, 5, 6]
    subs = {s["id"]: s["subset_of"] for s in sel["subsets"]}
    assert subs["extract"] == {"id": "whole", "exhibit": by_id["whole"]["exhibit"], "title": by_id["whole"]["title"]}
    assert subs["refile"]["exhibit"] == 1 and subs["refile"]["id"] == ""


def test_two_genuinely_different_scanned_pages_never_match(tmp_path: Path) -> None:
    from medchron.update import pageid as PI

    def sigs(texts: list[str]) -> list:
        p = tmp_path / f"{len(list(tmp_path.iterdir()))}.pdf"
        p.write_bytes(_scan_pdf([_scan(t) for t in texts]))
        return PI.signatures(p)

    mine, theirs = sigs([_record(i) for i in range(6)]), sigs([_record(i) for i in range(10, 16)])
    assert PI.pair_pages(mine, theirs) == {}
    assert PI.identify([(1, theirs)], [("new", mine)]).pages == {"new": {}}


def test_a_form_filled_in_on_another_day_beside_a_true_copy_is_still_read(tmp_path: Path) -> None:
    """The same form with another date and pain score looks like its twin to a
    picture; beside a re-encoded copy of the next page it even pairs in order.
    It is transcribed, its words disagree, and it is read."""
    form_a, form_b = _record(3, "03/02/2026", 6), _record(3, "03/09/2026", 4)
    nxt = _record(4)
    sr = _seed(tmp_path, {}, {"first": [_scan(form_a), _scan(nxt)], "second": [_scan(form_b), _scan(nxt, jpeg=40)]})
    skip = _pages_run(sr)
    assert skip == {}  # nothing pixel-equal: every page is transcribed
    ident = json.loads((tmp_path / "runs" / "alpha" / "update" / "page_identity.json").read_text())
    assert ident["pages"]["second"]["1"] == ["repeat", "first", 1, "near"]  # the picture alone would drop it
    assert _queued(sr) == {"first": [1, 2], "second": [1, 2]}
    _transcribe(sr, {"first": [form_a, nxt], "second": [form_b, nxt]}, skip)
    sel = _select(sr)
    by_id = {r["id"]: r for r in sel["exhibits"]}
    assert 1 in by_id["second"]["read"]  # another visit: read
    assert by_id["second"]["read"] == [1]  # the re-encoded copy whose words agree is a repeat
    assert by_id["second"]["excluded"] == {"repeats an earlier page of the new documents": 1}
