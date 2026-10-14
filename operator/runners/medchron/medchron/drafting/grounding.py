"""The attorney notes' basis table, grounded in code.

WHY (2026-10-14). The notes' end tables were model prose, written fresh on
every run, and each pre-delivery review found a different slip in them: a
recipient's address cited to responses that do not say it, the firm's
unserved internal draft cited as the plaintiff's served responses, "only the
topics are in the file" of a set the file holds whole. A second model reading
the first one's prose is the same non-convergence one layer up. So the model
now gives ROWS, never prose: ``{document, request, file, paper, quote}``; code
keeps a row only when

* ``file`` is a document in the matter's file (by name);
* the file is the kind of paper the row says (``paper``): a row that calls it
  served, or responses, is dropped when the file is an unserved draft, or is
  not responses (``kind``);
* ``quote`` is in that file's own text, word for word after normalization;

and renders what survives. A dropped row becomes "no verified basis cite;
check the file" for its request; it never rides as prose.

The not-in-record and reserved-decision tables are gone: a served set carries
no NOT IN RECORD slot (``wording.slots``), and the attorney's decisions are the
document's own ``{{ATTORNEY}}`` flags, listed by code with their document and
place (``notes``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from . import render

MIN_QUOTE = 12
_WORDS = re.compile(r"[a-z0-9]+")
_RESPONSES = re.compile(r"(?i)\bresponses?\s+to\b.{0,120}?(interrogator|requests?\s+for)")
_DRAFT = re.compile(r"(?i)\bdraft\b")


def _norm(s: str) -> str:
    return " ".join(_WORDS.findall(s.lower()))


@dataclass
class Row:
    document: str
    request: int
    file: str
    paper: str
    quote: str


@dataclass
class Grounded:
    rows: list[tuple[Row, str, str]]  # (row, the file's name, what the file is, as code reads it)
    dropped: dict[str, list[int]]  # document -> requests whose row did not verify


def parse(text: str) -> list[Row]:
    """The model's rows; anything that is not a well-formed row is no row."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").split("\n", 1)[-1]
    start, end = body.find("{"), body.rfind("}")
    try:
        data = json.loads(body[start : end + 1]) if start >= 0 else {}
    except ValueError:
        return []
    out = []
    for r in data.get("basis") or [] if isinstance(data, dict) else []:
        try:
            out.append(
                Row(str(r["document"]), int(r["request"]), str(r["file"]), str(r.get("paper") or ""), str(r["quote"]))
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def find_file(name: str, corpus: Mapping[str, str]) -> str | None:
    """The corpus document a row's ``file`` names (as written, or its stem)."""
    want = render._stem(re.sub(r"(?i),?\s*p(?:age)?s?\.?\s*[\d\-, ]+$", "", name.strip().removeprefix("FILE:").strip()))
    hits = [n for n in corpus if render._stem(n) == want]
    return hits[0] if len(hits) == 1 else None


def kind(name: str, text: str) -> str:
    """What the file is, as code reads its own name and face."""
    face = text[: render.FACE_CHARS]
    if _DRAFT.search(name) or re.search(r"(?m)^\W*DRAFT\b", face):
        return "unserved draft"
    if _RESPONSES.search(face):
        return "served responses" if render.SERVED.search(text) or "VERIFICATION" in text.upper() else "responses"
    if any(render.is_the_set(text, k) for k in render.SET_TITLES):
        return "served set" if render.SERVED.search(text) else "unserved set"
    return "document"


def _claim_holds(claim: str, actual: str) -> bool:
    c = claim.lower()
    if "served" in c and "unserved" not in c and not actual.startswith("served"):
        return False
    if "response" in c and "responses" not in actual:
        return False
    return True


def verify(rows: list[Row], corpus: Mapping[str, str]) -> Grounded:
    keep: list[tuple[Row, str, str]] = []
    dropped: dict[str, list[int]] = {}
    for r in rows:
        name = find_file(r.file, corpus)
        ok = name is not None and len(_norm(r.quote)) >= MIN_QUOTE
        actual = kind(name, corpus[name]) if name else ""
        ok = ok and _claim_holds(r.paper, actual) and _norm(r.quote) in _norm(corpus[name or ""])
        if ok and name:
            keep.append((r, name, actual))
        else:
            dropped.setdefault(r.document, []).append(r.request)
    return Grounded(keep, dropped)


def render_table(g: Grounded, documents: list[tuple[str, list[int]]]) -> str:
    """The basis table per document: each verified row, then the requests with
    no verified cite (one line), so a gap is said, never filled."""
    out = []
    for label, numbers in documents:
        rows = sorted((x for x in g.rows if x[0].document == label), key=lambda x: x[0].request)
        lines = [f"### {label}", "", "| Request | Basis in the file |", "| --- | --- |"]
        lines += [
            f'| No. {r.request} | FILE: {name} ({what}): "{" ".join(r.quote.split())}" |' for r, name, what in rows
        ]
        cited = {r.request for r, _n, _w in rows}
        missing = [n for n in numbers if n not in cited]
        if not rows:
            lines = lines[:2]
        if missing:
            lines += [
                "",
                "No verified basis cite; check the file: Nos. " + ", ".join(str(n) for n in missing),
            ]
        out.append("\n".join(lines))
    return "\n\n".join(out)


def documents(papers: list[Any]) -> list[tuple[str, list[int]]]:
    """(label, request numbers) of each delivered document."""
    from .compose import split_items

    return [(p.label, sorted(split_items(p.text)[1])) for p in papers]
