"""Records Reviewed and Limitations: the disclosures for what a stage
decided by rule instead of stopping the job (the 2026-10-24 stop-path audit).
Split from ``limitations.py`` for size; ``limitations.section`` calls each.
Every sentence is computed from an artifact the deciding stage wrote, never
by a model, and a record the map read is never named away here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .. import unopened
from .base import read_json


def _shown(name: str) -> str:
    """A file name as the firm sees it. An email attachment's pulled row keeps
    its extension in its name and again in ``ext`` (stages/msg.py), so the key
    the coverage gate matches on reads "x.pdf.pdf"; the firm is shown "x.pdf"."""
    return re.sub(r"(\.[A-Za-z0-9]{2,5})\1$", r"\1", name)


def _pages(pages: list[int]) -> str:
    """'p. 4' or 'pp. 1-3, 7': a page list as a reader writes it."""
    ps = sorted(set(pages))
    runs: list[str] = []
    start = prev = ps[0]
    for p in ps[1:] + [None]:
        if p is not None and p == prev + 1:
            prev = p
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        if p is not None:
            start = prev = p
    return ("p. " if len(ps) == 1 else "pp. ") + ", ".join(runs)


def _unit_files(d: Path, unit: str | None, sub: str, name: str) -> list[Path]:
    """The per-unit artifact `name` under `sub/<unit>/`, or every unit's when
    the section is written for the whole matter."""
    if unit is not None:
        p = d / sub / unit / name
        return [p] if p.is_file() else []
    return sorted((d / sub).glob(f"*/{name}")) if (d / sub).is_dir() else []


#: How each reason the map named a page unread reads to the firm
#: (stages/compose.py UNREAD_REASONS writes the codes).
_UNREAD_SAY = {
    "refused": "The following pages were not read: the automated reading declined to process them on every "
    "attempt, including on a second, stronger model. Nothing from those pages appears in this chronology:\n",
    "empty": "The following pages were not read: the automated reading returned no account of them on every "
    "attempt. Nothing from those pages appears in this chronology:\n",
    "truncated": "The following pages were not read: each is too dense for the automated reading to finish "
    "within its output limit, even one page at a time. Nothing from those pages appears in this chronology:\n",
}


def _map_unread_lines(d: Path, unit: str | None) -> list[str]:
    """Pages the map never read (stages/compose.py, unread_pages.json). Only a
    page nothing read is here: a record the map read is never named away."""
    by: dict[str, dict[str, set[int] | None]] = {}
    for p in _unit_files(d, unit, "runs", "unread_pages.json"):
        for r in read_json(p, []) or []:
            if not isinstance(r, dict) or not r.get("file") or r.get("reason") not in _UNREAD_SAY:
                continue
            files = by.setdefault(r["reason"], {})
            have = files.get(r["file"], set())
            files[r["file"]] = None if have is None or not r.get("pages") else have | set(r["pages"])
    o: list[str] = []
    for reason, files in by.items():
        o += ["", _UNREAD_SAY[reason]]
        o += [
            f'* "{_shown(name)}"' + (f" - {_pages(sorted(ps))}" if ps else " - the whole document")
            for name, ps in sorted(files.items())
        ]
    return o


def _rendered_lines(d: Path, unit: str | None) -> list[str]:
    """Exhibit pages made from a document's extracted text (stages/exhibits.py
    ``rendered_from_text``): a word-processing file, or a PDF no reader could
    page. The words are the document's; the layout is not."""
    names: set[str] = set()
    for p in _unit_files(d, unit, "out", "page_map.json"):
        for ex in read_json(p, []) or []:
            for f in (ex.get("files") or []) if isinstance(ex, dict) else []:
                if isinstance(f, dict) and f.get("rendered_from_text") and f.get("file"):
                    names.add(str(f["file"]))
    if not names:
        return []
    return [
        "",
        "The following documents could not be included in the exhibits as their original pages (a word-processing "
        "file, or a file no PDF reader could open). Their exhibit pages are rendered from the document's text, "
        "without its original layout:\n",
    ] + [f'* "{_shown(n)}"' for n in sorted(names)]


