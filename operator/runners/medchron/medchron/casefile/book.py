"""`book`: the File Status print-out per matter, filed in the matter.

Ported from the file-review toolset's ``render.py``: one HTML page per record
(every document id rendered as the document's name), printed to PDF by the
image's Chromium headless shell. The seat image installs it under
``PLAYWRIGHT_BROWSERS_PATH`` (``/opt/hermes/.playwright``) and makes the whole
tree world-readable and executable (``chmod -R a+rX /opt/hermes``), so the
runner's own uid can run it; ``SMD_CHROME`` overrides the path. A Chromium
that is missing or fails is OUR machinery: the stage fails (resumable), and no
File Status is ever filed without its PDF. A record holding an em or en dash
is refused, never repaired.

What a closure says on the print-out is what happens to it: a line proposed
for approval says so with its number; a closure the second reader did not
confirm says it stays open and why.
"""

from __future__ import annotations

import datetime as dt
import glob
import html
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .outcome import CasefileFailed
from .rank import LABEL

DASHES = ("—", "–")
DEFAULT_BROWSERS = "/opt/hermes/.playwright"
_SHELLS = (
    "chromium_headless_shell-*/chrome-linux*/headless_shell",
    "chromium_headless_shell-*/*/chrome-headless-shell",
)
CHECK_WORDS = {
    "confirmed": ("Confirmed", "ok"),
    "confirmed_flag": ("Confirmed, with a flag", "flag"),
    "not_found": ("Not found", "bad"),
    "not_confirmed": ("Not confirmed", "flag"),
}
RECORDS_WORDS = {"in": ("In", "ok"), "not_in": ("Not in", "bad"), "not_confirmed": ("Not confirmed", "warn")}
ACTION_WORDS = {
    "order_list": "Records order list",
    "leave_open": "Leave open",
    "draft_letter": "Letter drafted",
    "question": "Question for the firm",
}
CSS = """
@page { size: Letter; margin: 0.6in; }
* { box-sizing: border-box; }
html, body { background: #fff; color: #1a1a1a; }
body { font-family: Georgia, "Times New Roman", serif; font-size: 10.5pt; line-height: 1.4; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 4pt 0; padding-bottom: 4pt; border-bottom: 1.5pt solid #1a1a1a; }
h2 { font-size: 12.5pt; margin: 18pt 0 6pt 0; break-after: avoid; }
p.sub, p.asof { color: #666; margin: 4pt 0 8pt 0; }
p.headline { font-style: italic; margin: 10pt 0 6pt 0; }
table { width: 100%; border-collapse: collapse; margin: 0 0 4pt 0; }
th, td { border: 0.75pt solid #bbb; padding: 5pt 6pt; text-align: left; vertical-align: top; }
th { background: #eee; font-weight: bold; }
tr { break-inside: avoid; }
thead { display: table-header-group; }
.ok { color: #2e6b2e; font-weight: bold; } .bad { color: #b0301a; font-weight: bold; }
.warn, .flag { color: #8a2a12; font-weight: bold; } .act { font-weight: bold; } .empty { color: #666; }
ol, ul { margin: 4pt 0 0 0; padding-left: 22pt; } li { margin: 2pt 0; break-inside: avoid; }
"""
UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def e(value: Any) -> str:
    return "" if value is None else html.escape(str(value), quote=True)


def long_date(iso: Any) -> str:
    try:
        d = dt.date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return str(iso) if iso else "not given"
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def names_for_ids(obj: Any, meta: dict[str, Any]) -> Any:
    """Every document uuid inside every string, replaced by the document's name
    (a dash in a firm's file name flattened to a hyphen)."""

    def name(m: re.Match[str]) -> str:
        got = meta.get(m.group(0).lower()) or meta.get(m.group(0)) or {}
        out = str(got.get("name") or m.group(0)[:8])
        for d in DASHES:
            out = out.replace(d, "-")
        return out

    if isinstance(obj, str):
        return UUID.sub(name, obj)
    if isinstance(obj, dict):
        # An id field keeps its uuid: the print-out matches task ids against the
        # proposed changes, and a renamed id never matches.
        return {k: v if _is_id_key(k) else names_for_ids(v, meta) for k, v in obj.items()}
    if isinstance(obj, list):
        return [names_for_ids(v, meta) for v in obj]
    return obj


def _is_id_key(key: Any) -> bool:
    return str(key) == "id" or str(key).endswith(("_id", "_ids"))


def table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>\n" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>\n{body}</tbody></table>\n"


