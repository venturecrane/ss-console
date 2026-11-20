"""A produced response body, split into its numbered responses.

``--body`` takes either the answer stage's parsed output, JSON::

    {"form": {"6.4": {"kind": "answered_from_file", "text": "..."}},
     "special": {"3": {"kind": "objection_only", "text": "..."}},
     "production": {...}}

(a flat ``{"6.4": {...}}`` is read as form numbers), or the body as filed, a
``.docx`` (read by ``jobs.matter.word``) or plain text. A text body is cut at
its response headings (``RESPONSE TO SPECIAL INTERROGATORY NO. 3:``, or the
bare heading where no ``RESPONSE TO`` line follows it), and each response's
kind is read by the answer stage's own reader when that module is present
(``stages.kind_reader``); with no reader the kind is None, which the
comparison reports as unread rather than guessing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .key import number

_HEADING = re.compile(
    r"^[ \t]*(RESPONSE TO[ \t]+)?(FORM INTERROGATORY|SPECIAL INTERROGATORY|REQUEST FOR PRODUCTION)"
    r"[ \t]+NO\.?[ \t]*([0-9]+(?:\.[0-9]+)?)[ \t]*:?",
    re.IGNORECASE | re.MULTILINE,
)
_FAMILY = {"FORM INTERROGATORY": "form", "SPECIAL INTERROGATORY": "special", "REQUEST FOR PRODUCTION": "production"}

Responses = dict[tuple[str, str], dict[str, Any]]


def _from_json(doc: Any) -> Responses:
    out: Responses = {}
    if not isinstance(doc, dict):
        return out
    grouped = any(k in doc for k in _FAMILY.values())
    groups = {k: doc.get(k) for k in _FAMILY.values()} if grouped else {"form": doc}
    for family, items in groups.items():
        for n, r in (items or {}).items() if isinstance(items, dict) else []:
            if isinstance(r, dict):
                key = number(n) if family == "form" else str(n).strip()
                out[(family, key)] = {"kind": r.get("kind"), "text": str(r.get("text") or "")}
    return out


def split(text: str, kind_of: Callable[[str], str | None] | None) -> Responses:
    """Each heading's response text, the RESPONSE TO block preferred."""
    hits = list(_HEADING.finditer(text))
    out: Responses = {}
    for i, m in enumerate(hits):
        family = _FAMILY[m.group(2).upper()]
        n = number(m.group(3)) if family == "form" else str(int(m.group(3)))
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        chunk = text[m.end() : end].strip("\n")
        if (family, n) in out and not m.group(1):
            continue  # a restated request after its response: keep the response
        if (family, n) in out and out[(family, n)].get("_response"):
            continue
        out[(family, n)] = {"kind": kind_of(chunk) if kind_of else None, "text": chunk, "_response": bool(m.group(1))}
    for r in out.values():
        r.pop("_response", None)
    return out


def read(path: Path, kind_of: Callable[[str], str | None] | None) -> Responses:
    if path.suffix.lower() == ".json":
        return _from_json(json.loads(path.read_text(encoding="utf-8")))
    if path.suffix.lower() == ".docx":
        from ...matter import word

        return split(word.record_docx_text(path), kind_of)
    return split(path.read_text(encoding="utf-8"), kind_of)
