"""Pages a matter file holds more than once are read once.

WHY (2026-11-06). A file in suit carries the same records several times: a
provider's chart produced to the firm, produced again in a later request, and
again inside a subpoena response. On one matter 1,221 of 8,193 text pages were
word-for-word repeats of an earlier page and 124 of 1,952 scanned pages were
the same image as another. Every repeat was summarized, and every repeated
scan transcribed, at full price, and the summary then carried the same visit
two or three times.

Two folds, both exact, neither a judgment:

* **Scanned pages** (``transcribe``): two pages whose rendered image is byte
  for byte the same are sent once, and the one reading is written to both.
* **Text pages** (``summarize``): a page whose words are the same as an
  earlier page's, after case, punctuation and spacing are dropped, is replaced
  in the summarizer's input by one line saying where it was first read.

A look-alike is not a repeat: a form with the same layout and different
handwriting differs in its image and in its words, and is read. Short pages
(under ``MIN_CHARS`` of words) are never folded: a cover sheet and a blank
back share nothing worth pointing at.

Every location is kept. The pointer line names the first location in the
text the summary is made from, and ``record`` lists each repeated page with
where it was first read, written beside the job's other records. The extracted
text files are not changed: the quotation check and the review still read
every page where it lies.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

#: A page with fewer normalized characters than this is never folded.
MIN_CHARS = 200
_MARK = re.compile(r"(\[p\.(\d+)\]\n?)")
RECORD = "repeated-pages.json"


def norm(text: str) -> str:
    """The page's words: case, punctuation and spacing dropped."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def pages(text: str) -> list[tuple[int, str, str]]:
    """``(page number, its marker, its body)`` for a text with ``[p.N]``
    markers; a text with none is one page. Text before the first marker (a
    file header) rides with page 0 and is never folded."""
    parts = _MARK.split(text)
    if len(parts) == 1:
        return [(1, "", text)]
    out = [(0, "", parts[0])] if parts[0] else []
    for i in range(1, len(parts), 3):
        out.append((int(parts[i + 1]), parts[i], parts[i + 2]))
    return out


def pointer(name: str, page: int, whole: bool) -> str:
    """The line the summarizer reads in place of a repeated page."""
    what = "This document repeats" if whole else "This page repeats"
    return f"({what} FILE: {name}, p. {page}, word for word; it is read there.)\n"


class Repeats:
    """The first location of every page read so far, and each later location
    of the same words."""

    def __init__(self) -> None:
        #: words -> (file name, page, which fold call): two files can share a
        #: name, so a location is the call it was read in, never the name.
        self._first: dict[str, tuple[str, int, int]] = {}
        self._files = 0
        self.rows: list[dict[str, Any]] = []
        self.chars = 0

    def fold(self, name: str, text: str) -> str:
        """``text`` with every page already read elsewhere replaced by a
        pointer to its first location. Call once per file, in corpus order."""
        split = pages(text)
        whole = len(split) == 1 and not split[0][1]
        self._files += 1
        out = []
        for number, mark, body in split:
            words = norm(body) if number else ""
            if len(words) < MIN_CHARS:
                out.append(mark + body)
                continue
            key = hashlib.sha256(words.encode("utf-8")).hexdigest()
            first = self._first.setdefault(key, (name, number, self._files))
            if first[1:] == (number, self._files):
                out.append(mark + body)
                continue
            self.rows.append({"file": name, "page": number, "first_file": first[0], "first_page": first[1]})
            self.chars += len(body)
            out.append(mark + pointer(first[0], first[1], whole))
        return "".join(out)

    def record(self) -> dict[str, Any]:
        return {"pages": len(self.rows), "characters": self.chars, "repeats": self.rows}

    def save(self, data: Path, key: str = "text") -> None:
        """Merge this fold's record into the job's ``repeated-pages.json``
        under ``key`` (``text`` or ``scanned``)."""
        save(data, key, self.record())


def save(data: Path, key: str, record: dict[str, Any], add: bool = False) -> None:
    """``add``: keep the rows already recorded under ``key`` (a resumed
    transcription finds only the pages still unread, so its fold is partial)."""
    path = data / RECORD
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if add and isinstance(doc.get(key), dict):
        rows = list(doc[key].get("repeats") or [])
        rows += [r for r in record["repeats"] if r not in rows]
        record = {**record, "pages": len(rows), "repeats": rows}
    doc[key] = record
    tmp = data / f".{RECORD}.tmp"
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)


def same_images(jobs: list[tuple[dict[str, Any], int, str]]) -> tuple[list[tuple[dict[str, Any], int, str]], dict]:
    """``jobs`` are ``(document row, page, base64 image)``. Returns the pages
    to read (the first of each image) and, for each of those, the other
    ``(row, page)`` locations that carry the same image byte for byte."""
    first: dict[str, tuple[str, int]] = {}
    keep: list[tuple[dict[str, Any], int, str]] = []
    twins: dict[tuple[str, int], list[tuple[dict[str, Any], int]]] = {}
    for row, page, b64 in jobs:
        key = hashlib.sha256(b64.encode("ascii")).hexdigest()
        if key in first:
            twins.setdefault(first[key], []).append((row, page))
            continue
        first[key] = (str(row["id"]), page)
        keep.append((row, page, b64))
    return keep, twins


def scanned_record(
    keep: list[tuple[dict[str, Any], int, str]], twins: dict[tuple[str, int], list[tuple[dict[str, Any], int]]]
) -> dict[str, Any]:
    names = {(str(r["id"]), n): str(r.get("name")) for r, n, _ in keep}
    rows = [
        {"file": str(r.get("name")), "page": n, "first_file": names[at], "first_page": at[1]}
        for at, others in twins.items()
        for r, n in others
    ]
    return {"pages": len(rows), "repeats": rows}
