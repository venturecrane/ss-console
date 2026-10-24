"""Section 6 of the document: what was reviewed, and where this document's
edges are. Computed entirely from run artifacts, never written by a model,
because this is the one section whose whole value is that it cannot disagree
with what actually happened.

Every claim traces to a file on disk: raw_manifest.jsonl (pulled set, pull
failures, byte-duplicates), manifest.json and folders.json (everything on the
matter, so exclusions are named), include.json (the authored selection),
extracted.jsonl (page count), ocr_results.jsonl (transcription and the pages
that stayed illegible), msg_attachments.json (email containers: encrypted
holes, folded records). Two optional slug-level files carry observations a
run authored from measured facts (`text_duplicates.json`, `file_observations.json`);
their shapes are pinned, and a file whose rows all fail the shape warns
loudly rather than rendering an empty disclosure.

Decided-by-rule disclosures (2026-10-24 stop-path audit), each read only when
present: ``runs/<unit>/unread_pages.json`` (pages the map never read,
stages/compose.py), ``out/<unit>/page_map.json`` rows with
``rendered_from_text`` (stages/exhibits.py), ``runs/<unit>/
prior_history_itemized.json`` (stages/summarize.py), ``dropped_unkept`` in
msg_attachments.json (email attachments found and not opened), and the
billing stages' ``billing_gaps[-<unit>].json`` and
``billing_dropped_rows[-<unit>].json`` at the slug level. Each is a plain
factual sentence; no figure is ever shown from a billing row the page did
not confirm, and a record the map read is never named away.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .. import unopened
from ..gates import foreign_identity as FI
from .base import read_json, read_jsonl


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _scope_lines(d: Path, byid: dict, ok: list, folded: list, failed: list) -> list[str]:
    o: list[str] = []
    n_file = len(ok) - len(folded)
    line = f"This chronology was prepared from {_plural(n_file, 'document')} in the matter file"
    if folded:
        line += (
            f", together with {_plural(len(folded), 'record')} that existed only as attachments to email in the matter"
        )
    pages = sum(int(e.get("pages") or 0) for e in read_jsonl(d / "extracted.jsonl"))
    if pages:
        line += f", totalling approximately {pages:,} pages"
    o.append(line + ".")
    # The count belongs in the opening sentence, not only in the list further
    # down. A run that could retrieve a fraction of the file still produces a
    # document that looks like a chronology, and the reader who skims section 6
    # is the one who most needs to know how much of the file is behind it.
    if failed:
        o.append(
            f"{_plural(len(failed), 'document')} in the reviewed folders could not be retrieved at all "
            f"and {'is' if len(failed) == 1 else 'are'} not reflected anywhere in this chronology; "
            f"each is named below."
        )
    inc = read_json(d / "include.json", {}) or {}
    prefixes = inc.get("include_prefixes") or []
    if prefixes:
        o += ["", "Folders reviewed: " + "; ".join(sorted(prefixes)) + "."]
    man = read_json(d / "manifest.json", [])
    man = man["documents"] if isinstance(man, dict) else man
    if man and prefixes:
        fpath = {f0["id"]: f0["path"] for f0 in read_json(d / "folders.json", [])}
        fld: dict[str, int] = {}
        for m in man:
            if m.get("deleted") or m.get("id") in byid:
                continue
            fid = m.get("folderId")
            f = (
                fpath[fid]
                if fid and fid in fpath
                else ("/(folder not listed in the matter file)" if fid else "/(no folder)")
            )
            top = ("/" + f.lstrip("/").split("/", 1)[0]) if f.startswith("/") and not f.startswith("/(") else f
            fld[top] = fld.get(top, 0) + 1
        skipped = sorted(
            (f, n) for f, n in fld.items() if not any(f.startswith(px) or px.startswith(f) for px in prefixes)
        )
        if skipped:
            o += [
                "",
                "Folders in the matter file outside the scope of this review, with the number of documents in each:\n",
            ]
            o += [f"* {f} ({_plural(n, 'document')})" for f, n in skipped]
    return o


def _unread_lines(d: Path, failed: list, log: Callable[[str], None]) -> list[str]:
    o: list[str] = []
    unread = [
        (r.get("name", "?"), r.get("folder", ""), str(r.get("error") or "could not be retrieved")[:90]) for r in failed
    ]
    m = read_json(d / "msg_attachments.json", {}) or {}
    if m.get("emails_opened"):
        line = (
            f"Email in the matter file was reviewed: {m['emails_opened']} messages were opened and their attachments "
            f"extracted. {m.get('distinct_attachments') or 0} distinct attachments were found, of which "
            f"{m.get('already_pulled') or 0} were copies of documents already in the reviewed set and "
            f"{m.get('new_to_the_corpus') or 0} were new to it"
        )
        # A withheld attachment was found and NOT read (2026-10-24: the
        # identity rule withholds them now, so "and were read" became false).
        held_back = _withheld_attachments(d, m)
        if not held_back:
            line += " and were read."
        elif held_back >= int(m.get("new_to_the_corpus") or 0):
            line += f"; {'it was' if held_back == 1 else 'they were'} left out, for the reason given below."
        else:
            verb = "was" if held_back == 1 else "were"
            line += f"; {held_back} of those {verb} left out, for the reason given below, and the rest were read."
        enc = m.get("encrypted") or []
        if enc:
            line += f" {_plural(len(enc), 'attachment')} could not be opened (listed below)."
        o += ["", line]
    # Pulled intact, then could not be opened without its password: an ok row
    # in raw_manifest, so `failed` never carries it (unopened.py).
    byid = {r.get("id"): r for r in read_jsonl(d / "raw_manifest.jsonl")}
    for fid, name in unopened.password_protected(d).items():
        unread.append((name, str((byid.get(fid) or {}).get("folder") or ""), unopened.REASON))
    for e in m.get("encrypted") or []:
        unread.append(
            (
                e.get("attachment", "?"),
                str(e.get("email", ""))[:60],
                "encrypted by the sender (Microsoft RMS); opening it requires the recipient's credentials",
            )
        )
    if unread:
        o += ["", "The following documents could not be read, and nothing from them appears in this chronology:\n"]
        o += [f'* "{name}"{f" ({where})" if where else ""} - {why}' for name, where, why in sorted(unread)]
    # The last row per document: a resume that re-asks a failed image page
    # records the document again, and the newer row is the one that holds.
    ocr = list({r.get("id"): r for r in read_jsonl(d / "ocr_results.jsonl")}.values())
    # `failed_pages` was computed on every scanned document and read by nobody,
    # so a page the reader could not transcribe at all was disclosed to the
    # client exactly nowhere. An unread page belongs in the section above this
    # one -- that section is the whole point of this stage.
    failed = [r for r in ocr if (r.get("failed_pages") or 0) > 0]
    if failed:
        total = sum(r["failed_pages"] for r in failed)
        o += [
            "",
            f"The following documents were scanned images. {_plural(total, 'page')} could not be transcribed at "
            f"all, and nothing from those pages appears in this chronology:\n",
        ]
        o += [
            f'* "{r.get("name", "?")}" - {_plural(r["failed_pages"], "page")} of {r.get("pages", "?")}'
            for r in sorted(failed, key=lambda x: -x["failed_pages"])
        ]
    ill = [r for r in ocr if (r.get("illegible_marks") or 0) > 0]
    if ill:
        total = sum(r["illegible_marks"] for r in ill)
        o += [
            "",
            f"The following documents were scanned images and were transcribed for this chronology. Transcription "
            f"left {_plural(total, 'passage')} unreadable, marked as illegible in the source; entries drawn from "
            f"these documents are otherwise complete:\n",
        ]
        o += [
            f'* "{r.get("name", "?")}" - {_plural(r["illegible_marks"], "passage")}'
            for r in sorted(ill, key=lambda x: -x["illegible_marks"])
        ]
    return o


def _duplicate_lines(d: Path, byid: dict, dupes: list, unit: str | None, log: Callable[[str], None]) -> list[str]:
    o: list[str] = []
    if dupes:
        rows = []
        for r in dupes:
            keep = byid.get(r["duplicate_of"]) or {}
            rows.append((r.get("name", "?"), r.get("folder", ""), keep.get("name", "?"), keep.get("folder", "")))
        o += [
            "",
            "The following documents in the matter file are byte-identical copies of documents already reflected "
            "above. One copy was processed; nothing was removed from the matter file:\n",
        ]
        o += [
            f'* "{name}" ({fold}) is an exact copy of "{kname}" ({kfold})' for name, fold, kname, kfold in sorted(rows)
        ]
    tdupes = read_json(d / "text_duplicates.json", None)
    if tdupes is not None:
        raw_n = len(tdupes) if isinstance(tdupes, list) else 0
        rows_t = [
            t
            for t in (tdupes if isinstance(tdupes, list) else [])
            if isinstance(t, dict) and t.get("kept") and t.get("dropped")
        ]
        if raw_n and not rows_t:
            log(
                "WARNING: text_duplicates.json rows carry no kept+dropped; schema drift, disclosure would be silently empty"
            )
        if unit is not None:
            rows_t = [t for t in rows_t if not t.get("unit") or t["unit"] == unit]
        if rows_t:
            o += [
                "",
                "The following documents contain identical text although the files themselves differ (separate scans "
                "of one document). One copy was processed; nothing was removed from the matter file. Where the two "
                "filenames claim different date ranges, the content is identical and one name misdescribes its "
                "contents:\n",
            ]
            o += [
                f'* "{t.get("dropped", "?")}" contains the same text as "{t.get("kept", "?")}"'
                + (f" ({t['chars']:,} characters)" if t.get("chars") else "")
                for t in sorted(rows_t, key=lambda x: x.get("kept", ""))
            ]
    obs = read_json(d / "file_observations.json", None)
    if obs is not None:
        raw_n = len(obs) if isinstance(obs, list) else 0
        rows_o = [x for x in (obs if isinstance(obs, list) else []) if isinstance(x, dict) and x.get("text")]
        if raw_n and not rows_o:
            log(
                "WARNING: file_observations.json rows carry no 'text'; schema drift, observations would be silently empty"
            )
        if unit is not None:
            rows_o = [x for x in rows_o if not x.get("unit") or x["unit"] == unit]
        if rows_o:
            o += ["", "Observations about the matter file noted during this review:\n"]
            o += [f"* {x['text']}" for x in rows_o]
    return o


def _shown(name: str) -> str:
    """A file name as the firm sees it. An email attachment's pulled row keeps
    its extension in its name and again in ``ext`` (stages/msg.py), so the key
    the coverage gate matches on reads "x.pdf.pdf"; the firm is shown "x.pdf"."""
    return re.sub(r"(\.[A-Za-z0-9]{2,5})\1$", r"\1", name)


def _withheld_attachments(d: Path, m: dict) -> int:
    """New email attachments that were withheld (the firm's direction or the
    identity rule): found, and not read."""
    withheld = FI.withheld(d)
    if not withheld:
        return 0
    return sum(
        1
        for a in m.get("attachments") or []
        if isinstance(a, dict)
        and not a.get("already_pulled_as")
        and FI.matches(str(a.get("sha256") or "")[:12], withheld)
    )


def _withheld_names(d: Path, byid: dict, listed: set[str]) -> list[tuple[str, str]]:
    """Withheld documents orphans.json does not already name, each with how it
    is disclosed (``FI.withheld_reason``: the firm's direction, or the identity
    rule's reason, 2026-10-24): a withheld email attachment is never folded, so
    it has no pulled row and only the email index knows its file name."""
    out: list[tuple[str, str]] = []
    m = read_json(d / "msg_attachments.json", {}) or {}
    by12 = {str(a.get("sha256") or "")[:12]: a for a in (m.get("attachments") or []) if isinstance(a, dict)}
    for wid in sorted(FI.withheld(d)):
        row = byid.get(wid) or byid.get(f"msgatt-{wid}")
        if row is not None:
            name = str(row.get("name") or "") + str(row.get("ext") or "")
        else:
            a = by12.get(wid.removeprefix("msgatt-")) or {}
            name = str((a.get("names") or [""])[0] or "")
        if name and name not in listed:
            out.append((name, FI.withheld_reason(d, wid)))
    return out


def _not_used_lines(d: Path, byid: dict, unit: str | None, cfg: Any) -> list[str]:
    """Every pulled document that is not a source of this chronology, with the
    factual reason (2026-10-23). The reasons were already decided by rule and
    printed by the coverage gate, but only SMD read that log: a document the
    firm said to leave out, one the composer read and drew no entry from, or
    one excluded by name or size was named to the firm nowhere.

    Sources, each written by the stage that decided it: ``orphans.json`` (files
    no unit owns: withheld, excluded by class, name or size, or unreadable),
    the withheld list (``identity_release.json``), and
    ``coverage_decisions.json`` with the coverage gate's own rule
    (``coverage.rule_rows``, computed here from the same reader the gate
    records with, because this section is written before the gate runs).
    Password-protected files are named in the unreadable list above, so they
    are not repeated here."""
    from .. import coverage_decide
    from . import coverage

    o: list[str] = []
    rows = [
        (str(x.get("name") or ""), str(x.get("reason") or ""))
        for x in (read_json(d / "orphans.json", {}) or {}).get("orphans") or []
        if isinstance(x, dict) and x.get("name") and x.get("reason") and x.get("reason") != unopened.REASON
    ]
    listed = {n for n, _ in rows}
    rows += _withheld_names(d, byid, listed)
    if rows:
        o += [
            "",
            "The following documents were retrieved and not used as sources for this chronology, each for the reason stated:\n",
        ]
        o += [f'* "{_shown(name)}" - {why}' for name, why in sorted(rows)]
    read: dict[str, str] = {}
    if unit is not None:
        for r in coverage_decide.rows(d, unit):
            read[str(r["name"])] = str(r["reason"])
        if cfg is not None:
            for r in coverage.rule_rows(d, unit, cfg):
                read.setdefault(r["name"], r["reason"])
    if read:
        o += [
            "",
            "The following documents were read in full and no entry in this chronology is drawn from them, "
            "each for the reason stated:\n",
        ]
        o += [f'* "{name}" - {why}' for name, why in sorted(read.items())]
    return o


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


def _billing_lines(d: Path, unit: str | None, log: Callable[[str], None]) -> list[str]:
    o: list[str] = []
    gaps = _billing_rows(d, unit, "billing_gaps.json", log)
    if gaps:
        o += [
            "",
            "The following billing pages were identified but could not be read, and no amount from them appears "
            "in the billing summary:\n",
        ]
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


def section(slug_dir: Path, unit: str | None, log: Callable[[str], None], cfg: Any = None) -> list[str]:
    o = ["", "## Records Reviewed and Limitations\n"]
    raw = read_jsonl(slug_dir / "raw_manifest.jsonl")
    byid: dict[Any, dict] = {r.get("id"): r for r in raw}
    ok = [r for r in byid.values() if r.get("ok") and not r.get("duplicate_of")]
    failed = [r for r in byid.values() if not r.get("ok")]
    dupes = [r for r in byid.values() if r.get("duplicate_of")]
    folded = [r for r in ok if str(r.get("id", "")).startswith("msgatt-")]
    o += _scope_lines(slug_dir, byid, ok, folded, failed)
    o += _unread_lines(slug_dir, failed, log)
    o += _map_unread_lines(slug_dir, unit)
    o += _attachments_not_opened(slug_dir)
    o += _rendered_lines(slug_dir, unit)
    o += _billing_lines(slug_dir, unit, log)
    o += _prior_history_lines(slug_dir, unit)
    o += _not_used_lines(slug_dir, byid, unit, cfg)
    o += _duplicate_lines(slug_dir, byid, dupes, unit, log)
    if len(o) == 2:
        o.append(
            "All documents in the reviewed folders were retrieved and read in full. No document was unreadable, "
            "encrypted, or excluded."
        )
    return o
