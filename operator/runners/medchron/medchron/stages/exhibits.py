"""`exhibits`: build the exhibit SET and remap the chronology's citations
onto it. $0, mechanical.

An exhibit is one provider's source records merged in date order: no cover
page, no bates stamp, no added structure, named by the firm's convention
(`Exhibit <n> - <Provider> - <MM-DD-YYYY> - <MM-DD-YYYY> (<Record Type>).pdf`).
Citations in the assembled chronology point at per-file exhibit numbers;
this step merges each provider's files into one PDF and rewrites every
citation to `(Exhibit <group> - p. <offset + original page>)`, so a reader
who opens the exhibit lands on the page the sentence came from.

ONE EXHIBIT PER PROVIDER: the firm reads an exhibit as a provider's record
set (a first cut split bulk productions, inferring a rule from one instance
in the exemplar, and the firm asked why two providers had been split).
Exhibits are built only for provider groups the chronology actually cites;
a citation in the text overrides the undated-lane presumption, a cited
sentinel lane refuses, and a citation that cannot be remapped is exit 1.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import StageRun, read_json, read_jsonl
from .group import index_rows

CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")
BILL = re.compile(r"(?i)\bbill|ledger|invoice|statement|charges\b")
CERT = re.compile(r"(?i)certif")
EXHIBIT_FILE = re.compile(r"Exhibit \d+ - .*\.pdf(\.orig|\.stripped)?$")


def us(d: str) -> str:
    return f"{d[5:7]}-{d[8:10]}-{d[0:4]}" if d and d[0] != "9" else ""


def _clear_stale(out: Path) -> None:
    """A rebuild that changes an exhibit's TITLE otherwise leaves the old file
    beside the new one, and glob-based downstream stages classify the stale
    one. Only exhibit pdfs and their strip derivatives, never worksheets."""
    for p in out.iterdir():
        if EXHIBIT_FILE.match(p.name):
            p.unlink()


def remap_citations(text: str, remap: dict[int, tuple[int, int]]) -> tuple[str, set[int]]:
    missing: set[int] = set()

    def sub(m: re.Match) -> str:
        old = int(m.group(1))
        if old not in remap:
            missing.add(old)
            return m.group(0)
        new, off = remap[old]
        if not m.group(2):
            return f"(Exhibit {new})"
        pages = []
        for tok in re.split(r",\s*", m.group(2).strip()):
            rng = re.match(r"(\d+)\s*-\s*(\d+)$", tok.strip())
            if rng:
                pages.append(f"{int(rng.group(1)) + off - 1}-{int(rng.group(2)) + off - 1}")
            elif tok.strip().isdigit():
                pages.append(str(int(tok.strip()) + off - 1))
        return f"(Exhibit {new} - p. {', '.join(pages)})" if pages else f"(Exhibit {new})"

    return CITE.sub(sub, text), missing


def run(sr: StageRun) -> int:
    from pypdf import PdfReader, PdfWriter

    d = sr.slug_dir
    unit = sr.unit.unit
    rd = d / "runs" / unit
    out = d / "out" / unit
    out.mkdir(parents=True, exist_ok=True)
    _clear_stale(out)
    groups = read_json(d / "groups" / f"{unit}.json", [])
    exmap: dict[str, int] = read_json(rd / "exhibit_map.json", {})
    files = read_json(d / "units" / f"{unit}.json", [])
    byname = {f["name"] + (f.get("ext") or ""): f for f in files}
    raw = {r["id"]: r for r in read_jsonl(d / "raw_manifest.jsonl") if r.get("ok")}
    idx_dates, _ = index_rows(rd)
    scoped, merged = rd / "entries_scoped.md", rd / "merged.md"
    if not scoped.is_file() and merged.is_file() and merged.stat().st_size > 50:
        sr.log("REFUSING TO BUILD: merged.md holds merged cluster entries but entries_scoped.md does not exist")
        return 1
    src_text = (scoped if scoped.is_file() else rd / "entries.md").read_text(encoding="utf-8")
    cited_old = {int(m.group(1)) for m in re.finditer(r"\(Exhibit (\d+)", src_text)}

    def text_cited(g: dict[str, Any]) -> bool:
        ids = set(g["file_ids"])
        return any(old in cited_old for name, old in exmap.items() if (byname.get(name) or {}).get("id") in ids)

    live = []
    for g in groups:
        if not any((byname.get(n) or {}).get("id") in set(g["file_ids"]) for n in exmap):
            continue
        if g.get("exhibit", True):
            live.append(g)
        elif text_cited(g):
            if g["provider"].startswith("(unattributed"):
                sr.log("REFUSING TO BUILD: an entry cites a file in the unresolved sentinel lane")
                return 1
            sr.log(f"  !! lane '{g['provider']}' has no dated entries but IS cited; included")
            live.append(g)
    live.sort(key=lambda g: g["first"])

    page_map: list[dict[str, Any]] = []
    remap: dict[int, tuple[int, int]] = {}
    n = 0
    for g in live:
        gfiles = []
        for name, old in sorted(exmap.items(), key=lambda kv: kv[1]):
            f = byname.get(name)
            if not f or f["id"] not in g["file_ids"]:
                continue
            ds = sorted(idx_dates.get(name, []))
            gfiles.append((ds[0] if ds else "9999", name, old, f))
        gfiles.sort(key=lambda x: (x[0], x[2]))
        if not gfiles:
            continue
        n += 1
        w = PdfWriter()
        entries: list[dict[str, Any]] = []
        cursor = 1
        for _first, name, old, f in gfiles:
            rec = raw.get(f["id"])
            if not rec or (f.get("ext") or "").lower() != ".pdf":
                continue
            try:
                r = PdfReader(rec["path"])
            except Exception as exc:  # noqa: BLE001 - an unreadable PDF is recorded as that file's error in the exhibit list and the loop continues
                entries.append({"file": name, "error": str(exc)[:100]})
                continue
            for pg in r.pages:
                w.add_page(pg)
            remap[old] = (n, cursor)
            entries.append({"file": name, "old_exhibit": old, "start_page": cursor, "pages": len(r.pages)})
            cursor += len(r.pages)
        if cursor == 1:
            n -= 1
            continue
        names = " ".join(e["file"] for e in entries)
        rt = (
            "Certified Medical Records"
            if CERT.search(names)
            else "Medical Records & Bills"
            if BILL.search(names)
            else "Medical Records"
        )
        ud = sorted({dt for _f, name, _o, _x in gfiles for dt in idx_dates.get(name, [])})
        span = us(ud[0]) if ud else us(g["first"])
        if len(ud) > 1 and ud[-1] != ud[0]:
            span += f" - {us(ud[-1])}"
        title = f"Exhibit {n} - {g['provider']} - {span} ({rt})"
        with (out / (re.sub(r"[/:]", "-", title) + ".pdf")).open("wb") as fh:
            w.write(fh)
        page_map.append(
            {
                "exhibit": n,
                "title": title,
                "provider": g["provider"],
                "record_type": rt,
                "total_pages": cursor - 1,
                "files": entries,
            }
        )
        sr.log(f"Exhibit {n}: {cursor - 1:5d} pp  {g['provider'][:44]}")

    remapped, missing = remap_citations(src_text, remap)
    (rd / "entries_final.md").write_text(remapped, encoding="utf-8")
    (out / "page_map.json").write_text(json.dumps(page_map, indent=1), encoding="utf-8")
    (rd / "exhibit_remap.json").write_text(
        json.dumps({str(k): v for k, v in remap.items()}, indent=1), encoding="utf-8"
    )
    uncited = [(g["provider"], len(g["file_ids"])) for g in groups if g not in live]
    sr.log(f"{len(page_map)} exhibits, {sum(e['total_pages'] for e in page_map)} pages -> {out}")
    if missing:
        sr.log(f"citations to old exhibit(s) {sorted(missing)} could not be remapped")
        return 1
    if uncited:
        sr.log("not exhibited (no cited records): " + ", ".join(f"{p} x{c}" for p, c in uncited[:8]))
    return 0
