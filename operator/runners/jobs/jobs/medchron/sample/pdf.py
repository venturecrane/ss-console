"""Render a document as a provider-styled PDF, and most of its pages as scans.

A real production arrives mostly as images: faxed, photocopied, a little
crooked, with the sending machine's header across the top and speckle where
the glass was dirty. Those are the pages the pipeline pays to transcribe, and
a demo set made of clean text pages would never exercise that stage. So each
page marked ``scan`` is drawn, rasterized, skewed, speckled, stamped with its
fax header and put back as an image with no text layer. Hand-filled form
answers are drawn in a slanted face with a wandering baseline, and a page
marked ``illegible`` is blurred until nobody can read it.

Byte-stable: every random choice comes from a generator keyed by the matter
seed, the document and the page; fonts are the PDF base 14 or files shipped
with matplotlib (already a runtime dependency); the PDF is written without a
fresh /ID and with fixed metadata.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import layout
from .model import Document, Page, Plan, rng

W, H, M = 612, 792, 54
DPI = 100
_META = {
    "producer": "SMD sample records",
    "creator": "SMD sample records",
    "creationDate": "D:20260101000000Z",
    "modDate": "D:20260101000000Z",
    "title": "",
    "author": "",
    "subject": "",
    "keywords": "",
}


def _font_dir() -> Path:
    import matplotlib

    return Path(matplotlib.get_data_path()) / "fonts" / "ttf"


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    if pymupdf.get_text_length(text, fontname=font, fontsize=size) <= width:
        return [text]
    indent = text[: len(text) - len(text.lstrip())]
    out, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else indent + word
        if cur and pymupdf.get_text_length(trial, fontname=font, fontsize=size) > width:
            out.append(cur)
            cur = indent + "  " + word
        else:
            cur = trial
    return out + [cur] if cur else out


def _style(plan: Plan, doc: Document) -> dict[str, Any]:
    st = plan.providers[doc.provider].style
    return {
        "face": str(st.get("face", "helv")),
        "accent": tuple(float(x) for x in st.get("accent", (0.15, 0.25, 0.45))),
        "layout": str(st.get("layout", "rule")),
        "size": float(st.get("size", 9.5)),
    }


def _draw_header(pg: pymupdf.Page, plan: Plan, doc: Document, st: dict[str, Any], top: float) -> float:
    head = layout.header(plan, doc)
    accent, font = st["accent"], st["face"]
    if st["layout"] == "band":
        pg.draw_rect(pymupdf.Rect(0, top, W, top + 44), color=accent, fill=accent)
        pg.insert_text((M, top + 22), head[0], fontname="hebo", fontsize=13, color=(1, 1, 1))
        pg.insert_text((M, top + 36), head[1], fontname=font, fontsize=7.5, color=(1, 1, 1))
        return top + 62
    if st["layout"] == "box":
        pg.draw_rect(pymupdf.Rect(M, top + 8, M + 34, top + 42), color=accent, fill=accent)
        initials = "".join(w[0] for w in head[0].split()[:3])
        pg.insert_text((M + 5, top + 31), initials, fontname="hebo", fontsize=12, color=(1, 1, 1))
        pg.insert_text((M + 44, top + 24), head[0], fontname="tibo", fontsize=12.5, color=accent)
        pg.insert_text((M + 44, top + 37), head[1], fontname=font, fontsize=7.5)
        return top + 58
    pg.insert_text((M, top + 24), head[0], fontname="hebo" if font != "tiro" else "tibo", fontsize=13, color=accent)
    pg.insert_text((M, top + 37), head[1], fontname=font, fontsize=7.5)
    pg.draw_line((M, top + 43), (W - M, top + 43), color=accent, width=1.2)
    return top + 60


def _body_lines(page: Page, font: str, size: float) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    for i, line in enumerate(page.lines):
        hand = i in page.hand
        for part in _wrap(line, font, size + (1.5 if hand else 0), W - 2 * M):
            out.append((part, hand))
    return out


def _hand(pg: pymupdf.Page, x: float, y: float, text: str, size: float, gen: Any) -> None:
    """Ink: a slanted face, a wandering baseline, a little size jitter."""
    cx = x
    for ch in text:
        s = size + gen.uniform(-0.6, 0.9)
        pg.insert_text((cx, y + gen.uniform(-1.2, 1.2)), ch, fontname="hand", fontsize=s, color=(0.08, 0.12, 0.45))
        cx += pymupdf.get_text_length(ch, fontname="helv", fontsize=s) * gen.uniform(0.98, 1.12)


def draw(pg: pymupdf.Page, plan: Plan, doc: Document, page: Page, *, scan: bool) -> None:
    st = _style(plan, doc)
    gen = rng(plan.matter["seed"], doc.id, page.number, "hand")
    y = _draw_header(pg, plan, doc, st, 14 if scan and doc.fax else 0)
    if page.title:
        pg.insert_text((M, y), page.title.upper(), fontname="hebo", fontsize=10.5)
        y += 18
    size = st["size"]
    lines = _body_lines(page, st["face"], size)
    room = H - y - 70
    while len(lines) * size * 1.32 > room and size > 6.5:
        size -= 0.5
        lines = _body_lines(page, st["face"], size)
    if page.hand:
        pg.insert_font(fontname="hand", fontfile=str(_font_dir() / "DejaVuSans-Oblique.ttf"))
    for text, hand in lines:
        if hand:
            _hand(pg, M, y, text.strip(), size + 1.5, gen)
        else:
            pg.insert_text((M, y), text, fontname=st["face"], fontsize=size)
        y += size * 1.32
    foot = layout.footer(plan, doc, page)
    pg.draw_line((M, H - 58), (W - M, H - 58), color=(0.6, 0.6, 0.6), width=0.5)
    pg.insert_text((M, H - 46), foot[0], fontname="helv", fontsize=7.5)
    pg.insert_text((W - M - 60, H - 46), foot[1], fontname="helv", fontsize=7.5)
    pg.insert_text((M, H - 34), foot[2], fontname="heit", fontsize=7, color=(0.35, 0.35, 0.35))


def _scan(plan: Plan, doc: Document, page: Page) -> bytes:
    """The page as a fax or photocopy would deliver it: JPEG bytes."""
    tmp = pymupdf.open()
    draw(tmp.new_page(width=W, height=H), plan, doc, page, scan=True)
    pix = tmp[0].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    tmp.close()
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    gen = rng(plan.matter["seed"], doc.id, page.number, "scan")
    fax = layout.fax_line(plan, doc, page)
    if fax:
        mono = ImageFont.truetype(str(_font_dir() / "DejaVuSansMono.ttf"), 13)
        ImageDraw.Draw(img).text((40, 6), fax, fill=30, font=mono)
    img = img.rotate(gen.uniform(-1.6, 1.6), resample=Image.Resampling.BICUBIC, fillcolor=255)
    if page.illegible:
        img = img.filter(ImageFilter.GaussianBlur(4.5)).point(lambda v: 150 + v // 3)
    dr = ImageDraw.Draw(img)
    for _ in range(gen.randint(150, 450) * (4 if page.illegible else 1)):
        x, y = gen.randrange(img.width), gen.randrange(img.height)
        dr.point((x, y), fill=gen.randint(0, 120))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=52, optimize=False)
    return buf.getvalue()


def render(plan: Plan, doc: Document) -> bytes:
    out = pymupdf.open()
    for page in doc.pages:
        pg = out.new_page(width=W, height=H)
        if page.scan:
            pg.insert_image(pg.rect, stream=_scan(plan, doc, page))
        else:
            draw(pg, plan, doc, page, scan=False)
    out.set_metadata(dict(_META, title=doc.name))
    data = out.tobytes(garbage=3, deflate=True, no_new_id=True)
    out.close()
    return data