def _open_tasks(rec: dict[str, Any], lines: dict[str, int], verdicts: dict[str, Any]) -> str:
    rows = []
    for t in rec.get("open_tasks") or []:
        tid, action = str(t.get("task_id") or ""), t.get("action")
        if action == "mark_complete" and tid in lines:
            what = f"Mark complete: proposed, change {lines[tid]}, awaiting approval"
        elif action == "mark_complete" and (verdicts.get(tid) or {}).get("by") == "rule":
            what = "Leave open: tasks about money, a court date or a statute stay with the firm"
        elif action == "mark_complete":
            v = verdicts.get(tid) or {}
            what = f"Leave open: a second reading did not confirm the proof ({v.get('verdict') or 'unsure'})"
        else:
            what = ACTION_WORDS.get(str(action), str(action))
        rows.append([e(t.get("subject")), f'<span class="act">{e(what)}</span>', e(t.get("why"))])
    if not rows:
        return '<h2>Open tasks</h2>\n<p class="empty">No open tasks.</p>\n'
    return "<h2>Open tasks</h2>\n" + table(["Task", "What happens", "Why"], rows)


def _done(rec: dict[str, Any]) -> str:
    rows = []
    for t in rec.get("done_tasks_checked") or []:
        word, cls = CHECK_WORDS.get(str(t.get("check")), (t.get("check"), "warn"))
        rows.append([e(t.get("subject")), f'<span class="{cls}">{e(word)}</span>', e(t.get("proof"))])
    return (
        "<h2>Tasks marked done, checked against the file</h2>\n" + table(["Task", "Check", "Proof"], rows)
        if rows
        else ""
    )


def _facilities(rec: dict[str, Any]) -> str:
    rows = []
    for f in rec.get("facilities") or []:
        word, cls = RECORDS_WORDS.get(str(f.get("records")), (f.get("records"), "warn"))
        rows.append([e(f.get("facility")), f'<span class="{cls}">{e(word)}</span>', e(f.get("what_the_file_shows"))])
    return "<h2>Facilities</h2>\n" + table(["Facility", "Records", "What the file shows"], rows) if rows else ""


def _medicals(rec: dict[str, Any], med_lines: dict[int, int]) -> str:
    rows = []
    for i, m in enumerate(rec.get("medicals") or []):
        if m.get("existing_row"):
            status = "Already on the Medicals tab"
        elif i in med_lines:
            status = f"Proposed, change {med_lines[i]}, awaiting approval"
        else:
            status = "Reported, not proposed"
        charge = "" if m.get("charge") is None else f"{float(m['charge']):,.2f}"
        when = " to ".join(x for x in (m.get("service_start"), m.get("service_end")) if x)
        rows.append([e(m.get("provider")), e(when), e(charge), e(status), e(m.get("note"))])
    return "<h2>Medicals</h2>\n" + table(["Provider", "Service", "Charge", "Status", "Note"], rows) if rows else ""


INSURANCE_SIDES = (("first_party", "Client's own policy"), ("third_party", "Other side"))


def _insurance(rec: dict[str, Any]) -> str:
    ins = rec.get("insurance") or {}
    rows = []
    for key, side in INSURANCE_SIDES:
        for p in ins.get(key) or []:
            source = "Layout only, no document in the file" if p.get("layout_only") else "From the file"
            rows.append(
                [
                    e(side),
                    e(p.get("carrier")),
                    e(p.get("claim_no")),
                    e(p.get("adjuster")),
                    e(p.get("limits")),
                    e(source),
                ]
            )
    if not rows:
        return ""
    return "<h2>Insurance</h2>\n" + table(["Policy", "Carrier", "Claim", "Adjuster", "Limits", "Source"], rows)


def questions_kept(rec: dict[str, Any], verdicts: dict[str, Any]) -> list[Any]:
    """The reader's questions, less any asking the firm to confirm closing a task a firm rule keeps open.

    The reader may propose closing a money task and ask the firm to confirm it;
    code refuses that closure, so the question would ask for something that is
    never on offer."""
    kept_open = [
        str(t.get("subject") or "").strip().lower()
        for t in rec.get("open_tasks") or []
        if (verdicts.get(str(t.get("task_id") or "")) or {}).get("by") == "rule"
    ]
    kept_open = [s for s in kept_open if s]

    def offers_refused(q: Any) -> bool:
        text = str(q).lower()
        return "clos" in text and any(s in text for s in kept_open)

    return [q for q in rec.get("questions") or [] if not offers_refused(q)]


