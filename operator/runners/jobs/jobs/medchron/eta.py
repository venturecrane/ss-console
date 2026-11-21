"""The chronology's expected finish: minutes per stage from the matter's size.

One pure function, :func:`expected`, from MEASURED serial rates (each constant
names its basis), and :func:`stamp`, which writes the answer once on the run's
state after extract and logs it. It is for SMD, never shown to the firm.

The rates were read off three real runs' stage timestamps (494, 986 and 3,821
pages, 2026-11-18/19). Between and beyond them a stage is read off the
straight lines joining those points, extended by the last segment's slope,
so a figure never falls as pages rise.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import budget
from ..stages import compose_pool

#: Serial page reading, scanned pages per minute (measured: 144 minutes for
#: the 3,821-page run's scanned pages, one call at a time).
VISION_PAGES_PER_MIN = 14.5
#: The writer, one call at a time: (pages, minutes), measured on the three runs.
WRITER_SERIAL = ((494, 22.0), (986, 78.0), (3821, 362.0))
#: The full entry read: 34 minutes at 3,821 pages, measured ALREADY eight at
#: once, so the worker count does not divide it.
READER = ((3821, 34.0),)
#: The review: measured at the three sizes, unchanged by this build.
REVIEW = ((494, 2.5), (986, 13.6), (3821, 40.5))
#: Every other stage together (listing, download, extract, assemble, merge,
#: build, delivery): measured at the three sizes.
OTHER = ((494, 12.0), (986, 20.0), (3821, 34.0))
#: Which stages each figure covers; a figure whose stages are all done drops out.
STAGES = {"vision": ("vision",), "writer": ("map",), "reader": ("audit",), "review": ("review",)}
BASIS = "measured serial rates at 494, 986 and 3,821 pages (2026-11-18/19), writer and page reading divided by workers"


def _line(points: tuple[tuple[int, float], ...], pages: int) -> float:
    """Minutes at `pages` on the lines through (0, 0) and `points`."""
    pts = ((0, 0.0), *points)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if pages <= x1:
            return y0 + (y1 - y0) * (pages - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    return y1 + (y1 - y0) * (pages - x1) / (x1 - x0)


def expected(pages: int, scanned: int, workers: int, done: Iterable[str] = ()) -> dict[str, float]:
    """Minutes per figure (vision, writer, reader, review, other) and `total`,
    for a matter of `pages` pages, `scanned` of them read as images, with the
    writer and page reading `workers` calls at once; a figure whose stages
    are all in `done` is 0."""
    w = max(1, int(workers))
    pages, scanned = max(0, int(pages)), max(0, int(scanned))
    out = {
        "vision": scanned / VISION_PAGES_PER_MIN / w,
        "writer": _line(WRITER_SERIAL, pages) / w,
        "reader": _line(READER, pages),
        "review": _line(REVIEW, pages),
        "other": _line(OTHER, pages),
    }
    finished = set(done)
    for key, names in STAGES.items():
        if all(n in finished for n in names):
            out[key] = 0.0
    out = {k: round(v, 1) for k, v in out.items()}
    out["total"] = round(sum(out.values()), 1)
    return out


def stamp(st: Any, extracted: Path, log: Callable[[str], None], workers: int | None = None) -> None:
    """Write `st.expected_finish` once, when extract's rows exist, and log it."""
    if st.expected_finish is not None or not extracted.is_file():
        return
    done = [n for n, r in st.stages.items() if r.status == "done"]
    w = compose_pool.COMPOSE_WORKERS if workers is None else workers
    mins = expected(budget.pages_read(extracted), budget.scanned_pages(extracted), w, done)
    now = time.time()
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + mins["total"] * 60))
    st.expected_finish = {
        "at": at,
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "basis": BASIS,
        "minutes": mins,
    }
    st.save()
    log(f"[eta] expected finish {at} ({mins['total']} min: {mins})")
