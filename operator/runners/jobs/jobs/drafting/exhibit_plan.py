"""Which exhibit each pulled file goes in, for a production response, by code.

WHY (2026-11-23, plan A3). The firm's prompt asked the model to write the
Index of Exhibits after the last response, and SMD rebuilt it by hand on every
request: 18 index lines across last week's hand finals. Where a file goes is a
rule the firm states once (its Rules 8, 9 and 12: Exhibit A medical records, B
medical bills, C liens, D incident documents, E wage loss; no correspondence
exhibit; never a letter to or from an insurer), so code places it, and the
model only says which exhibit a response produces from.

The firm authors the table inside ``response_scope`` (``drafting-firm.yaml``),
checked by :func:`validate` from ``response_scope.validate``; a firm that
authored none gets no plan, and its production responses are drafted exactly
as before (dark)::

    response_scope:
      exhibits:
        letters:
          - {letter: A, title: MEDICAL RECORDS, categories: [medical_records],
             folders: [<regex>], names: [<regex>]}      # one or more of the three
        never:                                          # optional
          - {reason: an insurance letter, names: [<regex>], folders: [...], categories: [...]}

Placement, per file of the scope step's record (``scope.json``), in order:

1. A file the privilege wall holds back (``walled.json``) is never placed and
   never named: it is counted.
2. A ``never`` entry whose names, folders or categories match leaves the file
   out with that entry's reason.
3. The first exhibit, in table order, whose name or folder patterns match it.
4. Else the first exhibit whose categories hold the file's category.
5. Else it is left out: no exhibit takes files of its category.

Nothing here reads a page or calls a model. ``exhibits.json`` in the job's data
directory is the plan; ``exhibit_index`` reads it after the gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILE = "exhibits.json"
KEY = "exhibits"
#: The set type whose responses produce documents, and so carry an index.
PRODUCTION = "requests_for_production"
_LETTER = re.compile(r"^[A-Z]$")
_MATCH_KEYS = ("categories", "folders", "names")


@dataclass(frozen=True)
class Exhibit:
    letter: str
    title: str
    categories: tuple[str, ...] = ()
    folders: tuple[re.Pattern[str], ...] = ()
    names: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class Never:
    reason: str
    categories: tuple[str, ...] = ()
    folders: tuple[re.Pattern[str], ...] = ()
    names: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class Table:
    exhibits: tuple[Exhibit, ...]
    never: tuple[Never, ...] = ()

    def by_letter(self) -> dict[str, Exhibit]:
        return {e.letter: e for e in self.exhibits}


# ---- the firm input -----------------------------------------------------------------
def _matchers(where: str, raw: dict[str, Any], categories: tuple[str, ...]) -> list[str]:
    out = []
    for k in _MATCH_KEYS:
        if k not in raw:
            continue
        v = raw[k]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
            out.append(f"{where}.{k}: expected a list of one or more strings")
            continue
        if k == "categories":
            out += [f"{where}.categories: {c!r} is not one of {list(categories)}" for c in v if c not in categories]
            continue
        for p in v:
            try:
                re.compile(p)
            except re.error as exc:
                out.append(f"{where}.{k}: invalid regex {p!r} ({exc})")
    if not any(k in raw for k in _MATCH_KEYS):
        out.append(f"{where}: needs one or more of {list(_MATCH_KEYS)}")
    return out


def validate(body: object, categories: tuple[str, ...], where: str = f"response_scope.{KEY}") -> list[str]:
    """The table's closed shape: ``{letters: [...], never: [...]}`` (``never`` optional)."""
    if not isinstance(body, dict) or "letters" not in body or not set(body) <= {"letters", "never"}:
        return [f"{where}: expected {{letters: [...], never: [...]}} (never optional)"]
    out: list[str] = []
    letters = body["letters"]
    if not isinstance(letters, list) or not letters:
        return [f"{where}.letters: expected a list of one or more exhibits"]
    seen: set[str] = set()
    for i, e in enumerate(letters):
        w = f"{where}.letters[{i}]"
        if (
            not isinstance(e, dict)
            or not {"letter", "title"} <= set(e)
            or not set(e) <= {"letter", "title", *_MATCH_KEYS}
        ):
            out.append(f"{w}: expected {{letter, title}} and one or more of {list(_MATCH_KEYS)}")
            continue
        if not isinstance(e["letter"], str) or not _LETTER.match(e["letter"]) or e["letter"] in seen:
            out.append(f"{w}.letter: one capital letter, each used once")
        seen.add(str(e["letter"]))
        if not isinstance(e["title"], str) or not e["title"].strip():
            out.append(f"{w}.title: the exhibit's title, as the index prints it")
        out += _matchers(w, e, categories)
    never = body.get("never", [])
    if not isinstance(never, list):
        return [*out, f"{where}.never: expected a list"]
    for i, n in enumerate(never):
        w = f"{where}.never[{i}]"
        if not isinstance(n, dict) or "reason" not in n or not set(n) <= {"reason", *_MATCH_KEYS}:
            out.append(f"{w}: expected {{reason}} and one or more of {list(_MATCH_KEYS)}")
            continue
        if not isinstance(n["reason"], str) or not n["reason"].strip():
            out.append(f"{w}.reason: why a file it matches is never an exhibit, as the notes say it")
        out += _matchers(w, n, categories)
    return out