def _unattributed_lines(d: Path, unit: str | None) -> list[str]:
    """The exhibit of cited records no rule could attribute to a provider
    (stages/exhibits.py ``_settle_sentinel``): titled for what it is, and each
    of its documents named."""
    rows: list[tuple[int, str]] = []
    for p in _unit_files(d, unit, "out", "page_map.json"):
        for ex in read_json(p, []) or []:
            if isinstance(ex, dict) and ex.get("unattributed"):
                rows += [(int(ex.get("exhibit") or 0), str(f["file"])) for f in ex.get("files") or [] if f.get("file")]
    if not rows:
        return []
    return [
        "",
        "Neither the record nor the chronology entries citing them name the provider of the following documents, "
        "so they are filed together in an exhibit titled Unattributed Records:\n",
    ] + [f'* Exhibit {n}: "{_shown(name)}"' for n, name in sorted(rows)]


#: Written by ``stages/exhibit_merge`` under ``runs/<unit>/``: one row per
#: merged exhibit, {"kept", "kept_title", "dropped_old", "files_kept",
#: "files_dropped", "pages"}.
MERGES_FILE = "exhibit_merges.json"


def _quoted(names: list[str]) -> str:
    return ", ".join(f'"{_shown(n)}"' for n in names)


def _merge_lines(d: Path, unit: str | None) -> list[str]:
    """Exhibits delivered once because two of them held the same record pages
    (2026-10-25, Pamplona: one Kaiser ER PDF saved twice, and two overlapping
    university-hospital exports). Factual: which files, and the exhibit that holds them."""
    rows = [
        r
        for p in _unit_files(d, unit, "runs", MERGES_FILE)
        for r in read_json(p, []) or []
        if isinstance(r, dict) and r.get("files_dropped") and r.get("files_kept") and r.get("kept")
    ]
    if not rows:
        return []
    return [
        "",
        "The following documents hold the same record pages as other documents in the matter file. Each set of "
        "pages is delivered once; nothing was removed from the matter file:\n",
    ] + [
        f"* {_quoted(r['files_dropped'])} and {_quoted(r['files_kept'])} hold the same record pages; they are "
        f"delivered once, as Exhibit {r['kept']}."
        for r in sorted(rows, key=lambda x: int(x["kept"]))
    ]


def _prior_history_lines(d: Path, unit: str | None) -> list[str]:
    """stages/summarize.py could not write a Prior Medical History summary
    that cites only pages the entries cite, so it itemized them instead."""
    if not _unit_files(d, unit, "runs", "prior_history_itemized.json"):
        return []
    return [
        "",
        "The pre-incident records are itemized entry by entry in the chronology; no separate summary of the "
        "prior medical history is included.",
    ]


#: Attachment kinds the email index drops that could hold a record: a kind it
#: does not open (an archive, a nested email), or a small non-image file.
_NOT_OPENED_IMAGE = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif"}


def _attachments_not_opened(d: Path) -> list[str]:
    """Email attachments found and not opened (stages/msg.py `dropped_unkept`).
    The never-a-record kinds (calendar files, signatures, logos) stay out; an
    archive, an attached email, or a small document is named, because nothing
    inside it was read."""
    m = read_json(d / "msg_attachments.json", {}) or {}
    rows: set[tuple[str, str, str]] = set()
    for a in m.get("dropped_unkept") or []:
        if not isinstance(a, dict) or a.get("already_pulled_as"):
            continue
        name = str(a.get("attachment") or "")
        ext = ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""
        if a.get("kind") == "other":
            why = "a kind of file this review does not open"
        elif a.get("kind") == "skipped-tiny" and ext not in _NOT_OPENED_IMAGE:
            why = "under 20 KB, treated as an inline graphic and not opened"
        else:
            continue
        rows.add((name, str(a.get("email") or ""), why))
    if not rows:
        return []
    return [
        "",
        "The following email attachments were found and not opened, and nothing inside them appears in this "
        "chronology:\n",
    ] + [f'* "{n}"' + (f' (attached to the email "{e}")' if e else "") + f" - {w}" for n, e, w in sorted(rows)]


