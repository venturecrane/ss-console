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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .base import read_json, read_jsonl


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _scope_lines(d: Path, byid: dict, ok: list, folded: list) -> list[str]:
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
            f"{m.get('new_to_the_corpus') or 0} were new to it and were read."
        )
        enc = m.get("encrypted") or []
        if enc:
            line += f" {_plural(len(enc), 'attachment')} could not be opened (listed below)."
        o += ["", line]
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
    ill = [r for r in read_jsonl(d / "ocr_results.jsonl") if (r.get("illegible_marks") or 0) > 0]
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


def section(slug_dir: Path, unit: str | None, log: Callable[[str], None]) -> list[str]:
    o = ["", "## Records Reviewed and Limitations\n"]
    raw = read_jsonl(slug_dir / "raw_manifest.jsonl")
    byid: dict[Any, dict] = {r.get("id"): r for r in raw}
    ok = [r for r in byid.values() if r.get("ok") and not r.get("duplicate_of")]
    failed = [r for r in byid.values() if not r.get("ok")]
    dupes = [r for r in byid.values() if r.get("duplicate_of")]
    folded = [r for r in ok if str(r.get("id", "")).startswith("msgatt-")]
    o += _scope_lines(slug_dir, byid, ok, folded)
    o += _unread_lines(slug_dir, failed, log)
    o += _duplicate_lines(slug_dir, byid, dupes, unit, log)
    if len(o) == 2:
        o.append(
            "All documents in the reviewed folders were retrieved and read in full. No document was unreadable, "
            "encrypted, or excluded."
        )
    return o
