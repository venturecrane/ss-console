"""The attorney's request as a checklist: one item per change he asked for.

A model reads the request and the brief's section titles and returns JSON;
code decides whether that JSON is a checklist. An item is kept only when:

* its ``kind`` is one of ``KINDS`` and its ``params`` fit that kind's shape;
* its ``quote`` is the attorney's own words, found in the request text (as
  words: case and spacing aside), so no item exists he did not ask for;
* its ``target`` names exactly one section of the brief, or ``document``
  (every section), or, for a formatting item, the whole file.

A checklist that fails any of that is asked for once more, told exactly what
failed; failing again, the job stops (``revise_unchecked``) for SMD. Nothing
here edits the brief.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..verify.items import parse_json
from . import sections as sec

KINDS = ("remove_citations", "shorten_section", "restructure_section", "formatting", "replace_text", "other")
#: The kinds of citation a ``remove_citations`` item may name (``checks.CITES``).
CITE_KINDS = ("record", "deposition", "case", "statute", "all")
#: The renderer's firm style keys a formatting item may set
#: (``drafting-firm.yaml`` ``format.layout.<class>``, brief-format's schema).
LAYOUT_KEYS = {
    "line_spacing": (float, 1.0, 3.0),
    "heading_line_spacing": (float, 1.0, 3.0),
    # Exact points; each wins over its multiple (a body at exact 24 pt is a
    # 12 pt brief double spaced; its headings then take 24 pt too).
    "line_spacing_pt": (float, 6.0, 72.0),
    "heading_line_spacing_pt": (float, 6.0, 72.0),
    "heading_space_before_pt": (float, 0.0, 48.0),
    "heading_space_after_pt": (float, 0.0, 48.0),
    "table_font_pt": (float, 8.0, 20.0),
    "table_border_pt": (float, 0.0, 6.0),
    "table_header_fill": (str, None, None),
    "table_total_fill": (str, None, None),
}
_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")
MAX_ITEMS = 40


@dataclass
class Item:
    id: str
    quote: str
    kind: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChecklistError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:600])
        self.problems = problems


def _words(s: str) -> str:
    """Words only: quotes, dashes and spacing differ between a mail client and
    a model's copy; the words do not."""
    return " ".join(re.findall(r"\w+", str(s or "").lower()))


def _quoted(quote: str, request_text: str) -> bool:
    q = _words(quote)
    return bool(q) and f" {q} " in f" {_words(request_text)} "


