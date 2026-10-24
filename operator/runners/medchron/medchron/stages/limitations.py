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

from pathlib import Path
from typing import Any, Callable

from .. import unopened
from ..gates import foreign_identity as FI
from .base import read_json, read_jsonl
from .limitations_rules import (
    _attachments_not_opened,
    _billing_lines,
    _map_unread_lines,
    _prior_history_lines,
    _rendered_lines,
    _review_left_out_lines,
    _shown,
)


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
        # A decision recorded on an earlier run can name a file this document
        # now cites; it is never described as one no entry is drawn from.
        cited = (coverage.cited_files(d, unit) or (set(), set()))[0]
        for r in coverage_decide.rows(d, unit):
            if str(r["name"]) not in cited:
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


#: The section heading build_doc writes, and the last section of the document.
HEADING = "## Records Reviewed and Limitations"


def refresh(doc: Path, slug_dir: Path, unit: str | None, log: Callable[[str], None], cfg: Any = None) -> bool:
    """Rewrite the limitations section of ``final-chronology.md`` from the
    artifacts as they stand now. build_doc writes the section before the
    pre-delivery review; the review can leave an entry out by rule
    (``review-left-out.json``) and re-render, so it calls this first and the
    delivered document discloses what the review decided. True when the
    section changed."""
    md = doc.read_text(encoding="utf-8")
    at = md.find("\n" + HEADING)
    if at < 0:
        log("limitations refresh: the document carries no limitations section; left as it is")
        return False
    fresh = md[:at] + "\n".join(section(slug_dir, unit, log, cfg))
    if fresh == md:
        return False
    doc.write_text(fresh, encoding="utf-8")
    return True


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
    o += _review_left_out_lines(slug_dir, unit, log)
    o += _not_used_lines(slug_dir, byid, unit, cfg)
    o += _duplicate_lines(slug_dir, byid, dupes, unit, log)
    if len(o) == 2:
        o.append(
            "All documents in the reviewed folders were retrieved and read in full. No document was unreadable, "
            "encrypted, or excluded."
        )
    return o
