"""`assemble`: a chronology from a unit's map output. MECHANICAL ONLY: no
model call. Sorting, exhibit numbering, citation substitution and the
same-date same-provider clustering are code, never a model, because those are
the parts that must be reproducible.

Refuses (exit 1) over a truncated chunk that was not repaired and over a
chunk that ended REFUSED: both contribute no entries for their source files,
which is the silent-omission class a chronology must not have.

Outputs under runs/<unit>/: entries.md (standalone entries, pre-merge),
clusters.md (same date+provider fragments for the merge), exhibit_map.json
(file name -> exhibit number), billing_dates.md, conflicts.md, files_seen.md.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .base import StageRun, read_json
from .compose import read_usage

# File names contain commas, so the name cannot be [^,)]+. A plain lazy body is
# not enough either: the model sometimes writes TWO page groups in one
# citation, "(FILE: X.pdf, p. 114, p. 117)", and a lazy body backtracks to
# swallow the first group, yielding a phantom source. So the name stops before
# the first ", p." and every following page group is captured together.
CITE = re.compile(r"\(FILE:\s*((?:(?!,\s*p\.).)+?)((?:,\s*p\.\s*[0-9,\s\-]+)+)\)")
CITE_NOPAGE = re.compile(r"\(FILE:\s*(.+?)\)")
ENTRY_SPLIT = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE_HEAD = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
MAP_FILE = re.compile(r"map-0*(\d+)(?:-(\d+))?\.md$")


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

    def __init__(self, real_names: list[str]) -> None:
        self.real = list(real_names)
        self.norm = {_norm_name(n): n for n in self.real}
        self.unresolved: set[str] = set()

    def __call__(self, cited: str) -> str | None:
        cited = cited.strip()
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


def parse_maps(d: Path, maps: list[str]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    entries: list[dict[str, Any]] = []
    buckets: dict[str, list[str]] = {"billing_dates.md": [], "conflicts.md": [], "files_seen.md": []}
    names = (
        ("BILLING-DATES", "billing_dates.md"),
        ("CONFLICTS / REFERENCED-BUT-ABSENT", "conflicts.md"),
        ("FILES-SEEN", "files_seen.md"),
    )
    for fn in maps:
        txt = (d / fn).read_text(encoding="utf-8")
        body = section(txt, "ENTRIES")
        if body and "none in this chunk" not in body.lower():
            for chunk in ENTRY_SPLIT.split(body):
                chunk = chunk.strip()
                if not DATE_HEAD.match(chunk):
                    continue
                chunk = re.sub(r"(?m)^\[entry may continue in next chunk\]\s*$", "", chunk).strip()
                lines = chunk.splitlines()
                head = lines[1] if len(lines) > 1 else ""
                prov = head.split("|")[0].strip() if "|" in head else ""
                m = DATE_HEAD.match(chunk)
                if m is None:
                    continue
                entries.append(
                    {
                        "date": f"{m.group(3)}-{m.group(1)}-{m.group(2)}",
                        "provider": prov,
                        "key": norm_provider(prov),
                        "text": chunk,
                        "src": fn,
                    }
                )
        for name, out in names:
            s = section(txt, re.escape(name))
            if s and "none" not in s.lower()[:20]:
                buckets[out].append(f"<!-- {fn} -->\n{s}")
    return entries, buckets


def exhibit_numbers(entries: list[dict[str, Any]], resolve: Resolver) -> dict[str, int]:
    """Exhibit numbers by the first date each source file is cited on."""
    first_seen: dict[str, str] = {}
    for e in sorted(entries, key=lambda x: x["date"]):
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


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    maps = sorted(p.name for p in d.iterdir() if MAP_FILE.match(p.name))
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
    entries, buckets = parse_maps(d, maps)
    real_names = [
        f["name"] + (f.get("ext") or "") for f in read_json(sr.slug_dir / "units" / f"{sr.unit.unit}.json", [])
    ]
    resolve = Resolver(real_names)
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
            fh.write(substitute(e["text"], exhibit, resolve) + "\n\n")
    with (d / "clusters.md").open("w", encoding="utf-8") as fh:
        for dt, key, grp in clusters:
            fh.write(f"##### CLUSTER {dt} | {key} ({len(grp)} fragments)\n")
            fh.write("\n---FRAGMENT-BREAK---\n".join(substitute(g["text"], exhibit, resolve) for g in grp) + "\n\n")
    for fname, bucket in buckets.items():
        (d / fname).write_text("\n\n".join(bucket), encoding="utf-8")
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
