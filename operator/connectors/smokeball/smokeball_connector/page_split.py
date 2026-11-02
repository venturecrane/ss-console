"""Split pages out of a large PDF into single-page PDFs, in a memory-capped child.

The page finder (page_finder.py) needs each page of a scanned set as its own
small PDF to show the model. The same reason page_cut.py runs in a child
applies here: a 413 MB set must never be read whole into the connector, a
child of the gateway. Same cap (``page_cut.memory_limit``), same timeout, the
reader given a file handle.

BANDS (2026-11-02, acceptance on the real sets). The index needs only what a
page's header and footer carry: date, provider, type, heading, "(continued)",
a page marker. Showing the whole page also showed the clinical images on it,
and the model declined 84 of the first 100 pages of one set (stop_reason
refusal). So by default each page goes as TWO strips cut from it: the top
:data:`TOP_BAND` of the page and the bottom :data:`BOTTOM_BAND`, by setting the
media and crop boxes of two copies of the page to those strips (the visual top,
whatever the page's /Rotate). Nothing in the middle of the page is rendered.
``full`` writes the whole page instead.

Usage (internal)::

    python -I -m smokeball_connector.page_split <source> <first> <last> <outdir> [bands|full]

Writes ``p<N>.pdf`` for each page first..last that exists (``last`` past the
end is clamped; ``last`` below ``first`` writes nothing) and prints one JSON
line ``{"total": <page count>, "written": [N, ...]}``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .page_cut import MEMORY_LIMIT_BYTES, TIMEOUT_SECONDS, CutRefused, _limit_memory, memory_limit

_EXIT_REFUSED = 3
#: The share of the page height (as the page is viewed) each strip shows.
TOP_BAND = 0.22
BOTTOM_BAND = 0.12


def _band(page: Any, top: bool, share: float) -> Any:
    """``page`` with its media and crop boxes set to the visual top or bottom
    strip of ``share`` of the page as viewed (honouring /Rotate)."""
    from pypdf.generic import RectangleObject

    x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    w, h = x1 - x0, y1 - y0
    rotate = int(page.get("/Rotate") or 0) % 360
    # The edge that is at the top when the page is viewed, in unrotated space.
    edge = {0: "y1", 90: "x0", 180: "y0", 270: "x1"}.get(rotate, "y1")
    if not top:
        edge = {"y1": "y0", "y0": "y1", "x0": "x1", "x1": "x0"}[edge]
    box = {
        "y1": [x0, y1 - h * share, x1, y1],
        "y0": [x0, y0, x1, y0 + h * share],
        "x0": [x0, y0, x0 + w * share, y1],
        "x1": [x1 - w * share, y0, x1, y1],
    }[edge]
    page.mediabox = RectangleObject(box)
    page.cropbox = RectangleObject(box)
    return page


def _child(source: str, first: int, last: int, outdir: str, view: str = "bands") -> int:
    from pypdf import PdfReader, PdfWriter

    try:
        with open(source, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            if reader.is_encrypted:
                print("the file is encrypted, so its pages cannot be read", file=sys.stderr)
                return _EXIT_REFUSED
            total = len(reader.pages)
            written = []
            for number in range(first, min(last, total) + 1):
                writer = PdfWriter()
                if view == "full":
                    writer.add_page(reader.pages[number - 1])
                else:
                    _band(writer.add_page(reader.pages[number - 1]), True, TOP_BAND)
                    _band(writer.add_page(reader.pages[number - 1]), False, BOTTOM_BAND)
                with open(Path(outdir) / f"p{number}.pdf", "wb") as out:
                    writer.write(out)
                written.append(number)
    except MemoryError:
        return 4
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal, named by class only
        print(f"the file could not be read as a PDF ({exc.__class__.__name__})", file=sys.stderr)
        return _EXIT_REFUSED
    print(json.dumps({"total": total, "written": written}))
    return 0


def split(
    source: Path, first: int, last: int, outdir: Path, *, limit: int | None = None, view: str = "bands"
) -> tuple[int, list[int]]:
    """(the file's page count, the pages written as ``outdir/p<N>.pdf``), or :class:`CutRefused`."""
    cap = limit if limit is not None else memory_limit()
    argv = [
        sys.executable,
        "-I",
        "-m",
        "smokeball_connector.page_split",
        str(source),
        str(first),
        str(last),
        str(outdir),
        "full" if view == "full" else "bands",
    ]
    try:
        # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is this interpreter, this module, and paths the connector itself built; no shell
        done = subprocess.run(  # noqa: S603 - this interpreter, this module, paths the connector built
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env={"SMD_PAGE_CUT_MEMORY_LIMIT": str(cap), "PATH": "/usr/bin:/bin"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CutRefused(f"splitting the pages took longer than {TIMEOUT_SECONDS} seconds") from exc
    if done.returncode == _EXIT_REFUSED:
        reason = (done.stderr or "").strip().splitlines()[-1:] or ["the pages could not be read"]
        raise CutRefused(reason[0][:300])
    if done.returncode != 0:
        raise CutRefused("the file is too large to read pages from safely on this seat")
    try:
        data = json.loads(((done.stdout or "").strip().splitlines() or ["{}"])[-1])
        return int(data["total"]), [int(n) for n in data["written"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise CutRefused("the pages could not be split out") from exc


def main(argv: list[str]) -> int:
    _limit_memory(int(os.environ.get("SMD_PAGE_CUT_MEMORY_LIMIT") or MEMORY_LIMIT_BYTES))
    source, first, last, outdir = argv[:4]
    view = argv[4] if len(argv) > 4 else "bands"
    return _child(source, int(first), int(last), outdir, view)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
