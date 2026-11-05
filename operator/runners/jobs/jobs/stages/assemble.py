"""`assemble`: a chronology from a unit's map output. MECHANICAL ONLY: no
model call. Sorting, exhibit numbering, citation substitution and the
same-date same-provider clustering are code, never a model, because those are
the parts that must be reproducible.

Refuses (exit 1) over a truncated chunk that was not repaired and over a
chunk that ended REFUSED: both contribute no entries for their source files,
which is the silent-omission class a chronology must not have.

Outputs under runs/<unit>/: entries.md (standalone entries, pre-merge),
clusters.md (same date+provider fragments for the merge), exhibit_map.json
(file name -> exhibit number).

This module is also the one reader of a composed map. A map's entries, the
codes each records and the files each cites are parsed here, so what the file
counts and the code table say is what the entries say (``map_entries``,
``file_entry_counts``, ``code_rows``). Two shapes are on disk and both read: the
two blocks the prompt asks for since ADR 0104 W8, and the five blocks of a map a
kept or in-flight job composed before it (its INDEX rows stay what they were).
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import fileref
from .base import StageRun, read_json
from .compose import read_usage

# File names contain commas, so the name cannot be [^,)]+. A plain lazy body is
# not enough either: the model sometimes writes TWO page groups in one
# citation, "(FILE: X.pdf, p. 114, p. 117)", and a lazy body backtracks to
# swallow the first group, yielding a phantom source. So the name stops before
# the first ", p." and every following page group is captured together.
CITE = re.compile(r"\(FILE:\s*((?:(?!,\s*p\.).)+?)((?:,\s*p\.\s*[0-9,\s\-]+)+)\)")
CITE_NOPAGE = re.compile(r"\(FILE:\s*(.+?)\)")
# the chunk header (chunking.py writes it; a map's FILES-SEEN echoes the ones it accounts for)
FILE_HEADER = re.compile(r"=== FILE: (.+?) \(fileId ([\w.-]+)[,)]")
ENTRY_SPLIT = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE_HEAD = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
MAP_FILE = re.compile(r"map-0*(\d+)(?:-(\d+))?\.md$")
#: The line an entry records its ICD codes on (prompts/map-system.md). A cited
#: line is a sentence of the record, never this.
CODES_LINE = re.compile(r"(?im)^[ \t]*ICD codes?:[ \t]*([^\n]*)$")


def section(txt: str, name: str) -> str:
    m = re.search(rf"##\s*{name}\s*\n(.*?)(?=\n##\s|\Z)", txt, re.S)
    return m.group(1).strip() if m else ""


def norm_provider(p: str) -> str:
    p = re.sub(r"\s+", " ", (p or "").strip().rstrip(".,"))
    return re.sub(r"[^a-z0-9]", "", p.lower())[:28]


def _norm_name(s: str) -> str:
    # Compare STEMS: keeping the extension defeats prefix matching.
    s = re.sub(r"\.(pdf|docx?|tiff?|jpe?g|png|msg)$", "", s.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Resolver:
    """Cited names reconciled against the real file list: the model sometimes
    shortens a name or truncates one containing "(1)"; those would resolve to
    nothing, so they match by longest common prefix, and what cannot be
    resolved is reported rather than kept as a phantom."""

    def __init__(self, real_names: list[str], ids: dict[str, str] | None = None) -> None:
        self.real = list(real_names)
        self.norm = {_norm_name(n): n for n in self.real}
        # id -> name: a citation carrying `[fileId X]` names THAT file, which is
        # how the model tells two same-named attachments apart (fileref.py)
        self.ids = dict(ids or {})
        # name -> ids the CURRENT chunk carried (its `=== FILE:` headers). A bare
        # citation of a name two files share means the one this chunk held --
        # the model saw only that copy and had no reason to mark it.
        self.scope: dict[str, set[str]] = {}
        self.unresolved: set[str] = set()

    def __call__(self, cited: str) -> str | None:
        cited, fid = fileref.parse(cited, set(self.ids))
        if fid:
            return self.ids[fid]
        held = self.scope.get(cited) or set()
        if len(held) == 1 and next(iter(held)) in self.ids:
            return self.ids[next(iter(held))]
        if cited in self.real:
            return cited
        c = _norm_name(cited)
        if c in self.norm:
            return self.norm[c]
        cands = [n for k, n in self.norm.items() if k.startswith(c) or c.startswith(k)]
        if len(cands) == 1:
            return cands[0]
        if cands:
            return max(cands, key=lambda n: len(os.path.commonprefix([_norm_name(n), c])))
        self.unresolved.add(cited)
        return None


def take_codes(chunk: str) -> tuple[str, str]:
    """(the entry without its codes line, the codes it recorded). The line never
    reaches a stage downstream of here, so it cannot reach the firm."""
    codes: list[str] = []

    def one(m: re.Match) -> str:
        if "(FILE:" in m.group(0):
            return m.group(0)
        codes.append(m.group(1).strip())
        return ""

    text = CODES_LINE.sub(one, chunk)
    return (re.sub(r"\n{3,}", "\n\n", text).strip(), ", ".join(c for c in codes if c)) if codes else (chunk, "")


def map_entries(txt: str) -> list[dict[str, Any]]:
    """The dated entries of one map's ENTRIES block, in the order written."""
    body = section(txt, "ENTRIES")
    if not body or "none in this chunk" in body.lower():
        return []
    out: list[dict[str, Any]] = []
    for chunk in ENTRY_SPLIT.split(body):
        chunk = chunk.strip()
        m = DATE_HEAD.match(chunk)
        if m is None:
            continue
        chunk, codes = take_codes(re.sub(r"(?m)^\[entry may continue in next chunk\]\s*$", "", chunk).strip())
        lines = chunk.splitlines()
        head = lines[1] if len(lines) > 1 else ""
        prov = head.split("|")[0].strip() if "|" in head else ""
        out.append(
            {
                "date": f"{m.group(3)}-{m.group(1)}-{m.group(2)}",
                "provider": prov,
                "key": norm_provider(prov),
                "text": chunk,
                "codes": codes,
            }
        )
    return out