def _lists(rec: dict[str, Any], verdicts: dict[str, Any]) -> str:
    parts = []
    orders = [[e(o.get("facility")), e(o.get("note"))] for o in rec.get("records_to_order") or []]
    if orders:
        parts.append("<h2>Records to order</h2>\n" + table(["Facility", "Note"], orders))
    looks = [f"{e(f.get('finding'))}" for f in [*(rec.get("injury_flags") or []), *(rec.get("time_sensitive") or [])]]
    if looks:
        parts.append("<h2>Worth a look</h2>\n<ul>\n" + "".join(f"<li>{x}</li>\n" for x in looks) + "</ul>\n")
    letters = [
        [e(x.get("type")), e(x.get("status")), e(", ".join(x.get("missing") or []))] for x in rec.get("letters") or []
    ]
    if letters:
        parts.append("<h2>Letters</h2>\n" + table(["Letter", "Status", "Missing"], letters))
    qs = questions_kept(rec, verdicts)
    if qs:
        parts.append("<h2>Questions</h2>\n<ol>\n" + "".join(f"<li>{e(q)}</li>\n" for q in qs) + "</ol>\n")
    return "\n".join(parts)


def render_html(
    rec: dict[str, Any], asof: str, meta: dict[str, Any], rank: dict[str, Any], plan: dict[str, Any]
) -> str:
    """The print-out. ``plan``: ``{"tasks": {task_id: n}, "medicals": {row index: n}, "verdicts": {...}}``."""
    bad = [s for s in _strings(rec) if any(d in s for d in DASHES)]
    if bad:
        raise CasefileFailed(f"stage_unfinished: book: the record for {rec.get('number')} holds an em or en dash")
    rec = names_for_ids(rec, meta)
    number, clients = rec.get("number", ""), ", ".join(rec.get("clients") or []) or "client not given"
    sub = " | ".join(
        [
            e(rec.get("matter_type") or "matter type not given"),
            f"date of loss {e(long_date(rec.get('date_of_loss')))}",
            f"statute {e(long_date(rec.get('statute_date')))}",
            e(rec.get("status") or "status not given"),
        ]
    )
    parts = [
        f"<h1>{e(number)} | {e(clients)}</h1>",
        f'<p class="sub">{sub}</p>',
        f'<p class="asof">From Smokeball as of {e(long_date(asof))}. Priority: {e(LABEL[rank["priority"]])}. {e(rank["reason"])}</p>',
        f'<p class="headline">{e(rec.get("headline"))}</p>',
        _facilities(rec),
        _open_tasks(rec, plan.get("tasks") or {}, plan.get("verdicts") or {}),
        _done(rec),
        _medicals(rec, plan.get("medicals") or {}),
        _insurance(rec),
        _lists(rec, plan.get("verdicts") or {}),
    ]
    body = "\n".join(p for p in parts if p)
    return f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>File Status {e(number)} {e(asof)}</title><style>{CSS}</style></head>\n<body>\n{body}\n</body></html>\n'


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in (str(k), *_strings(v))]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def chrome() -> str | None:
    """The browser binary: ``SMD_CHROME``, else the image's headless shell, else
    a Chromium on PATH."""
    if os.environ.get("SMD_CHROME"):
        return os.environ["SMD_CHROME"]
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or DEFAULT_BROWSERS
    for pattern in _SHELLS:
        hits = sorted(glob.glob(os.path.join(root, pattern)))
        if hits:
            return hits[-1]
    return shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")


def print_pdf(html_path: Path, pdf_path: Path, *, timeout: int = 120) -> None:
    exe = chrome()
    if not exe or not os.access(exe, os.X_OK):
        raise CasefileFailed("stage_unfinished: book: no Chromium the runner can run was found for the PDF")
    # The browser profile lives outside the output folder: left in it, it filled
    # the job's artifact listing and pushed File Status files off it.
    with tempfile.TemporaryDirectory(prefix="casefile-chrome-") as profile:
        cmd = [
            exe,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        # argv is this module's own: the browser path above, flags, and job-local paths.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 - argv built here from the resolved browser path and job-local files, no shell
    if proc.returncode != 0 or not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise CasefileFailed(f"stage_unfinished: book: Chromium did not print the File Status (exit {proc.returncode})")


def file_status_name(number: str, asof: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9 ._-]", "", str(number))[:40]
    return f"File Status {safe} {asof}.pdf"


__all__ = ["chrome", "file_status_name", "names_for_ids", "print_pdf", "render_html"]