def _int(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _strs(v: Any, n: int = 30) -> bool:
    return isinstance(v, list) and len(v) <= n and all(isinstance(x, str) and x.strip() for x in v)


def _layout_problems(layout: Any) -> list[str]:
    if not isinstance(layout, dict) or not layout:
        return ["a formatting item carries params.layout, a non-empty object"]
    out = []
    for k, v in layout.items():
        spec = LAYOUT_KEYS.get(k)
        if spec is None:
            out.append(f"layout key {k!r} is not one of {sorted(LAYOUT_KEYS)}")
            continue
        typ, lo, hi = spec
        if typ is str:
            if not (isinstance(v, str) and _HEX.match(v)):
                out.append(f"layout {k} is a six-digit RRGGBB colour")
        elif not (isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= float(v) <= hi):
            out.append(f"layout {k} is a number from {lo} to {hi}")
    return out


def _param_problems(kind: str, p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("must_include", "must_exclude"):
        if key in p and not _strs(p[key]):
            out.append(f"params.{key} is a list of phrases")
    if kind == "remove_citations" and p.get("cite_kind") not in CITE_KINDS:
        out.append(f"remove_citations names params.cite_kind, one of {list(CITE_KINDS)}")
    elif kind == "shorten_section":
        lims = {k: p.get(k) for k in ("max_paragraphs", "max_sentences", "max_words") if k in p}
        ratio = p.get("max_ratio")
        if not lims and ratio is None:
            out.append("shorten_section names a limit: max_paragraphs, max_sentences, max_words or max_ratio")
        for k, v in lims.items():
            if not _int(v, 1, 5000):
                out.append(f"params.{k} is a whole number")
        if ratio is not None and not (isinstance(ratio, (int, float)) and 0.05 <= float(ratio) < 1.0):
            out.append("params.max_ratio is a fraction under 1")
    elif kind == "restructure_section":
        if not any(k in p for k in ("subheadings", "into_list", "move_before", "move_after")):
            out.append("restructure_section names subheadings, into_list, move_before or move_after")
        if "subheadings" in p and not _strs(p["subheadings"]):
            out.append("params.subheadings is a list of heading titles")
        if "into_list" in p and not isinstance(p["into_list"], bool):
            out.append("params.into_list is true or false")
        for k in ("move_before", "move_after"):
            if k in p and not (isinstance(p[k], str) and p[k].strip()):
                out.append(f"params.{k} names a section")
    elif kind == "formatting":
        out += _layout_problems(p.get("layout"))
    elif kind == "replace_text":
        if not (isinstance(p.get("old"), str) and p["old"].strip()):
            out.append("replace_text names params.old, the words to replace")
        if not isinstance(p.get("new"), str):
            out.append("replace_text names params.new (may be empty to delete)")
    return out


def validate(raw: Any, request_text: str, brief: list[sec.Section]) -> list[Item]:
    """The checklist, or ``ChecklistError`` naming every problem."""
    got = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(got, list) or not got:
        raise ChecklistError(["the answer is an object with a non-empty items list"])
    if len(got) > MAX_ITEMS:
        raise ChecklistError([f"at most {MAX_ITEMS} items"])
    problems: list[str] = []
    items: list[Item] = []
    for n, it in enumerate(got, 1):
        if not isinstance(it, dict):
            problems.append(f"item {n} is an object")
            continue
        iid = str(it.get("id") or f"c{n}").strip()[:16] or f"c{n}"
        kind, quote = it.get("kind"), it.get("quote")
        target = str(it.get("target") or "").strip()
        raw_params = it.get("params")
        params: dict[str, Any] = dict(raw_params) if isinstance(raw_params, dict) else {}
        where = f"item {iid}"
        if kind not in KINDS:
            problems.append(f"{where}: kind is one of {list(KINDS)}")
            continue
        if not (isinstance(quote, str) and _quoted(quote, request_text)):
            problems.append(f"{where}: quote is the attorney's own words, copied from the request")
        if kind == "formatting":
            target = target or sec.DOCUMENT
        elif not sec.find(brief, target):
            problems.append(f"{where}: target {target!r} names no single section of the brief (or 'document')")
        problems += [f"{where}: {p}" for p in _param_problems(str(kind), params)]
        items.append(Item(iid, str(quote or ""), str(kind), target, dict(params)))
    if len({i.id for i in items}) != len(items):
        problems.append("item ids are unique")
    if problems:
        raise ChecklistError(problems)
    return items


SYSTEM = (
    "You turn an attorney's email asking for changes to a filed brief into a checklist, one item per change he "
    'asks for. Return ONLY a JSON object {"items": [...]}. Each item: {"id": "c1", "quote": <his exact '
    'words for this change, copied from the email>, "kind": one of '
    + json.dumps(list(KINDS))
    + ', "target": <a section title exactly as listed, or "document">, "params": {...}}.\n'
    'params by kind: remove_citations {"cite_kind": one of '
    + json.dumps(list(CITE_KINDS))
    + '}; shorten_section {"max_paragraphs"|"max_sentences"|"max_words": int, or "max_ratio": fraction}; '
    'restructure_section {"subheadings": [titles in order] | "into_list": true | "move_before"|"move_after": '
    'section title}; formatting {"layout": {keys among '
    + json.dumps(sorted(LAYOUT_KEYS))
    + '}} (line_spacing 2.0 is double spacing; fills are RRGGBB); replace_text {"old": words in the brief, '
    '"new": his replacement}; other {}. Any item may add "must_include" / "must_exclude": [phrases he '
    "states]. Never invent a change he did not ask for, a number he did not give, or new facts; when he asks for "
    "something the brief would need new facts for, still make the item (kind other) with his words."
)


def user(request_text: str, brief: list[sec.Section], problems: list[str] | None = None) -> str:
    titles = "\n".join(f"- {s.title}" for s in brief if s.heading)
    out = f"THE ATTORNEY'S EMAIL:\n{request_text}\n\nTHE BRIEF'S SECTION TITLES:\n{titles}\n"
    if problems:
        out += "\nYOUR LAST CHECKLIST WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {p}" for p in problems)
    return out


def parse(text: str) -> Any:
    return parse_json(text)
