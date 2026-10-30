"""The breakdown's workbook: Summary, Discovery (one row per entry, every case
in attorney then case order, all twelve sections each, an empty section as
"None in file"), and Flags. Styled and cleaned as the status list is
(``litigation/book.py``); the run scans the built file with ``gates.leak_scan``
and re-reads it from its bytes (``check``) before it is filed."""

from __future__ import annotations

import collections
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any

from .. import book as lit_book
from . import sections

HEADER = [
    "Responsible (Smokeball)", "Matter #", "Case", "Section", "Item", "Direction", "Party",
    "Served", "Served (source)", "Due", "Due (source)", "Response served", "Response date", "Response (source)",
    "Extension", "Extension (source)", "Notes",
]  # fmt: skip
WIDTHS = [18, 10, 30, 26, 30, 18, 24, 12, 34, 12, 34, 10, 12, 34, 22, 34, 50]
NOT_READ = "No discovery paper was found by name in the file; not read"
DISCOVERY = "Discovery (12 sections)"


def order(ms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(ms, key=lambda m: ((m.get("responsible") or "~").lower(), str(m.get("title") or "").lower()))


def _d(v: Any) -> str:
    if (v or {}).get("unconfirmed"):
        return sections.UNCONFIRMED
    return str((v or {}).get("date") or "")


def _s(v: Any) -> str:
    return lit_book._src(v)


def rows(ms: list[dict[str, Any]]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for m in order(ms):
        base = [m.get("responsible") or "", m.get("number"), m.get("title") or ""]
        if not m.get("read"):
            out.append([*base, "", NOT_READ] + [""] * (len(HEADER) - 5))
            continue
        for key in sections.KEYS:
            got = (m.get("sections") or {}).get(key) or []
            label = sections.LABEL[key]
            if not got:
                out.append([*base, label, sections.NONE_IN_FILE] + [""] * (len(HEADER) - 5))
            for e in got:
                ext = e.get("extension") or {}
                resp = e.get("response") or {}
                out.append(
                    [
                        *base, label, e.get("item"), e.get("direction"), e.get("party"),
                        _d(e.get("served")), _s(e.get("served")), _d(e.get("due")), _s(e.get("due")),
                        resp.get("value") or "", _d(resp), _s(resp),
                        " ".join(x for x in (_d(ext), ext.get("detail") or "") if x), _s(ext), e.get("notes") or "",
                    ]
                )  # fmt: skip
    return out


def build(ms: list[dict[str, Any]], out: Path, as_of: dt.date) -> dict[str, Any]:
    from openpyxl import Workbook

    body = rows(ms)
    wb = Workbook()
    ws = wb.create_sheet("Summary", 0)
    wb.remove(wb.worksheets[1])  # the default empty sheet
    ws.append(["Section", "Entries", "Cases with an entry", "As of"])
    read = [m for m in ms if m.get("read")]
    for key in sections.KEYS:
        n = [len((m.get("sections") or {}).get(key) or []) for m in read]
        ws.append([sections.LABEL[key], sum(n), sum(1 for x in n if x), as_of.isoformat()])
    ws.append(["Cases read", len(read), "", ""])
    ws.append(["Cases with no discovery paper by name", len(ms) - len(read), "", ""])
    lit_book._fmt(ws, [36, 10, 18, 12])
    lit_book._sheet(wb, DISCOVERY, HEADER, body, WIDTHS)
    flags = [[m.get("number"), m.get("title") or "", f] for m in order(ms) for f in m.get("flags") or []]
    lit_book._sheet(wb, "Flags", ["Matter #", "Case", "Flag"], flags, [10, 34, 120])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"cases": len(ms), "cases_read": len(read), "rows": len(body), "flags": len(flags)}


def check(xlsx: bytes, ms: list[dict[str, Any]]) -> list[str]:
    """The Discovery tab re-read from the file's bytes equals the rows built
    from the data, row for row. Empty means it agrees."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    try:
        got = [[("" if c is None else str(c)) for c in r] for r in list(wb[DISCOVERY].iter_rows(values_only=True))[1:]]
    except KeyError:
        return ["the Discovery tab is missing"]
    finally:
        wb.close()
    want = [[("" if c is None else str(lit_book.clean(c))) for c in r] for r in rows(ms)]

    def norm(rs: list[list[str]]) -> collections.Counter[tuple[str, ...]]:
        return collections.Counter(tuple(c.strip() for c in r) for r in rs)

    if len(got) != len(want):
        return [f"the Discovery tab has {len(got)} rows in the file and {len(want)} in the data"]
    return [] if norm(got) == norm(want) else ["the Discovery tab's rows differ from the data they were built from"]


def for_model(result: dict[str, Any], ctx: Any) -> str:
    """The result with each source shown as the doc number the reader can open."""

    def conv(x: Any) -> Any:
        if isinstance(x, dict):
            out = {k: conv(v) for k, v in x.items() if k != "source"}
            src = x.get("source")
            if isinstance(src, dict):
                out["doc"] = ctx.by_id.get(str(src.get("file_id")))
            return out
        return [conv(v) for v in x] if isinstance(x, list) else x

    return json.dumps(conv(result), indent=1)