def _pats(raw: dict[str, Any], k: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.I) for p in raw.get(k) or ())


def table(firm_data: dict[str, Any] | None) -> Table | None:
    """The firm's exhibit table; None when it authored none (or it does not validate)."""
    from .response_scope import CATEGORIES, KEY as SCOPE

    scope = (firm_data or {}).get(SCOPE)
    body = scope.get(KEY) if isinstance(scope, dict) else None
    if body is None or validate(body, CATEGORIES):
        return None
    ex = tuple(
        Exhibit(e["letter"], " ".join(e["title"].split()).upper(), tuple(e.get("categories") or ()),
                _pats(e, "folders"), _pats(e, "names"))
        for e in body["letters"]
    )  # fmt: skip
    nv = tuple(
        Never(" ".join(n["reason"].split()), tuple(n.get("categories") or ()), _pats(n, "folders"), _pats(n, "names"))
        for n in body.get("never") or ()
    )
    return Table(ex, nv)


# ---- placement ----------------------------------------------------------------------
def _hits(m: Exhibit | Never, f: dict[str, Any], by_category: bool) -> bool:
    name, folder = str(f.get("name") or ""), str(f.get("folder") or "")
    if any(p.search(name) for p in m.names) or any(p.search(folder) for p in m.folders):
        return True
    return by_category and str(f.get("category") or "") in m.categories


def place(files: list[dict[str, Any]], t: Table, walled: set[str]) -> dict[str, Any]:
    """Every file in one exhibit or left out with a reason (the module's order)."""
    placed: dict[str, list[dict[str, Any]]] = {e.letter: [] for e in t.exhibits}
    left: list[dict[str, Any]] = []
    held = 0
    for f in files:
        if str(f.get("id")) in walled:
            held += 1
            continue
        row = {k: f.get(k) for k in ("id", "name", "folder", "category")}
        never = next((n for n in t.never if _hits(n, f, True)), None)
        if never is not None:
            left.append({**row, "reason": never.reason})
            continue
        home = next((e for e in t.exhibits if _hits(e, f, False)), None) or next(
            (e for e in t.exhibits if _hits(e, f, True)), None
        )
        if home is None:
            left.append(
                {**row, "reason": f"no exhibit takes {str(f.get('category') or 'other').replace('_', ' ')} files"}
            )
        else:
            placed[home.letter].append(row)
    return {
        "exhibits": [{"letter": e.letter, "title": e.title, "files": placed[e.letter]} for e in t.exhibits],
        "left_out": left,
        "walled": held,
    }


def walled_ids(data: Path) -> set[str]:
    p = Path(data) / "walled.json"
    return {str(w.get("id")) for w in json.loads(p.read_text(encoding="utf-8"))} if p.is_file() else set()


def run(data: Path, firm_data: dict[str, Any] | None, plan: list[dict[str, Any]]) -> dict[str, Any] | None:
    """``exhibits.json`` for a job with a production unit, from the scope step's
    record; None (and no file) when no unit produces or the firm authored no table."""
    p = Path(data) / FILE
    t = table(firm_data)
    if t is None or not any(u.get("variant") == PRODUCTION for u in plan):
        p.unlink(missing_ok=True)
        return None
    from .response_scope import SCOPE

    files = json.loads((Path(data) / SCOPE).read_text(encoding="utf-8"))["files"]
    rec = place(files, t, walled_ids(data))
    tmp = p.with_name(f".{FILE}.tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(p)
    return rec


def load(data: Path) -> dict[str, Any] | None:
    """The job's exhibit plan as it stands now, or None when it has none. The
    privilege wall moves printed emails out after transcription, later than the
    plan is made: a file walled since leaves its exhibit here and is counted."""
    p = Path(data) / FILE
    if not p.is_file():
        return None
    rec = json.loads(p.read_text(encoding="utf-8"))
    walled = walled_ids(data)
    for e in rec["exhibits"]:
        keep = [f for f in e["files"] if str(f.get("id")) not in walled]
        rec["walled"] = int(rec.get("walled") or 0) + len(e["files"]) - len(keep)
        e["files"] = keep
    left = [f for f in rec["left_out"] if str(f.get("id")) not in walled]
    rec["walled"] = int(rec.get("walled") or 0) + len(rec["left_out"]) - len(left)
    rec["left_out"] = left
    return rec


def letter_of(rec: dict[str, Any]) -> dict[str, str]:
    """File id -> its exhibit's letter, for the inventory column."""
    return {str(f["id"]): e["letter"] for e in rec["exhibits"] for f in e["files"]}
