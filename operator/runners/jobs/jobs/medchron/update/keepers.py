"""Which pages of the delivered exhibits were never READ. $0.

A new page pixel-equal to a delivered page is set aside only when that
delivered page was read (``update/pageid``): a page of a delivered exhibit
that a past update set aside (a suspected or pixel-equal copy, a blank or
billing page) is filed in the firm's folder but its facts were never written
into the chronology, so a copy of it is no evidence they were. 2026-11-22: on
the largest saved update, 75 pages' facts never reached a delivered update.

What a past update read is its own record: the job directory of each
delivered update on the matter (``vision_seed.delivered_same_matter``) keeps
``runs/<unit>/update/new_exhibits.final.json``, each filed exhibit with the
pages it read (``read``) and its page count, under the final exhibit number
and title. A delivered exhibit is matched to it by file name ("<title>.pdf")
or by sha256 (a PDF exhibit is its source file as it is). A delivered exhibit
no past update filed (one the full chronology read whole) has every page
read. A past update whose job directory is gone settles nothing: its
exhibits count as read, as before this rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FINAL = "new_exhibits.final.json"


def _rows(job_dir: Path, unit: str) -> list[dict[str, Any]]:
    p = job_dir / "runs" / unit / "update" / FINAL
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    except (OSError, ValueError):
        return []
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def _pages(v: Any) -> set[int]:
    return {p for p in v if isinstance(p, int) and not isinstance(p, bool) and p > 0} if isinstance(v, list) else set()


def unread_pages(delivered: list[dict[str, Any]], past: list[list[dict[str, Any]]]) -> dict[int, set[int]]:
    """{delivered exhibit: its pages a past update filed but never read}.
    `delivered`: the plan's delivered exhibits ({exhibit, name, sha256});
    `past`: each delivered update's filed exhibits, newest first."""
    by_name: dict[str, dict[str, Any]] = {}
    by_sha: dict[str, dict[str, Any]] = {}
    for rows in past:
        for r in rows:
            by_name.setdefault(f"{r.get('title')}.pdf", r)
            if r.get("sha256"):
                by_sha.setdefault(str(r["sha256"]), r)
    out: dict[int, set[int]] = {}
    for e in delivered:
        r = by_name.get(str(e.get("name"))) or by_sha.get(str(e.get("sha256") or ""))
        n = r.get("pages") if r is not None else None
        if r is None or not isinstance(n, int) or isinstance(n, bool):
            continue
        gone = set(range(1, n + 1)) - _pages(r.get("read"))
        if gone:
            out[int(e["exhibit"])] = gone
    return out


def from_jobs(delivered: list[dict[str, Any]], job_dirs: list[Path], unit: str) -> dict[int, set[int]]:
    """``unread_pages`` from the delivered update job directories on the volume."""
    return unread_pages(delivered, [_rows(d, unit) for d in job_dirs])
