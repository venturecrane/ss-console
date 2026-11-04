"""`pack`: one reading pack per matter, the per-matter half of every read.

Ported from the file-review toolset's pack builder (rule for rule; the
patterns and budgets are the firm's, ``firm.pack``):

* the matter header, the layouts as entered (values with nothing entered left
  out, the Medicals tab and insurance items labelled), every open task with its
  id and dates, every completed task with its completion and note, calendar
  events and memos;
* the document listing: every live document with its ``[doc N]`` number (the
  number the read's tools take), folder, name, saved date and what this run
  could do with it (text, a scan with no text layer, not pulled, an extraction
  error, or the same content as another document);
* a disc dump (a folder of more than 60 entries, at least 70 percent of them
  viewer files) is one line with its counts; its text-type files are still
  listed and read;
* the text of the readable documents by priority (correspondence and
  insurance, then bills, then email, then the rest, then medical records cut to
  a head), each under its type's budget, scaled down together to fit the pack
  budget, and past the hard cap NAMED, so the reader knows to open it.

Two economies from the lanes before this one:

* **Duplicates are read once.** A document whose content (sha256) another
  document on the matter already carries is listed as "same content as
  [doc N]" and its text is not packed twice.
* **Reuse the paid read.** With a prior delivered record (``state.py``) the
  pack is proportional to the change: the header, that record (its document
  ids as ``[doc N]``), every open task, and only the completed tasks, events
  and memos dated on or after the record's delivery; the listing names only
  the documents new or changed since it and the documents the record cites,
  and carries the text of only the new or changed ones. Every other document
  is one ``list_files`` / ``fetch_doc`` away. The layouts stay whole: they are
  a few thousand characters on the largest matter, and a Medicals row or a
  closure is judged against them.

A listing row is ``[doc N] | folder | name | date | state``: no document id.
The model cites ``[doc N]``; code turns it back into the id
(``recheck.resolve``), so the 36-character id is never paid for per row.

Scanned bills are not transcribed in this job (a predictable price): a scan's
pages carry ``SCAN_MARK`` and the listing says so.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..litigation import fetch as fetch_mod
from ..litigation.manifest import file_date, full_name
from .recheck import UUID, to_refs

#: What extract writes for a page with no text layer (``run.no_ocr``). Never
#: written to the shared text cache (``run.CasefileCache``).
SCAN_MARK = "[scanned page, no text layer: not read in this job]"
TEXT_TYPES = (".pdf", ".docx", ".doc", ".msg", ".rtf", ".txt", ".xlsx", ".xls", ".eml", ".csv")
DUMP_MIN, DUMP_SHARE = 60, 0.7
FOLDER_LINES_CAP = 40_000
HARD_CAP_RATIO = 1.25
SCAN_FLOOR = 200
_PAGE = re.compile(r"===== page \d+ =====")


def _ext(f: dict[str, Any]) -> str:
    e = str(f.get("ext") or "").lower()
    return e if not e or e.startswith(".") else "." + e


def folder_paths(inv: dict[str, Any]) -> dict[str, str]:
    return {str(f.get("id")): str(f.get("path") or "/") for f in inv.get("folders") or [] if isinstance(f, dict)}


def rows_of(inv: dict[str, Any]) -> list[dict[str, Any]]:
    paths = folder_paths(inv)
    out = []
    for f in inv.get("files") or []:
        if not f.get("id") or f.get("deleted"):
            continue
        out.append(
            {
                "id": str(f["id"]),
                "name": full_name(f),
                "folder": paths.get(str(f.get("folderId") or ""), "/"),
                "date": file_date(f),
                "ext": _ext(f),
                "size": int(f.get("size") or 0),
            }
        )
    out.sort(key=lambda r: (r["folder"], r["date"], r["name"]))
    return out


def dump_folders(rows: list[dict[str, Any]]) -> set[str]:
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["folder"], []).append(r)
    return {
        f
        for f, items in by.items()
        if len(items) > DUMP_MIN and sum(1 for r in items if r["ext"] not in TEXT_TYPES) / len(items) >= DUMP_SHARE
    }


def _matches(rx: list[re.Pattern[str]], *texts: str) -> bool:
    return any(p.search(t) for p in rx for t in texts)


def priority(r: dict[str, Any], firm: Any) -> int:
    corr, bill = firm.rx(firm.pack("correspondence_patterns")), firm.rx(firm.pack("bill_patterns"))
    if _matches(corr, r["name"], r["folder"]):
        return 0
    if _matches(bill, r["name"]):
        return 1
    if r["ext"] in (".msg", ".eml"):
        return 2
    if _matches(firm.rx(firm.pack("record_folder_patterns")), r["folder"]):
        return 4
    return 3


def budget_for(r: dict[str, Any], firm: Any) -> int:
    b = firm.pack("doc_budgets")
    if r["ext"] in (".msg", ".eml"):
        return int(b["email"])
    if _matches(firm.rx(firm.pack("bill_patterns")), r["name"]):
        return int(b["bill"])
    record = _matches(firm.rx(firm.pack("record_folder_patterns")), r["folder"])
    if record and not _matches(firm.rx(firm.pack("correspondence_patterns")), r["name"]):
        return int(b["record"])
    return int(b["other"])


def fetch_ids(inv: dict[str, Any], firm: Any, changed: set[str] | None) -> list[str]:
    """The documents this run downloads and extracts: every live text-type
    document outside a disc dump's viewer files (with a prior record, only the
    new or changed ones), most useful first, at most ``fetch_cap``."""
    rows = rows_of(inv)
    dumps = dump_folders(rows)
    want = [r for r in rows if r["ext"] in TEXT_TYPES and not (r["folder"] in dumps and r["ext"] not in TEXT_TYPES)]
    if changed is not None:
        want = [r for r in want if r["id"] in changed]
    want.sort(key=lambda r: (priority(r, firm), r["date"]), reverse=False)
    return [r["id"] for r in want[: int(firm.pack("fetch_cap"))]]


def body_chars(text: str) -> int:
    return len(_PAGE.sub("", text.replace(SCAN_MARK, "")).strip())


def doc_state(fid: str, pulled: dict[str, Any], extracted: dict[str, Any], texts: dict[str, str]) -> str:
    row = pulled.get(fid)
    if row and row.get("duplicate_of"):
        return f"duplicate:{row['duplicate_of']}"
    rec = extracted.get(fid)
    if rec is not None and not rec.get("ok"):
        return "error: " + str(rec.get("problem") or "")[:60]
    text = texts.get(fid)
    if text is None:
        return "not pulled" if not row else ("pull failed" if not row.get("ok") else "not extracted")
    scanned = text.count(SCAN_MARK)
    if scanned and body_chars(text) < SCAN_FLOOR:
        return f"SCAN, no text layer ({scanned} pages)"
    return f"text {len(text)} chars" + (f", {scanned} scanned pages not read" if scanned else "")


def is_readable(state: str) -> bool:
    return state.startswith("text ")


def _texts(data: Any, mid: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    out = {}
    for r in rows:
        t = fetch_mod.cached_text(data, mid, r["id"])
        if t is not None:
            out[r["id"]] = t
    return out


def _extracted(data: Any, mid: str) -> dict[str, Any]:
    from ..stages.base import read_jsonl

    return {str(r.get("file_id")): r for r in read_jsonl(fetch_mod.matter_dir(data, mid) / "extracted.jsonl")}


def _header(inv: dict[str, Any], prior: dict[str, Any] | None, by_id: dict[str, int] | None = None) -> list[str]:
    m, d = inv.get("matter") or {}, inv.get("detail") or {}
    mt = (d.get("matterType") or {}).get("name") if isinstance(d.get("matterType"), dict) else d.get("matterType")
    lines = [
        f"# MATTER {m.get('number')}: {d.get('title') or m.get('title') or ''}",
        "",
        f"- matter id: {m.get('id')}",
        f"- clients: {', '.join(c['name'] for c in inv.get('clients') or []) or '(none resolved)'}",
        f"- other side: {', '.join(c['name'] for c in inv.get('other_sides') or []) or '(none entered)'}",
        f"- matter type: {mt} | status: {m.get('status')} | opened: {str(d.get('openedDate') or '')[:10]}",
        f"- description field: {d.get('description')!r}",
        f"- responsible: {m.get('responsible')} | assisting: {m.get('assisting')}",
        "",
    ]
    if prior is not None:
        lines += [
            f"## PRIOR REVIEW RECORD (delivered {prior.get('delivered_at')}; update it, do not start over)",
            "Answer with record_review_patch: only what changed. Documents are cited as [doc N].",
            json.dumps(to_refs(prior.get("review"), by_id or {}), ensure_ascii=False),
            "",
        ]
    return lines


def _layouts(inv: dict[str, Any], firm: Any) -> list[str]:
    out = ["## LAYOUTS (as entered; values with nothing entered are omitted)"]
    design_med = str(firm.get("medicals_layout_design"))
    for key, item in (inv.get("layouts") or {}).items():
        row, vals = item.get("row") or {}, item.get("values") or {}
        pairs = [(k, v) for k, v in vals.items() if v not in (None, "", False, 0, "0", [], {})]
        if not pairs:
            continue
        design = str((row.get("layoutDesign") or {}).get("id") or row.get("layoutDesignId") or "")
        label = "MEDICALS TAB" if design_med in design else design.split("_")[0] or "LAYOUT"
        side = f" [{row.get('parentId')} #{row.get('parentIndex')}]" if row.get("parentId") else ""
        out.append(f"#### {label}{side} (item {key})")
        out += [f"- {k} = {str(v)[:200]}" for k, v in pairs]
    return out if len(out) > 1 else [out[0], "(no layout values entered)"]


def _task_line(t: dict[str, Any], done: bool) -> str:
    parts = [f"- id {t.get('id')} | {t.get('subject')!r}", f"created {str(t.get('createdDate') or '')[:10]}"]
    due = str(t.get("dueDate") or t.get("dueDateOnly") or "")[:10]
    parts.append(f"due {due}" if due else "no due date")
    if done:
        parts.append(f"completed {str(t.get('completedDate') or t.get('lastUpdated') or '')[:10]}")
    note = str(t.get("note") or "")
    if note:
        parts.append("note: " + " ".join(note.split())[:300])
    return " | ".join(parts)


#: The dates that place an item in time, per kind: every one present is
#: compared, so an edit after creation counts as a change.
TASK_DATES = ("completedDate", "lastUpdated", "createdDate", "dueDate", "dueDateOnly")
EVENT_DATES = ("startTime", "start", "lastUpdated", "createdDate")
MEMO_DATES = ("dateCreated", "createdDate", "lastUpdated", "updatedDate")


def changed_since(item: dict[str, Any], keys: tuple[str, ...], since: str | None) -> bool:
    """True with no prior record; else True when any of the item's dates is on
    or after the record's delivery (for an event: still to come), or when it
    carries no date at all (an item that cannot be placed is shown, not hidden)."""
    if since is None:
        return True
    ds = [str(item.get(k) or "")[:10] for k in keys if str(item.get(k) or "")[:10]]
    return not ds or max(ds) >= since


def _activity(inv: dict[str, Any], since: str | None = None) -> list[str]:
    done = [t for t in inv.get("tasks_done") or [] if changed_since(t, TASK_DATES, since)]
    events = [e for e in inv.get("events") or [] if changed_since(e, EVENT_DATES, since)]
    memos = [m for m in inv.get("memos") or [] if changed_since(m, MEMO_DATES, since)]

    def head(name: str, shown: int, total: int) -> str:
        if since is None or shown == total:
            return f"## {name} ({total})"
        return f"## {name} ({shown} of {total}; the rest are unchanged since {since} and the prior record covers them)"

    lines = [f"## OPEN TASKS ({len(inv.get('tasks_open') or [])})"]
    lines += [_task_line(t, False) for t in inv.get("tasks_open") or []]
    lines += ["", head("COMPLETED TASKS", len(done), len(inv.get("tasks_done") or []))]
    lines += [_task_line(t, True) for t in done]
    lines += ["", head("EVENTS", len(events), len(inv.get("events") or []))]
    for e in events:
        start = str(e.get("startTime") or e.get("start") or "")[:10]
        lines.append(f"- {start} | {e.get('subject')!r} | {str(e.get('description') or '')[:120]!r}")
    lines += ["", head("MEMOS", len(memos), len(inv.get("memos") or []))]
    for m in memos:
        when = str(m.get("dateCreated") or m.get("createdDate") or "")[:10]
        lines.append(f"- {when} | {m.get('title')!r} | {' '.join(str(m.get('text') or '').split())[:400]}")
    return [*lines, ""]


def _scale(header_len: int, budgets: dict[str, int], chars: dict[str, int], cap: int) -> float:
    def fit(s: float) -> int:
        return header_len + sum(min(int(budgets[i] * s), chars[i]) for i in budgets)

    if fit(1.0) <= cap:
        return 1.0
    lo, hi = 0.05, 1.0
    for _ in range(30):  # short documents are untouched by the cap, so the fit is not linear
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if fit(mid) > cap else (mid, hi)
    return lo


def build(ctx: Any, inv: dict[str, Any], firm: Any, prior: dict[str, Any] | None, changed: set[str] | None) -> dict:
    """``{"text", "meta", "readable", "packed", "scale"}``; ``meta`` maps every
    listed doc id to ``{n, name, folder, date, state}``."""
    mid = str((inv.get("matter") or {}).get("id"))
    rows = rows_of(inv)
    dumps = dump_folders(rows)
    texts, extracted = _texts(ctx.data, mid, rows), _extracted(ctx.data, mid)
    pulled = fetch_mod.pulled(ctx.data, mid)
    meta: dict[str, Any] = {}
    for r in rows:
        meta[r["id"]] = {**r, "n": ctx.by_id.get(r["id"]), "state": doc_state(r["id"], pulled, extracted, texts)}
    since = (str(prior.get("delivered_at") or "")[:10] or "0000-00-00") if prior is not None else None
    lines = [*_header(inv, prior, ctx.by_id), *_layouts(inv, firm), "", *_activity(inv, since)]
    if prior is None:
        lines += _listing(rows, meta, dumps, int(firm.pack("listing_rows_cap")))
    else:
        lines += _recheck_listing(rows, meta, dumps, prior, changed)
    readable = [r for r in rows if is_readable(meta[r["id"]]["state"]) and (changed is None or r["id"] in changed)]
    readable.sort(key=lambda r: (priority(r, firm), r["date"]))
    budgets = {r["id"]: budget_for(r, firm) for r in readable}
    chars = {r["id"]: len(texts[r["id"]]) + 120 for r in readable}
    cap = int(firm.pack("budget_chars"))
    scale = _scale(sum(len(x) + 1 for x in lines), budgets, chars, cap)
    used, packed = sum(len(x) + 1 for x in lines), []
    for r in readable:
        cost = min(int(budgets[r["id"]] * scale), chars[r["id"]]) + 140
        if used + cost > int(cap * HARD_CAP_RATIO):
            continue
        packed.append(r)
        used += cost
    held = [r for r in readable if r not in packed]
    if held:
        lines.append(f"## {len(held)} readable documents are not in this pack: open any with fetch_doc")
        lines += [f"- [doc {meta[r['id']]['n']}] {r['name']}" for r in held]
    head = "DOCUMENTS NEW OR CHANGED SINCE THE PRIOR REVIEW" if prior is not None else "DOCUMENT TEXT"
    lines += ["", f"## {head} ({len(packed)} of {len(readable)} readable documents; scale {scale:.2f})"]
    for r in packed:
        cut = int(budgets[r["id"]] * scale)
        text = re.sub(r"\n{3,}", "\n\n", texts[r["id"]]).strip()
        if len(text) > cut:
            text = text[:cut] + f"\n[truncated at {cut} chars of {len(text)}: fetch_doc reads the rest]"
        lines += [f"### [doc {meta[r['id']]['n']}] | {r['folder']} | {r['name']} | {r['date']}", text, ""]
    return {"text": "\n".join(lines), "meta": meta, "readable": len(readable), "packed": len(packed), "scale": scale}


def _listing(rows: list[dict[str, Any]], meta: dict[str, Any], dumps: set[str], rows_cap: int) -> list[str]:
    viewer = [r for r in rows if r["folder"] in dumps and r["ext"] not in TEXT_TYPES]
    listed = [r for r in rows if r not in viewer]
    out = [f"## DOCUMENT LISTING ({len(rows)} documents; {len(viewer)} viewer files inside {len(dumps)} disc dumps)"]
    for folder in sorted(dumps):
        n = sum(1 for r in viewer if r["folder"] == folder)
        out.append(f"- disc dump {folder} | {n} viewer files (not read; not proof of anything)")
    out.append(LISTING_HEAD)
    used = 0
    for r in listed:
        line = listing_row(r, meta)
        used += len(line)
        if used > rows_cap:
            out.append(f"(listing cut at {rows_cap} characters: list_files searches every name)")
            break
        out.append(line)
    return [*out, ""]


LISTING_HEAD = "doc | folder | name | date | state (cite a document as [doc N])"


def listing_row(r: dict[str, Any], meta: dict[str, Any]) -> str:
    st = meta[r["id"]]["state"]
    if st.startswith("duplicate:"):
        twin = meta.get(st.split(":", 1)[1], {}).get("n")
        st = f"same content as [doc {twin}]"
    return f"[doc {meta[r['id']]['n']}] | {r['folder']} | {r['name']} | {r['date']} | {st}"


def cited_ids(review: Any) -> set[str]:
    """Every id inside a stored record, lowercased (documents among them)."""
    return {u.lower() for u in UUID.findall(json.dumps(review, default=str))}


def _recheck_listing(
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    dumps: set[str],
    prior: dict[str, Any],
    changed: set[str] | None,
) -> list[str]:
    """Only the documents new or changed since the prior record and those it
    cites: the record already holds what the rest showed."""
    cited = cited_ids(prior.get("review"))
    new = {str(x).lower() for x in changed or set()}
    listed = [
        r for r in rows if not (r["folder"] in dumps and r["ext"] not in TEXT_TYPES) and r["id"].lower() in new | cited
    ]
    live = {r["id"].lower() for r in rows}
    gone = cited & {str(k).lower() for k in prior.get("manifest") or {}} - live
    out = [
        f"## DOCUMENT LISTING ({len(listed)} of {len(rows)} documents: the ones new or changed since the prior "
        "review, and the ones the prior record cites. list_files and fetch_doc reach every other document)",
        LISTING_HEAD,
        *[listing_row(r, meta) + (" | NEW OR CHANGED" if r["id"].lower() in new else "") for r in listed],
    ]
    if gone:
        out.append(
            f"- {len(gone)} documents the prior record cites are no longer on the matter (the record still shows "
            "each as a bare id): drop or replace those citations"
        )
    return [*out, ""]


__all__ = ["SCAN_MARK", "TEXT_TYPES", "body_chars", "build", "doc_state", "fetch_ids", "is_readable", "rows_of"]
