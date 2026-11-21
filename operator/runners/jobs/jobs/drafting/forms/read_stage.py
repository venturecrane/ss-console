"""Which boxes a served DISC-001 checks, read by the Operator before the answers.

For one served form set file: the form's eight pages are found by their text
layer ("DISC-001" beside "Page N of 8"; all eight or the job stops
``form_incomplete`` for SMD), rendered, and read three ways: two independent
model reads with ``reader.READER_INSTRUCTION`` (each a paid call through the
lane's doorway, so each is a ledger row) and the $0 pixel read
(``checkbox.pixel_read``). ``reader.reconcile`` decides. The list is used ONLY
when both model reads agree, the pixel read agrees on every page it
registered, nothing handwritten or attached adds an interrogatory, no reader
flagged a page it could not read, the form's revision is one the catalogue
carries and the count matches the set's ``request_count``. Anything else stops
``form_unconfirmed`` for SMD, who confirms the boxes with the served CLI's form
keys and resumes; a served set that already carries ``checked`` is never read
again. Never a guess.

What it writes: the served set's ``form``, ``form_revision``, ``checked``,
``definition_boxes`` and ``additions`` in ``served.json`` (the keys the served
schema carries), and ``form-read.json`` in the job directory, every read as it
came back, the pixel ratios and the reasons, so SMD sees all three.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping

from ...matter import pdf as pdf_mod
from ..outcome import DraftingFailed
from . import checkbox
from .catalog import DISC_001, CatalogueError, load
from .checked import compact
from .reader import READER_INSTRUCTION, ReaderRead, ReplyError, parse_reply, reconcile

STAGES = ("form_read_a", "form_read_b")
RECORD = "form-read.json"
SERVED = "served.json"
PAGES = 8
MAX_TOKENS = 2000
_REVISION = re.compile(r"DISC-001\s*\[\s*(Rev\.[^\]]+?)\s*\]")


def png(image: Any) -> bytes:
    """A grayscale page image as PNG bytes (what each reader is shown)."""
    import pymupdf

    g = checkbox.gray(image)
    return pymupdf.Pixmap(pymupdf.csGRAY, g.shape[1], g.shape[0], g.astype("uint8").tobytes(), 0).tobytes("png")


def revision_of(texts: list[str]) -> str | None:
    # Whitespace is collapsed BEFORE the dedupe: one revision printed with two
    # spaces on one page is still one revision (2026-11-20 replay: every real form).
    found = {" ".join(m.group(1).split()) for t in texts for m in _REVISION.finditer(t)}
    return next(iter(found)) if len(found) == 1 else None


def _stop(job_dir: Path, record: dict[str, Any], code: str, why: str) -> DraftingFailed:
    record["outcome"] = code
    _write(Path(job_dir) / RECORD, record)
    return DraftingFailed(f"{code}: {why}; SMD confirms the form before anything is answered")


def _write(p: Path, obj: Any) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    tmp.replace(p)


def _one_read(doorway: Any, stage: str, model: str, pngs: list[bytes], cat: Any) -> tuple[ReaderRead | None, str]:
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(b).decode()}}
        for b in pngs
    ]
    content.append({"type": "text", "text": READER_INSTRUCTION})
    res = doorway.call(stage, model=model, messages=[{"role": "user", "content": content}], max_tokens=MAX_TOKENS)
    text = str(getattr(res, "text", "") or "")
    try:
        return parse_reply(text, cat), text
    except ReplyError as exc:
        return None, f"{text}\n\n(not used: {exc})"


def served_set(job_dir: Path, index: int) -> dict[str, Any]:
    return json.loads((Path(job_dir) / SERVED).read_text(encoding="utf-8"))["sets"][index]


def read_served_form(
    pdf: Path | None,
    job_dir: Path,
    index: int,
    doorway: Any,
    model: str,
    *,
    texts: list[str] | None = None,
    images: Mapping[int, Any] | None = None,
    encode: Callable[[Any], bytes] = png,
    log: Callable[[str], None] = lambda _m: None,
) -> list[str]:
    """The checked numbers of served set ``index``, written into ``served.json``.
    ``texts`` (each page's text layer, ``matter/pdf.page_texts``) and ``images``
    (form page -> image) are read from the file at ``pdf`` when not given. Raises ``form_incomplete`` /
    ``form_unconfirmed`` (``DraftingFailed``, resumable) for SMD."""
    s = served_set(job_dir, index)
    if s.get("checked"):
        return list(s["checked"])  # SMD confirmed it, or an earlier read did: never read twice
    texts = texts if texts is not None else [str(t) for t in pdf_mod.page_texts(pdf or "")]
    found = sorted({n for n in (checkbox.form_page(t, DISC_001, PAGES) for t in texts) if n is not None})
    record: dict[str, Any] = {"set": index, "form_pages_found": [n + 1 for n in found]}
    if found != list(range(PAGES)):
        missing = [n + 1 for n in range(PAGES) if n not in found]
        raise _stop(job_dir, record, "form_incomplete", f"the served form is missing form page(s) {missing}")
    rev = revision_of(texts)
    record["revision"] = rev
    try:
        cat = load(DISC_001, rev or "")
    except CatalogueError:
        raise _stop(job_dir, record, "form_unconfirmed", f"the form's revision ({rev}) is not one we carry") from None
    images = images if images is not None else checkbox.form_pages(Path(pdf or "").read_bytes())
    pngs = [encode(images[n]) for n in range(PAGES) if n in images]
    reads = [_one_read(doorway, st, model, pngs, cat) for st in STAGES]
    pixel = checkbox.pixel_read(images, cat=cat)
    rec = reconcile(reads[0][0], reads[1][0], pixel, cat)
    record.update(
        reads=[{"stage": st, "reply": raw, "read": asdict(r) if r else None} for st, (r, raw) in zip(STAGES, reads)],
        pixel={"ratios": dict(pixel.ratios), "unreadable_pages": [p + 1 for p in pixel.unreadable_pages]},
        reconciled=asdict(rec),
    )
    unreadable = [u for r, _ in reads if r for u in r.unreadable]
    why = list(rec.to_smd) if (not rec.agreed or rec.additions or unreadable) else []
    count = s.get("request_count")
    if not why and isinstance(count, int) and count != len(rec.checked):
        why.append(f"{len(rec.checked)} boxes are checked but the set's count is {count}")
    if why:
        raise _stop(job_dir, record, "form_unconfirmed", "; ".join(why)[:300])
    record["outcome"] = "agreed"
    _write(Path(job_dir) / RECORD, record)
    raw = json.loads((Path(job_dir) / SERVED).read_text(encoding="utf-8"))
    raw["sets"][index].update(
        form=cat.form,
        form_revision=cat.revision,
        checked=list(rec.checked),
        definition_boxes=list(rec.definition_boxes),
        additions=False,
    )
    _write(Path(job_dir) / SERVED, raw)
    log(f"  form read: {len(rec.checked)} boxes checked ({compact(rec.checked, cat)}), three reads agree")
    return list(rec.checked)


def pixel_read(pdf: Path) -> dict[str, Any]:
    """The $0 pixel read alone of one served DISC-001 file: no model, nothing
    written (the replay's form-read stage). Its revision must be one the
    catalogue carries (``CatalogueError`` otherwise)."""
    texts = [str(t) for t in pdf_mod.page_texts(pdf)]
    cat = load(DISC_001, revision_of(texts) or "")
    px = checkbox.pixel_read(checkbox.form_pages(Path(pdf).read_bytes()), cat=cat)
    on = px.checked()
    boxes = set(cat.definition_boxes)
    return {
        "checked": sorted((k for k in on if k not in boxes), key=cat.position),
        "unregistered": [],
        "definition_boxes": sorted(k for k in on if k in boxes),
        "form": cat.form,
        "form_revision": cat.revision,
        "unreadable_pages": [p + 1 for p in px.unreadable_pages],
    }


def _source_paths(data: Path) -> dict[str, Path]:
    """Each matter document's file on disk, by its id (the pull's ``corpus.json``)."""
    p = Path(data) / "corpus.json"
    rows = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    return {str(r["id"]): Path(str(r["path"])) for r in rows if isinstance(r, dict) and r.get("id") and r.get("path")}


def _unread_operator_form(s: Any) -> bool:
    return (
        isinstance(s, dict)
        and s.get("set_type") == "form_interrogatories"
        and s.get("drafted_by") == "operator"
        and bool(s.get("source"))
        and not s.get("checked")
    )


def read_found(
    data: Path, job_dir: Path, doorway: Any, model: str, *, by_operator: bool, log: Callable[[str], None]
) -> list[int]:
    """The ``find`` stage's form read: every served form set the Operator drafts
    (``drafted_by`` operator, which needs ``served.FORM_SETS_BY_OPERATOR``) and
    that names its matter document (``source``) but no ``checked`` yet is read
    by ``read_served_form``, so ``served.json`` carries ``form``,
    ``form_revision`` and ``checked`` before the plan. Off (``by_operator``
    False): nothing is read and nothing is spent; a form set stays SMD's.
    Returns the indexes read, in order."""
    p = Path(job_dir) / SERVED
    if not by_operator or not p.is_file():
        return []
    sets = json.loads(p.read_text(encoding="utf-8")).get("sets") or []
    paths, done = _source_paths(data), []
    for i, s in enumerate(sets):
        if not _unread_operator_form(s):
            continue
        pdf = paths.get(str(s["source"]))
        if pdf is None or not pdf.is_file():
            raise _stop(job_dir, {"set": i}, "form_incomplete", f"the served form's file {s['source']} is not on disk")
        read_served_form(pdf, job_dir, i, doorway, model, log=log)
        done.append(i)
    return done
