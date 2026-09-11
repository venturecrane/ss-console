"""Rasterise one exhibit page for the image audit. Thread-safe, cached,
atomic.

413 of 914 cited pages on one matter were cited by more than one claim; a
render keyed only on (exhibit, page) had concurrent workers writing the same
file while another read it, yielding truncated PNGs and hard 400s. So: one
lock per output path, render to a thread-unique temp, atomic replace, reuse
an existing good render. pymupdf renders here (the frozen script shelled out
to pdftoppm, an undeclared system dependency); the target is 1600 px on the
long side (about 145 dpi on a letter page), stepping down to 1100 when the
encoded image would exceed the API's per-image limit.
"""

from __future__ import annotations

import base64
import os
import threading
from pathlib import Path

IMAGE_B64_LIMIT = 4_500_000
SCALES = (1600, 1100)
_render_locks: dict[str, threading.Lock] = {}
_render_guard = threading.Lock()


def render(pdf: Path, page: int, pages_dir: Path, tag: str) -> Path | None:
    """The PNG for `page` (1-based) of `pdf`, or None when it cannot be made."""
    import pymupdf

    pages_dir.mkdir(parents=True, exist_ok=True)
    final = pages_dir / f"{tag}_p{page}.png"
    with _render_guard:
        lk = _render_locks.setdefault(str(final), threading.Lock())
    with lk:
        if final.is_file() and final.stat().st_size > 1024:
            return final
        tmp = pages_dir / f".tmp_{tag}_p{page}_{threading.get_ident()}.png"
        doc = pymupdf.open(str(pdf))
        try:
            if page < 1 or page > len(doc):
                return None
            pg = doc[page - 1]
            long_side = max(pg.rect.width, pg.rect.height) or 1.0
            for target in SCALES:
                zoom = target / long_side
                data = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
                if len(base64.b64encode(data)) <= IMAGE_B64_LIMIT:
                    tmp.write_bytes(data)
                    os.replace(tmp, final)
                    return final
            return None
        finally:
            doc.close()


def img_block(path: Path) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(path.read_bytes()).decode(),
        },
    }
