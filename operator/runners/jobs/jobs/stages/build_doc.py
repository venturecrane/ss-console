"""`build_doc`: the final chronology markdown in the firm's section order,
ready for the renderer. $0.

Sections: Title / Treatment Timeline (provider table) / Diagnostic Highlights
(ICD table) / Medical Chronology (the entries, with the Prior Medical History
block and the scope note at its head) / Exhibit List / Records Reviewed and
Limitations. Tables are computed FROM THE ENTRIES in code, never by a model,
so they cannot disagree with the body; the provider table is facility level
through the same canon the exhibits use. A diagnosis code is an audited claim
(``icd_claims``): the document carries its claim for the audit, and the row the
firm reads is settled from what the audit kept.

Refuses (exit 1) when text precedes the first dated entry and is not a Prior
Medical History heading: that text would be dropped by the entry parser and
the section would be missing from the deliverable with nothing downstream
able to see it (three delivered chronologies shipped that way).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..medchron import icd_tables, meta_markers
from ..medchron.update import icd
from . import assemble, icd_claims
from .base import StageRun, read_json
from .group import Canon
from .limitations import section as limitations_section

ENTRY = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")
PRIOR_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*Prior Medical History\s*(?:\*\*)?\s*$", re.IGNORECASE)


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
    """{dotless code: (first date, display code, provider)} from what the maps
    record, restricted to dates the document carries."""
    return icd.index_codes(assemble.code_rows(run_dir), kept_dates)


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
    text = meta_markers.strip(body_src.read_text(encoding="utf-8"))  # the pipeline's own notes, before the audit
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
    try:
        table, claims = icd_claims.build(sr, rd, [e["text"] for e in entries])
    except icd_tables.TablesMissing as exc:
        sr.log(str(exc))
        return 1
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
    o += ["", "## Diagnostic Highlights\n", *table]
    o += ["", "## Medical Chronology\n"]
    if prior_block:
        o.append(prior_block + "\n")
    if note:
        o.append(note + "\n")
    o += [c + "\n" for c in claims]
    o += [e["text"] + "\n" for e in entries]
    o += ["## Exhibit List\n", "| Exhibit No. | Description |"]
    o += [f"| {e['exhibit']} | {e['title'].split(' - ', 1)[1]} |" for e in pm]
    o += limitations_section(d, unit, sr.log, sr.cfg)
    out = "\n".join(o)
    (rd / "final-chronology.md").write_text(out, encoding="utf-8")
    pre = sum(1 for e in entries if e["iso"] < sr.job.incident_date)
    sr.log(
        f"{unit}: {len(entries)} entries ({pre} pre-incident), {len(rows)} providers, {len(table) - 1} ICD codes, "
        f"{len(pm)} exhibits, {len(out.split())} words"
    )
    return 0