def _billing_rows(d: Path, unit: str | None, name: str, log: Callable[[str], None]) -> list[tuple[str, int, str]]:
    """Rows of a billing disclosure file as (file, page, reason). The billing
    stages write them at the slug level, suffixed per unit on a joint matter
    (`billing_gaps-<unit>.json`, the billing_chart convention), so the unit's
    own file wins and the plain one is the single-unit case. Never an amount:
    an amount the page did not confirm is what these files keep out."""
    stem = name[: -len(".json")]
    own = d / f"{stem}-{unit}.json" if unit is not None else None
    paths = [own] if own is not None and own.is_file() else ([d / name] if (d / name).is_file() else [])
    out: set[tuple[str, int, str]] = set()
    raw = 0
    for p in paths:
        rows = read_json(p, [])
        raw += len(rows) if isinstance(rows, list) else 0
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and r.get("file") and str(r.get("page") or "").isdigit():
                out.add((str(r["file"]), int(r["page"]), str(r.get("reason") or "").strip()))
    if raw and not out:
        log(f"WARNING: {name} rows carry no file+page; schema drift, disclosure would be silently empty")
    return sorted(out)


def _stem(name: str) -> str:
    """The name billing_docs.json uses for a file (``units.billing_stem``)."""
    return name[:-4] if name.lower().endswith((".pdf", ".doc")) else name


def _billing_lines(d: Path, unit: str | None, log: Callable[[str], None]) -> list[str]:
    o: list[str] = []
    gaps = _billing_rows(d, unit, "billing_gaps.json", log)
    # A password-protected bill is one file that could not be opened, not a
    # list of unread pages. The unread-documents list above already names it
    # when the extract stage recorded the password (``unopened``); otherwise
    # it is named here once, with the same words.
    named = {_stem(n) for n in unopened.password_protected(d).values()}
    locked = sorted({f for f, _, r in gaps if r == unopened.REASON and _stem(f) not in named})
    gaps = [g for g in gaps if g[2] != unopened.REASON]
    if gaps or locked:
        o += [
            "",
            "The following billing pages were identified but could not be read, and no amount from them appears "
            "in the billing summary:\n",
        ]
        o += [f'* "{f}" - {unopened.REASON}' for f in locked]
        o += [f'* "{f}" - p. {p}' + (f" ({r})" if r else "") for f, p, r in gaps]
    dropped = _billing_rows(d, unit, "billing_dropped_rows.json", log)
    if dropped:
        o += [
            "",
            "The following billing rows were left out of the billing summary because their amount could not be "
            "confirmed against the page:\n",
        ]
        o += [f'* "{f}" - p. {p}' + (f" ({r})" if r else "") for f, p, r in dropped]
    return o


#: Written by the pre-delivery review (stages/review_evidence.py LEFT_OUT):
#: {"unit", "entries": [{"date", "provider", "exhibit", "pages", "reason"}]}.
REVIEW_LEFT_OUT = "review-left-out.json"


def _review_left_out_lines(d: Path, unit: str | None, log: Callable[[str], None]) -> list[str]:
    """Entries the review left out by rule (another person's record, or a
    page that does not identify the client). Each is named by its date,
    provider, exhibit and pages with the review's own finished reason clause;
    the other person is never named, because nothing here carries a name but
    the provider's."""
    rows: list[str] = []
    raw = 0
    for p in _unit_files(d, unit, "out", REVIEW_LEFT_OUT):
        rec = read_json(p, {}) or {}
        ents = rec.get("entries") if isinstance(rec, dict) else None
        raw += len(ents) if isinstance(ents, list) else 0
        for e in ents if isinstance(ents, list) else []:
            if not isinstance(e, dict) or not e.get("date") or not str(e.get("reason") or "").strip():
                continue
            where = [str(e.get("provider") or "").strip()]
            if isinstance(e.get("exhibit"), int):
                where.append(f"Exhibit {e['exhibit']}")
            pages = [int(x) for x in e.get("pages") or [] if isinstance(x, int) or str(x).isdigit()]
            if pages:
                where.append(_pages(pages))
            place = ", ".join(w for w in where if w)
            reason = str(e["reason"]).strip().rstrip(".")
            rows.append(f"* The entry dated {e['date']}" + (f" ({place})" if place else "") + f" {reason}.")
    if raw and not rows:
        log(f"WARNING: {REVIEW_LEFT_OUT} rows carry no date+reason; schema drift, disclosure would be silently empty")
    if not rows:
        return []
    return [
        "",
        "The following entries were left out of this chronology before delivery, each for the reason stated:\n",
    ] + rows