def cited_refs(text: str) -> list[str]:
    """Every file reference an entry's citations make, as written."""
    return [f.strip() for f, _ in CITE.findall(text)] + [
        f.strip() for f in CITE_NOPAGE.findall(text) if ", p." not in f
    ]


def scope_of(d: Path, fn: str, txt: str) -> dict[str, set[str]]:
    """name -> ids this map's chunk carried. The chunk text on disk is the
    source: a map of the two-block shape echoes only the headers it wrote no
    entry from, so the map alone would forget which of two same-named files its
    entries mean. The map's own echoes still count, for a job whose chunk text
    is gone."""
    seen: dict[str, set[str]] = defaultdict(set)
    chunk = d / ("chunk-" + fn[len("map-") : -len(".md")] + ".txt")
    for source in (txt, chunk.read_text(encoding="utf-8", errors="replace") if chunk.is_file() else ""):
        for name, fid in FILE_HEADER.findall(source):
            seen[name.strip()].add(fid)
    return seen


def map_names(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if MAP_FILE.match(p.name)) if d.is_dir() else []


def parse_maps(d: Path, maps: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for fn in maps:
        txt = (d / fn).read_text(encoding="utf-8")
        seen = scope_of(d, fn, txt)
        entries += [{**e, "src": fn, "seen": seen} for e in map_entries(txt)]
    return entries


def file_entry_counts(d: Path, unit_files: list[dict[str, Any]]) -> dict[str, int]:
    """Per unit file, the entries that cite it, counted here and resolved the way
    ``substitute`` resolves them: the counts the coverage gate decides on, never
    a number a model typed. A file no entry cites is absent."""
    names = [f["name"] + (f.get("ext") or "") for f in unit_files]
    resolve = Resolver(names, {f["id"]: n for f, n in zip(unit_files, names)})
    out: dict[str, int] = {}
    for e in parse_maps(d, map_names(d)):
        resolve.scope = e["seen"]
        for name in {resolve(ref) for ref in cited_refs(e["text"])} - {None}:
            out[str(name)] = out.get(str(name), 0) + 1
    return out


def code_rows(d: Path) -> list[str]:
    """Every map's codes as `YYYY-MM-DD | provider | codes | file` rows, the shape
    ``update/icd.index_codes`` reads: from the entries that record them, and from
    the INDEX block of a map composed before the block went."""
    rows: list[str] = []
    for fn in map_names(d):
        txt = (d / fn).read_text(encoding="utf-8")
        rows += [ln.strip() for ln in section(txt, "INDEX").splitlines() if ln.strip()]
        rows += [f"{e['date']} | {e['provider']} | {e['codes']} | --" for e in map_entries(txt) if e["codes"]]
    return rows


def exhibit_numbers(entries: list[dict[str, Any]], resolve: Resolver) -> dict[str, int]:
    """Exhibit numbers by the first date each source file is cited on."""
    first_seen: dict[str, str] = {}
    for e in sorted(entries, key=lambda x: x["date"]):
        resolve.scope = e.get("seen") or {}
        for fname, _ in CITE.findall(e["text"]):
            r = resolve(fname)
            if r:
                first_seen.setdefault(r, e["date"])
        for fname in CITE_NOPAGE.findall(e["text"]):
            if ", p." in fname:
                continue
            r = resolve(fname.strip())
            if r:
                first_seen.setdefault(r, e["date"])
    ordered = sorted(first_seen.items(), key=lambda kv: (kv[1], kv[0]))
    return {name: i for i, (name, _) in enumerate(ordered, 1)}


def substitute(text: str, exhibit: dict[str, int], resolve: Resolver) -> str:
    def one(m: re.Match) -> str:
        n = exhibit.get(resolve(m.group(1)) or m.group(1).strip())
        # split, not findall: matching consumes the separating comma so a
        # second ", p. N" group could never match and would be silently lost.
        pages = ", ".join(
            p for p in (re.sub(r"\s+", "", g).strip(",") for g in re.split(r",\s*p\.\s*", m.group(2))) if p
        )
        return f"(Exhibit {n} - p. {pages})" if n else m.group(0)

    def one_np(m: re.Match) -> str:
        inner = m.group(1).strip()
        if ", p." in inner:
            return m.group(0)
        n = exhibit.get(resolve(inner) or inner)
        return f"(Exhibit {n})" if n else m.group(0)

    return CITE_NOPAGE.sub(one_np, CITE.sub(one, text))


def _substitute_entry(e: dict[str, Any], exhibit: dict[str, int], resolve: Resolver) -> str:
    """Citations resolve in the scope of the chunk that wrote them."""
    resolve.scope = e.get("seen") or {}
    return substitute(e["text"], exhibit, resolve)


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    maps = map_names(d)
    trunc: set[str] = set()
    for u in read_usage(d):
        if u.get("stop") == "max_tokens":
            trunc.add(str(u["chunk"]))
        elif u.get("stop") == "end_turn":
            trunc.discard(str(u["chunk"]))
    live = set()
    for f in maps:
        m = MAP_FILE.match(f)
        if m is None:
            continue
        live.add(f"{int(m.group(1))}.{m.group(2)}" if m.group(2) else m.group(1))
    unrepaired = sorted(trunc & live)
    if unrepaired:
        sr.log(f"REFUSING TO ASSEMBLE: chunk(s) {unrepaired} truncated at max_tokens and not repaired")
        return 1
    refused = [f for f in maps if (d / f).read_text(encoding="utf-8").strip().startswith("## REFUSED")]
    if refused:
        sr.log(f"REFUSING TO ASSEMBLE: chunk(s) {refused} were refused and carry no entries")
        return 1
    entries = parse_maps(d, maps)
    unit_files = read_json(sr.slug_dir / "units" / f"{sr.unit.unit}.json", [])
    real_names = [f["name"] + (f.get("ext") or "") for f in unit_files]
    resolve = Resolver(real_names, {f["id"]: n for f, n in zip(unit_files, real_names)})
    exhibit = exhibit_numbers(entries, resolve)
    (d / "exhibit_map.json").write_text(json.dumps(exhibit, indent=1), encoding="utf-8")

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        groups[(e["date"], e["key"])].append(e)
    singles, clusters = [], []
    for (dt, key), grp in sorted(groups.items()):
        (singles.append(grp[0]) if len(grp) == 1 else clusters.append((dt, key, grp)))
    with (d / "entries.md").open("w", encoding="utf-8") as fh:
        for e in sorted(singles, key=lambda x: x["date"]):
            fh.write(_substitute_entry(e, exhibit, resolve) + "\n\n")
    with (d / "clusters.md").open("w", encoding="utf-8") as fh:
        for dt, key, grp in clusters:
            fh.write(f"##### CLUSTER {dt} | {key} ({len(grp)} fragments)\n")
            fh.write("\n---FRAGMENT-BREAK---\n".join(_substitute_entry(g, exhibit, resolve) for g in grp) + "\n\n")
    sr.log(f"{sr.unit.unit}: {len(maps)} map files, {len(entries)} entry fragments")
    sr.log(f"  {len(singles)} standalone, {len(clusters)} clusters to merge")
    sr.log(f"  {len(exhibit)} source files -> exhibit numbers")
    if resolve.unresolved:
        sr.log(f"  UNRESOLVED cited file name(s): {len(resolve.unresolved)}")
        for n in sorted(resolve.unresolved)[:8]:
            sr.log(f"     ? {n[:70]}")
    dates = sorted({e["date"] for e in entries})
    if dates:
        sr.log(f"  date span {dates[0]} .. {dates[-1]} ({len(dates)} distinct)")
    return 0
