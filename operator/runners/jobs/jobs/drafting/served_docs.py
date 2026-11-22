"""The finder's view of the job's disk: one ``Doc`` per extracted document (``served_find.py``).

Split from ``served_find`` (2026-11-21) when the form half's filing-mates and
our-side forms took it past the module-size ceiling. What it reads: the pull's
``extracted.jsonl`` (each document's text, by page), ``manifest.json`` (its
listed date), ``corpus.json`` (its sha256 and file on disk), and the job's
``caption.json`` when one is there.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: What one call reads: the first three pages and the last page, at most this.
EXCERPT_CHARS = 12_000
_PAGE = re.compile(r"(?m)^\[p\.(\d+)\]$")


@dataclass
class Doc:
    id: str
    name: str
    folder: str
    pages: dict[int, str]
    listed: str | None
    sha: str | None
    copies: list[str] = field(default_factory=list)
    ext: str = ""
    kind: str = ""
    path: str = ""

    @property
    def first(self) -> str:
        return "\n".join(self.pages.get(n, "") for n in (1, 2))

    @property
    def text(self) -> str:
        return "\n".join(self.pages[n] for n in sorted(self.pages))

    def excerpt(self) -> str:
        nums = sorted(self.pages)
        pick = [*nums[:3], *([nums[-1]] if len(nums) > 3 else [])]
        out = "\n".join(f"[p.{n}]\n{self.pages[n]}" for n in pick)
        return out if len(out) <= EXCERPT_CHARS else out[: EXCERPT_CHARS - 3000] + "\n...\n" + out[-3000:]


def _pages(text: str) -> dict[int, str]:
    marks = list(_PAGE.finditer(text))
    if not marks:
        return {1: text} if text.strip() else {}
    return {int(m.group(1)): text[m.end() : (marks[i + 1].start() if i + 1 < len(marks) else len(text))].strip()
            for i, m in enumerate(marks)}  # fmt: skip


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except ValueError:
        return default


def docs(data: Path) -> list[Doc]:
    """Every extracted document but email bodies, byte copies folded into one."""
    listing = {str(r.get("id")): r for r in load(Path(data) / "manifest.json", []) if isinstance(r, dict)}
    corpus = [r for r in load(Path(data) / "corpus.json", []) if isinstance(r, dict)]
    shas = {str(r.get("id")): r.get("sha256") for r in corpus}
    paths = {str(r.get("id")): str(r.get("path") or "") for r in corpus}
    p = Path(data) / "extracted.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.is_file() else []
    out: list[Doc] = []
    by_sha: dict[str, Doc] = {}
    for r in rows:
        if r.get("kind") == "email_body":
            continue
        rid, tp = str(r.get("id")), r.get("text_path")
        text = Path(tp).read_text(encoding="utf-8", errors="replace") if tp and Path(tp).is_file() else ""
        lst = listing.get(rid) or {}
        d = Doc(rid, str(r.get("name") or ""), str(r.get("folder") or ""), _pages(text),
                str(lst.get("created") or lst.get("modified") or "")[:10] or None, shas.get(rid),
                ext=str(r.get("ext") or Path(str(r.get("name") or "")).suffix).lower(), kind=str(r.get("kind") or ""),
                path=paths.get(rid, ""))  # fmt: skip
        if d.sha and d.sha in by_sha:
            by_sha[d.sha].copies.append(f"{d.folder}/{d.name}")
            continue
        by_sha.update({d.sha: d} if d.sha else {})
        out.append(d)
    return out


def other_side(data: Path) -> list[str]:
    """The other side's parties in the job's caption record, when one is on disk
    (a form set's asking party when its form fields carry no text)."""
    cap = load(Path(data) / "caption.json", {})
    rows = cap.get("parties") if isinstance(cap, dict) else None
    return [str(r["name"]) for r in rows or [] if isinstance(r, dict) and r.get("side") != "client" and r.get("name")]
