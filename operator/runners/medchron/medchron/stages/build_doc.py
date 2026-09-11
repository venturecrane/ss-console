"""`build_doc`: the final chronology markdown in the firm's section order,
ready for the renderer. $0.

Sections: Title / Treatment Timeline (provider table) / Diagnostic Highlights
(ICD table) / Medical Chronology (the entries, with the Prior Medical History
block and the scope note at its head) / Exhibit List / Records Reviewed and
Limitations. Tables are computed FROM THE ENTRIES in code, never by a model,
so they cannot disagree with the body; ICD descriptors come from the vendored
CMS tables (a code the tables do not carry is left blank); the provider table
is facility level through the same canon the exhibits use.

Refuses (exit 1) when text precedes the first dated entry and is not a Prior
Medical History heading: that text would be dropped by the entry parser and
the section would be missing from the deliverable with nothing downstream
able to see it (three delivered chronologies shipped that way).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .. import icd_tables
from .base import StageRun, read_json
from .group import Canon, index_rows
from .limitations import section as limitations_section

ENTRY = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")
PRIOR_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*Prior Medical History\s*(?:\*\*)?\s*$", re.IGNORECASE)
# An ICD-10-CM code is a letter, exactly two digits, then at most four more
# characters (with or without the dot); a shape that admitted three digits
# before the decimal shipped garbled codes into a client's table.
ICD_SHAPE = re.compile(r"[A-Z]\d{2}(\.[0-9A-Z]{1,4}|[0-9A-Z]{0,4})|\d{3}(\.\d{1,2})?|[VE]\d{2,3}(\.\d)?")


class BuildRefusal(RuntimeError):
    pass


def split_prior(text: str) -> tuple[str, str]:
    """(prior_block, entries_text). The heading is MODEL-GENERATED, so its
    form varies ("Prior Medical History", "# ...", "**...**"); match any and
    normalise to the bare text. Anything else ahead of the first entry refuses."""
    m = re.search(r"(?m)^\d{2}/\d{2}/\d{4}\s*$", text)
    head = text[: m.start()].strip() if m else ""
    if not head:
        return "", text
    lines = head.splitlines()
    if not PRIOR_HEAD.match(lines[0]):
        raise BuildRefusal(
            f"{len(head)} chars precede the first dated entry but the first line is not a Prior Medical "
            f"History heading: {lines[0][:120]!r}; this text would be dropped from the deliverable"
        )
    return "\n".join(["Prior Medical History"] + lines[1:]).strip(), text[m.start() :] if m else ""


def parse_entries(text: str) -> list[dict[str, Any]]:
    entries = []
    for e in ENTRY.split(text):
        e = e.strip()
        m = DATE.match(e)
        if not m:
            continue
        lines = e.splitlines()
        head = lines[1] if len(lines) > 1 else ""
        prov = head.split("|")[0].strip() if "|" in head else head.strip()
        first_cite = CITE.search(e)
        entries.append(
            {
                "iso": f"{m.group(3)}-{m.group(1)}-{m.group(2)}",
                "provider": prov,
                "text": e,
                "ref": first_cite.group(0)[1:-1] if first_cite else "",
            }
        )
    entries.sort(key=lambda x: x["iso"])
    return entries


def provider_rows(entries: list[dict[str, Any]], canon: Canon) -> list[tuple]:
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"dates": [], "ref": ""})
    for e in entries:
        a = agg[canon(e["provider"])]
        a["dates"].append(e["iso"])
        if not a["ref"]:
            a["ref"] = e["ref"]
    rows = []
    for prov, a in agg.items():
        ds = sorted(a["dates"])
        period = f"{ds[0][5:7]}/{ds[0][8:10]}/{ds[0][:4]}" + (
            f" - {ds[-1][5:7]}/{ds[-1][8:10]}/{ds[-1][:4]}" if ds[-1] != ds[0] else ""
        )
        rows.append((ds[0], prov, period, len(ds), a["ref"]))
    return sorted(rows)


def icd_codes(run_dir, kept_dates: set[str]) -> dict[str, tuple[str, str, str]]:
    """{dotless code: (first date, display code, provider)} from the INDEX
    blocks, restricted to dates the document carries."""
    _dates, provs = index_rows(run_dir)
    icd: dict[str, tuple[str, str, str]] = {}
    for p in run_dir.iterdir():
        if not re.match(r"map-\d+(-\d+)?\.md$", p.name):
            continue
        m = re.search(r"##\s*INDEX\s*\n(.*?)(?=\n##\s|\Z)", p.read_text(encoding="utf-8"), re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            cols = [x.strip() for x in line.split("|")]
            if len(cols) < 3 or not re.match(r"^\d{4}-\d{2}-\d{2}$", cols[0]) or cols[0] not in kept_dates:
                continue
            for code in re.split(r"[,/;]\s*", cols[2]):
                code = re.sub(r"\s*\(.*?\)", "", code).strip()
                code = re.sub(r"(?i)\s+(?:as recorded|history).*$", "", code).strip()
                if not code or code == "--" or not ICD_SHAPE.fullmatch(code):
                    continue
                key = code.replace(".", "")
                prior = icd.get(key)
                if prior is None or cols[0] < prior[0]:
                    icd[key] = (cols[0], code, cols[1] if len(cols) > 1 else "")
                elif cols[0] == prior[0] and "." in code and "." not in prior[1]:
                    icd[key] = (prior[0], code, prior[2])
    return icd


def _toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    unit = sr.unit.unit
    rd = d / "runs" / unit
    pm = read_json(d / "out" / unit / "page_map.json", [])
    body_src = rd / "entries_scoped_final.md"
    if not body_src.is_file():
        body_src = rd / "entries_final.md"
    text = body_src.read_text(encoding="utf-8")
    prior_block = ""
    if body_src.name == "entries_scoped_final.md":
        try:
            prior_block, text = split_prior(text)
        except BuildRefusal as exc:
            sr.log(f"REFUSING TO BUILD: {exc}")
            return 1
    entries = parse_entries(text)
    canon = Canon(sr.cfg)
    rows = provider_rows(entries, canon)
    icd = icd_codes(rd, {e["iso"] for e in entries})
    desc: dict[str, str] = {}
    if icd:
        try:
            tables = icd_tables.load(sr.job.install_root)
        except icd_tables.TablesMissing as exc:
            sr.log(str(exc))
            return 1
        blank = []
        for _key, (_d, code, _prov) in sorted(icd.items()):
            label = icd_tables.describe(code, tables)
            (desc.__setitem__(code, label) if label else blank.append(code))
        (rd / "icd_descriptors.json").write_text(json.dumps(desc, indent=1), encoding="utf-8")
        sr.log(
            f"  ICD descriptors: {len(desc)}/{len(icd)} resolved, {len(blank)} left blank"
            + (f": {', '.join(blank)}" if blank else "")
        )
    note = (
        (rd / "preincident_note.txt").read_text(encoding="utf-8").strip()
        if (rd / "preincident_note.txt").is_file()
        else ""
    )

    o = [
        f"{sr.unit.client_name} - Medical Chronology\n",
        "## Treatment Timeline\n",
        "| Medical Provider | Treatment Period | Visits | Reference |",
    ]
    o += [f"| {prov} | {period} | {n} | {ref} |" for _, prov, period, n, ref in rows]
    o += ["", "## Diagnostic Highlights\n", "| ICD Code | Description | First Diagnosed | Reference |"]
    for _key, (first, code, prov) in sorted(icd.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        same_day = [e for e in entries if e["iso"] == first]
        best = max(same_day, key=lambda e: len(_toks(e.get("provider")) & _toks(prov)), default=None)
        o.append(
            f"| {code} | {desc.get(code, '')} | {first[5:7]}/{first[8:10]}/{first[:4]} | {best['ref'] if best else ''} |"
        )
    o += ["", "## Medical Chronology\n"]
    if prior_block:
        o.append(prior_block + "\n")
    if note:
        o.append(note + "\n")
    o += [e["text"] + "\n" for e in entries]
    o += ["## Exhibit List\n", "| Exhibit No. | Description |"]
    o += [f"| {e['exhibit']} | {e['title'].split(' - ', 1)[1]} |" for e in pm]
    o += limitations_section(d, unit, sr.log)
    out = "\n".join(o)
    (rd / "final-chronology.md").write_text(out, encoding="utf-8")
    pre = sum(1 for e in entries if e["iso"] < sr.job.incident_date)
    sr.log(
        f"{unit}: {len(entries)} entries ({pre} pre-incident), {len(rows)} providers, {len(icd)} ICD codes, "
        f"{len(pm)} exhibits, {len(out.split())} words"
    )
    return 0
