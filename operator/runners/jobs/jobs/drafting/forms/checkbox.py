"""Which boxes are checked on a served form, read from the page pixels in code.

The served form interrogatories are computer-made PDFs whose text layer does
NOT carry the checkbox marks (measured 2026-11-20), so the boxes are read from
rendered page images. This module is the $0 cross-check beside the two model
readers (``reader.py``): on an unscanned print of the SAME revision, every
box sits where the official PDF puts it (``catalog.Box``), so the share of
dark pixels inside it says whether it is marked.

Registration first: the page image is matched to the official geometry by
searching a small uniform offset and scale for the transform under which the
printed box outlines are darkest. A page that does not register (a scan, a
different revision, a rotated page) is ``unreadable``, never guessed at.

Images are numpy arrays (height x width, grayscale 0..255, or RGB/RGBA); the
form page each image shows is the caller's mapping (``form_page`` reads it
from a page's text layer).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ...matter import pdf as matter_pdf
from .catalog import Box, Catalogue, load

DARK = 128  # a pixel darker than this is ink
REGISTER_MIN = 0.80  # share of box-outline samples that must be ink at the best transform
OFFSET_PT = 6.0  # largest uniform shift searched, in points
SCALES = (0.985, 0.99, 0.995, 1.0, 1.005, 1.01, 1.015)
ASPECT_TOLERANCE = 0.02
INSET_PT = 1.5  # the inside of a box excludes its outline by this much
FILLED_MIN = 0.05  # share of ink inside a box that reads as checked
_PAGE_OF = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)")


@dataclass(frozen=True)
class PixelRead:
    """Per box key (an interrogatory number, or "4(a)(1)"/"4(a)(2)"), the
    share of ink inside it, for boxes on pages that registered; and the form
    pages that did not register (or had no image)."""

    ratios: Mapping[str, float]
    unreadable_pages: tuple[int, ...]
    offsets: Mapping[int, tuple[float, float, float]] = field(default_factory=dict)  # page -> (dx px, dy px, scale)

    def checked(self, threshold: float = FILLED_MIN) -> frozenset[str]:
        return frozenset(k for k, v in self.ratios.items() if v >= threshold)

    @property
    def registered(self) -> frozenset[str]:
        return frozenset(self.ratios)


def geometry(cat: Catalogue | None = None) -> dict[str, Box]:
    """Every box on the form: interrogatory numbers and the Sec. 4(a) boxes."""
    cat = cat or load()
    out = {i.number: i.box for i in cat.interrogatories}
    out.update(cat.definition_boxes)
    return out


def form_page(text: str, form: str = "DISC-001", pages: int = 8) -> int | None:
    """The 0-based form page a served page's text layer says it is ("Page 3 of
    8" beside the form id), or None (a scan, a cover page, a proof of service)."""
    if form not in text:
        return None
    m = _PAGE_OF.search(text)
    if not m or int(m.group(2)) != pages or not 1 <= int(m.group(1)) <= pages:
        return None
    return int(m.group(1)) - 1


def gray(image: Any) -> np.ndarray:
    a = np.asarray(image)
    if a.ndim == 3:
        a = a[..., :3].mean(axis=2)
    if a.ndim != 2:
        raise ValueError("a page image is height x width (grayscale) or height x width x channels")
    return a.astype(np.float32)


def _dilate(mask: np.ndarray) -> np.ndarray:
    """Ink grown by one pixel, so a hairline outline is found at a rounding offset."""
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= out[:, :-1].copy()
    out[:, :-1] |= out[:, 1:].copy()
    return out


def _outline_points(boxes: list[Box]) -> np.ndarray:
    """Sample points (x, y in points) along every box outline."""
    pts: list[tuple[float, float]] = []
    for b in boxes:
        x0, y0, x1, y1 = b.rect
        for t in np.linspace(0.1, 0.9, 12):
            x = x0 + (x1 - x0) * t
            pts += [(x, y0), (x, y1)]
        for t in np.linspace(0.2, 0.8, 5):
            y = y0 + (y1 - y0) * t
            pts += [(x0, y), (x1, y)]
    return np.array(pts, dtype=np.float32)


