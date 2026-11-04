"""Pages whose text cannot carry the fact are read as images, with one
pointed question each.

Text extraction prints a blank signature line's words as if the form were
complete, so a "verified" or "signed" claim on a form page is confirmed from
the page image (taxonomy item 2: a blank verification recorded as verified).
A thin page (under ``THIN_CHARS`` of text) behind any fact, and a figure on a
machine-transcribed or thin page (OCR garbles digits), are read the same way.

Confirmed, the fact stands with what the image showed in ``image_reading``.
Not confirmed (``no``, ``unclear``, an unreadable answer, no page image to
read), the fact becomes ``inference``: it renders only as an attorney marker
(``{{A:id}}``), never as a statement. Answers are cached per page sha and
question.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from . import prompts
from .shapes import DocPages, Fact, Ledger

THIN_CHARS = 200
IMAGE_MAX_TOKENS = 1_024
STAGE = "evidence_images"
CACHE_DIR = ("evidence", "images")
_FORM = re.compile(r"(?i)\b(verification|declare under penalty|signature|signed|executed on|notary|sworn)\b")
_SIGNED_CLAIM = re.compile(r"(?i)\b(verif\w*|sign\w*|execut\w*|attest\w*|sworn|swore|notariz\w*)\b")


def question(f: Fact, page_text: str, machine: bool) -> str | None:
    """The question this fact's page must answer from its image, or None."""
    text = page_text or ""
    if _SIGNED_CLAIM.search(f.claim + " " + f.excerpt) and _FORM.search(text):
        return prompts.signed_question(f.claim)
    if f.kind == "figure" and (machine or len(text.strip()) < THIN_CHARS):
        from .render import figure_text

        return prompts.figure_question(figure_text(f))
    if f.kind != "inference" and len(text.strip()) < THIN_CHARS:
        return prompts.thin_question(f.claim, f.excerpt)
    return None


def _answer(text: str) -> tuple[str, str]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return "unclear", "the image read did not answer"
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return "unclear", "the image read did not answer"
    a = str(d.get("answer") or "").strip().lower()
    return (a if a in ("yes", "no", "unclear") else "unclear"), " ".join(str(d.get("reading") or "").split())[:300]


def _render(source: str | None, page: int, log: Callable[[str], None], name: str) -> str | None:
    """Base64 PNG of the page, by vision's own renderer, or None."""
    if not source or not str(source).lower().endswith(".pdf") or not Path(source).is_file():
        return None
    import pymupdf

    from ...stages.vision import render_page

    with pymupdf.open(str(source)) as doc:
        if not 1 <= page <= doc.page_count:
            return None
        return render_page(doc[page - 1], log, name, page)


def confirm(
    ledger: Ledger,
    corpus: Mapping[str, DocPages],
    data: Path,
    doorway: Any,
    model: str,
    log: Callable[[str], None] = print,
    renderer: Callable[[str | None, int, Callable[[str], None], str], str | None] = _render,
) -> Ledger:
    """The ledger with every page-image question asked and answered."""
    cache = data.joinpath(*CACHE_DIR)
    out: list[Fact] = []
    asked = 0
    for f in ledger:
        doc = corpus.get(f.file)
        if not f.usable or doc is None:
            out.append(f)
            continue
        q = question(f, doc.pages.get(f.page, ""), f.page in doc.machine)
        if q is None:
            out.append(f)
            continue
        key = hashlib.sha256(f"{f.page_sha}\0{q}\0{model}".encode("utf-8")).hexdigest()
        cp = cache / f"{key}.json"
        if cp.is_file():
            got = json.loads(cp.read_text(encoding="utf-8"))
            answer, reading = got["answer"], got["reading"]
        else:
            b64 = renderer(doc.source_path, f.page, log, f.file)
            if b64 is None:
                answer, reading = "unclear", "no page image to read"
            else:
                asked += 1
                r = doorway.call(
                    STAGE,
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                                {"type": "text", "text": q},
                            ],
                        }
                    ],
                    max_tokens=IMAGE_MAX_TOKENS,
                    custom_id=f"evimg-{key[:16]}",
                )
                answer, reading = _answer(r.text)
                cache.mkdir(parents=True, exist_ok=True)
                tmp = cache / f".{key}.json.tmp"
                tmp.write_text(json.dumps({"id": f.id, "answer": answer, "reading": reading}), encoding="utf-8")
                tmp.replace(cp)
        if answer == "yes":
            out.append(f.with_(image_reading=reading or "confirmed from the page image"))
        else:
            note = "; ".join(x for x in (f.note, f"page image: {answer}") if x)
            out.append(f.with_(kind="inference", image_reading=reading or answer, note=note))
    log(f"  evidence images: {asked} page image(s) read")
    return Ledger(tuple(out))
