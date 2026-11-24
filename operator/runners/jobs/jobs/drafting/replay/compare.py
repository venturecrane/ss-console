"""The answer key against what a stage wrote. Pure: no file, no import of a stage.

Each function returns a :class:`Section`: rows for the printed table, the
mismatches (each one line, every one of them a reason the replay exits
nonzero), and notes (what was reported but not compared).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .key import ITEM_LISTS, Key, KeyItem, KeySet, number

#: How much of a sliced request the key's ``request_head`` holds.
HEAD = 80
_WS = re.compile(r"\s+")
#: A subpart marker at the start of a line or after whitespace: (a), (b) ...
_SUBPART = re.compile(r"(?:^|\s)\(([a-z])\)")


@dataclass
class Section:
    name: str
    status: str = "ok"
    rows: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def finish(self) -> "Section":
        if self.mismatches and self.status == "ok":
            self.status = "mismatch"
        return self

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "rows": self.rows,
            "mismatches": self.mismatches,
            "notes": self.notes,
        }


def norm(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def head(text: str) -> str:
    """The first ``HEAD`` characters, trimmed AFTER the cut (a cut landing on a
    space must not make two equal heads differ) and case-folded (a key copied
    from a paper's capitals against the sliced text)."""
    return norm(text)[:HEAD].strip().casefold()


def served_sets(served: Any) -> list[dict[str, Any]]:
    sets = served.get("sets") if isinstance(served, dict) else None
    return [s for s in sets if isinstance(s, dict)] if isinstance(sets, list) else []


def _set_key(s: dict[str, Any]) -> tuple[str, int]:
    n = s.get("set_number")
    return str(s.get("set_type") or s.get("type") or ""), n if isinstance(n, int) else -1


def _sha_of(s: dict[str, Any]) -> str:
    return str(s.get("sha256") or s.get("file_sha256") or "").lower()


def _set_row(want: KeySet, got: dict[str, Any] | None, sec: Section) -> None:
    label = f"{want.type} set {want.set_number}"
    if got is None:
        sec.rows.append({"set": label, "found": "no", "count_key": want.request_count, "count_found": None})
        sec.mismatches.append(f"{label}: missed (in the key, not found)")
        return
    count = got.get("request_count")
    sec.rows.append({"set": label, "found": "yes", "count_key": want.request_count, "count_found": count})
    if want.request_count is not None and count != want.request_count:
        sec.mismatches.append(f"{label}: request count {count} found, {want.request_count} in the key")
    got_sha = _sha_of(got)
    if want.sha256 and got_sha and got_sha != want.sha256:
        sec.mismatches.append(f"{label}: file {got_sha[:12]} found, {want.sha256[:12]} in the key")


def _item_rows(
    kind: str, want_set: KeySet | None, items: list[KeyItem], slices: list[str] | None, sec: Section
) -> None:
    if not items:
        return
    if want_set is None:
        sec.notes.append(f"{kind}: the key lists requests but no {ITEM_LISTS[kind]} set")
        return
    label = f"{want_set.type} set {want_set.set_number}"
    if slices is None:
        sec.mismatches.append(f"{label}: no sliced requests to compare ({len(items)} request heads in the key)")
        return
    for it in items:
        got = head(slices[it.n - 1]) if 1 <= it.n <= len(slices) else None
        want = head(it.request_head)
        ok = got is not None and (not want or got == want)
        sec.rows.append({"set": label, "n": it.n, "request_head_equal": ok})
        if not ok:
            sec.mismatches.append(f"{label} no. {it.n}: sliced request {'missing' if got is None else 'differs'}")


def find(key: Key, served: Any, slices: dict[tuple[str, int], list[str] | None]) -> Section:
    """Sets found against the key's served sets, counts, and sliced requests."""
    sec = Section("found vs key")
    got = {_set_key(s): s for s in served_sets(served)}
    for want in key.served_sets:
        _set_row(want, got.get((want.type, want.set_number)), sec)
    wanted = {(w.type, w.set_number) for w in key.served_sets}
    for k in sorted(set(got) - wanted):
        sec.rows.append({"set": f"{k[0]} set {k[1]}", "found": "extra", "count_key": None, "count_found": None})
        sec.mismatches.append(f"{k[0]} set {k[1]}: extra (found, not in the key)")
    for kind, set_type in ITEM_LISTS.items():
        of_type = sorted((w for w in key.served_sets if w.type == set_type), key=lambda w: w.set_number)
        want_set = of_type[0] if of_type else None
        found = want_set is not None and (want_set.type, want_set.set_number) in got
        sl = slices.get((want_set.type, want_set.set_number)) if want_set is not None and found else None
        if want_set is not None and not found:
            continue  # already a miss above; no second line per request
        _item_rows(kind, want_set, key.items[kind], sl, sec)
    return sec.finish()


def _num(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def categories(scope: Any) -> Section:
    """Files, pages, scan pages and characters per category; the share in other."""
    sec = Section("categories")
    cats = scope.get("categories") if isinstance(scope, dict) else None
    if not isinstance(cats, dict):
        sec.status = "error"
        sec.mismatches.append("the scope result has no categories")
        return sec
    total = sum(_num(c.get("chars")) for c in cats.values() if isinstance(c, dict)) or 0
    for name, c in sorted(cats.items()):
        c = c if isinstance(c, dict) else {}
        chars = _num(c.get("chars"))
        sec.rows.append(
            {
                "category": name,
                "files": _num(c.get("files")),
                "pages": _num(c.get("pages")),
                "scan_pages": _num(c.get("scan_pages")),
                "chars": chars,
                "share": round(chars / total, 3) if total else 0.0,
            }
        )
    other = next((r["share"] for r in sec.rows if r["category"] == "other"), 0.0)
    sec.notes.append(f"share of characters in other: {other:.1%}")
    return sec.finish()


def packs(scope: Any) -> Section:
    """Characters per unit's pack and what each left out."""
    sec = Section("packs")
    ps = scope.get("packs") if isinstance(scope, dict) else None
    if not isinstance(ps, dict):
        sec.notes.append("the scope result names no packs")
        return sec.finish()
    for unit, p in sorted(ps.items()):
        p = p if isinstance(p, dict) else {}
        left = p.get("left_out")
        left_n = len(left) if isinstance(left, list) else _num(left)
        sec.rows.append({"unit": unit, "chars": _num(p.get("chars")), "cap": p.get("cap"), "left_out": left_n})
    return sec.finish()


def cost(projection: Any) -> Section:
    """Projected dollars and minutes, each line item; reported, not compared."""
    sec = Section("cost")
    if not isinstance(projection, dict):
        sec.status = "error"
        sec.mismatches.append("the cost projection is not an object")
        return sec
    for line in projection.get("lines") or []:
        if isinstance(line, dict):
            sec.rows.append({"item": line.get("item"), "dollars": line.get("dollars"), "minutes": line.get("minutes")})
            if "stored_pages" in line:
                sec.notes.append(f"{line['stored_pages']} scanned page(s) already in the seat's page store (not paid)")
    sec.rows.append({"item": "TOTAL", "dollars": projection.get("dollars"), "minutes": projection.get("minutes")})
    return sec.finish()


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{2,5}$", "", norm(name)).casefold()


def exhibits(key: Key, placed: Any, listed: list[dict[str, Any]] | None) -> Section:
    """The key's Index of Exhibits against code's: per file, its exhibit (the
    plan, ``exhibit_plan.place``) and, with a production response, the request
    numbers its line lists (``exhibit_index``). Files compare by name without
    extension. Bar (plan A3): extra 0; every miss is listed with code's reason."""
    sec = Section("exhibits vs key")
    if not key.exhibits:
        sec.notes.append("the key has no exhibits; nothing compared")
        return sec.finish()
    plan = placed if isinstance(placed, dict) else {}
    where = {_stem(f["name"]): e["letter"] for e in plan.get("exhibits") or [] for f in e.get("files") or []}
    why = {_stem(f["name"]): str(f.get("reason") or "") for f in plan.get("left_out") or []}
    lines = {(e["letter"], _stem(n)): list(e["requests"]) for e in listed or [] for n in e["files"]}
    wanted = {(k.letter, _stem(k.name)) for k in key.exhibits}
    for k in key.exhibits:
        s = _stem(k.name)
        got = where.get(s)
        row: dict[str, Any] = {"file": k.name, "key": k.letter, "code": got or "-"}
        if got is None:
            sec.mismatches.append(
                f"{k.name}: missed (key Exhibit {k.letter}; code left it out: {why.get(s) or 'not in the file'})"
            )
        elif got != k.letter:
            sec.mismatches.append(f"{k.name}: code placed it in Exhibit {got}, the key in Exhibit {k.letter}")
        if listed is not None and k.requests is not None and got == k.letter:
            nums = lines.get((k.letter, s))
            row.update(requests_key=list(k.requests), requests_code=nums)
            if nums is None or sorted(nums) != sorted(k.requests):
                sec.mismatches.append(f"{k.name}: Request Nos. {nums} in code's index, {list(k.requests)} in the key")
        sec.rows.append(row)
    extra = sorted(lines) if listed is not None else sorted((x, s) for s, x in where.items())
    for letter, s in extra:
        if (letter, s) not in wanted and s not in {_stem(k.name) for k in key.exhibits if k.letter != letter}:
            sec.rows.append({"file": s, "key": "-", "code": letter})
            sec.mismatches.append(f"{s}: extra (code placed it in Exhibit {letter}; not in the key's index)")
    if listed is None:
        sec.notes.append("no production response given (--production): request numbers not compared")
    return sec.finish()


def form_read(label: str, key_checked: list[str] | None, read: Any) -> Section:
    """The pixel read's checked numbers against the key's."""
    sec = Section(f"form pixel read ({label})")
    got_raw = read.get("checked") if isinstance(read, dict) else None
    if not isinstance(got_raw, list):
        sec.status = "error"
        sec.mismatches.append("the pixel read returned no checked list")
        return sec
    got = {number(x) for x in got_raw}
    unreg = sorted({number(x) for x in (read.get("unregistered") or [])}) if isinstance(read, dict) else []
    if key_checked is None:
        sec.notes.append(f"the key has no form.checked; {len(got)} numbers read")
        return sec.finish()
    want = set(key_checked)
    sec.rows.append(
        {
            "agreeing": len(got & want),
            "only_read": sorted(got - want, key=_sort_num),
            "only_key": sorted(want - got, key=_sort_num),
            "unregistered": unreg,
        }
    )
    for n in sorted(got ^ want, key=_sort_num):
        sec.mismatches.append(f"form {n}: {'read checked, not in the key' if n in got else 'in the key, not read'}")
    for n in unreg:
        sec.mismatches.append(f"form {n}: read checked but not in the form's catalog")
    return sec.finish()


def _sort_num(n: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in n.split("."))


def _layout(n: str, text: str, names: list[str]) -> list[str]:
    out: list[str] = []
    low_names = [norm(x).lower() for x in names]
    for line in text.splitlines():
        marks = _SUBPART.findall(line)
        if len(set(marks)) > 1:
            out.append(f"form {n}: subparts ({', '.join(marks)}) share one line")
        low = norm(line).lower()
        on_line = [x for x in low_names if x and x in low]
        if len(on_line) > 1:
            out.append(f"form {n}: {len(on_line)} providers on one line")
    return out


def body(key: Key, responses: dict[tuple[str, str], dict[str, Any]]) -> Section:
    """A produced body's per-number kinds, names and layout against the key.

    ``responses``: (``form`` | ``special`` | ``production``, number) ->
    {"kind": str | None, "text": str}."""
    sec = Section("produced body")
    for n, want in sorted(key.kinds.items(), key=lambda kv: _sort_num(kv[0])):
        r = responses.get(("form", n))
        _kind_row(sec, f"form {n}", want, r)
        if r is not None:
            low = norm(str(r.get("text") or "")).lower()
            for name in key.names.get(n, []):
                if norm(name).lower() not in low:
                    sec.mismatches.append(f"form {n}: name not in the response ({len(name)} chars)")
            sec.mismatches.extend(_layout(n, str(r.get("text") or ""), key.names.get(n, [])))
    for kind in ITEM_LISTS:
        for it in key.items[kind]:
            if it.kind:
                _kind_row(sec, f"{kind} {it.n}", it.kind, responses.get((kind, str(it.n))))
    return sec.finish()


def _kind_row(sec: Section, label: str, want: str, r: dict[str, Any] | None) -> None:
    got = None if r is None else r.get("kind")
    sec.rows.append({"response": label, "kind_key": want, "kind_body": got})
    if r is None:
        sec.mismatches.append(f"{label}: no response in the body")
    elif got is None:
        sec.mismatches.append(f"{label}: the response's kind could not be read")
    elif got != want:
        sec.mismatches.append(f"{label}: kind {got}, {want} in the key")