def _score(ink: np.ndarray, pts: np.ndarray, scale: float, dx: float, dy: float) -> float:
    h, w = ink.shape
    xs = np.rint(pts[:, 0] * scale + dx).astype(int)
    ys = np.rint(pts[:, 1] * scale + dy).astype(int)
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    if not ok.any():
        return 0.0
    return float(ink[ys[ok], xs[ok]].sum()) / len(xs)


def register(img: np.ndarray, boxes: list[Box], page_size: tuple[float, float]) -> tuple[float, float, float] | None:
    """(dx, dy, pixels per point) mapping the official geometry onto ``img``,
    or None when the page does not register."""
    h, w = img.shape
    if abs((h / w) / (page_size[1] / page_size[0]) - 1) > ASPECT_TOLERANCE:
        return None
    ink = _dilate(img < DARK)
    pts = _outline_points(boxes)
    base = w / page_size[0]
    reach = int(round(OFFSET_PT * base))
    best = (0.0, 0.0, 0.0, base)
    for k in SCALES:
        s = base * k
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                sc = _score(ink, pts, s, dx, dy)
                if sc > best[0]:
                    best = (sc, float(dx), float(dy), s)
    return (best[1], best[2], best[3]) if best[0] >= REGISTER_MIN else None


def _inside(ink: np.ndarray, rect: tuple[float, float, float, float], t: tuple[float, float, float]) -> float:
    dx, dy, s = t
    x0, y0, x1, y1 = rect
    a = int(np.ceil((x0 + INSET_PT) * s + dx))
    b = int(np.floor((x1 - INSET_PT) * s + dx))
    c = int(np.ceil((y0 + INSET_PT) * s + dy))
    d = int(np.floor((y1 - INSET_PT) * s + dy))
    region = ink[max(c, 0) : max(d, 0), max(a, 0) : max(b, 0)]
    return float(region.mean()) if region.size else 0.0


def pixel_read(
    page_images: Mapping[int, Any], geo: Mapping[str, Box] | None = None, cat: Catalogue | None = None
) -> PixelRead:
    """The ink share inside every box, per registered page. ``page_images``
    maps a 0-based form page to its rendered image."""
    cat = cat or load()
    geo = geo or geometry(cat)
    by_page: dict[int, dict[str, Box]] = {}
    for key, box in geo.items():
        by_page.setdefault(box.page, {})[key] = box
    ratios: dict[str, float] = {}
    bad: list[int] = []
    offsets: dict[int, tuple[float, float, float]] = {}
    for page, boxes in sorted(by_page.items()):
        if page not in page_images:
            bad.append(page)
            continue
        img = gray(page_images[page])
        t = register(img, list(boxes.values()), cat.page_size)
        if t is None:
            bad.append(page)
            continue
        offsets[page] = t
        ink = img < DARK
        ratios.update({k: _inside(ink, b.rect, t) for k, b in boxes.items()})
    return PixelRead(ratios, tuple(bad), offsets)


def render_pages(pdf: bytes | Path, dpi: int = 150) -> list[np.ndarray]:
    """Every page of a PDF as a grayscale image (the readers' 150 dpi), rendered
    by the one PDF home (``jobs/matter/pdf.py``)."""
    return [np.asarray(g) for g in matter_pdf.page_grays(pdf, dpi)]


def map_form_pages(texts: Sequence[str], images: Sequence[Any]) -> dict[int, np.ndarray]:
    """Each served page's image keyed by the form page its text layer says it
    is. A page whose text names no form page (a scan, a proof of service) is
    left out, and its form page then reads as unreadable."""
    if len(texts) != len(images):
        raise ValueError("one text layer per page image")
    out: dict[int, np.ndarray] = {}
    for text, image in zip(texts, images):
        n = form_page(str(text))
        if n is not None and n not in out:
            out[n] = gray(image)
    return out


def form_pages(pdf: bytes | Path, dpi: int = 150) -> dict[int, np.ndarray]:
    """A served PDF's form pages, keyed by the form page each one says it is
    (its text and images read through ``jobs/matter/pdf.py``)."""
    return map_form_pages(matter_pdf.page_texts(pdf), render_pages(pdf, dpi))
