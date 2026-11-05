"""What code counts against what the composer reported: the $0 proof of ADR 0104
W8, read by ``jobs rehearse``.

The coverage gate now reads per-file entry counts from the parsed entries. A
kept job's maps still carry the composer's own `entries: N` and `billing-dates:
N`, so on such a job the two can be laid side by side before the change reaches
a client's chronology. Every difference is named, none is capped, and the whole
table is written to ``coverage-counts.json`` in the rehearsal's copy.

The same read prices what the prompt stopped asking for: the share of the
composed output the removed blocks took, which is a share of what compose paid
for output on this job (``ledger.report`` carries the dollars).

An answer key, written before the change by someone reading the files, is
checked file by file::

    {"units": {"<unit>": {"files": {"<file name>": {"entries": 3, "billing_dates": 2}}}}}

``billing_dates`` is optional. A file the key does not list is reported as not
checked, never as a pass.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import assemble
from .base import read_json, read_jsonl
from .billing_chart import dt
from .coverage import NO_ENCOUNTER_REASON, SEEN_LINE, entry_counts, file_key
from .units import billing_stem

#: The blocks the prompt no longer asks for (the new shape's FILES-SEEN lines
#: stay, so only the old shape's lines count as removed).
REMOVED_BLOCKS = ("INDEX", "BILLING-DATES", re.escape("CONFLICTS / REFERENCED-BUT-ABSENT"))
REPORT = "coverage-counts.json"


def composer_said(run_dir: Path, unit_files: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per file, what its FILES-SEEN lines report: entries, billing dates, reason."""
    id_to_name = {f["id"]: file_key(f) for f in unit_files}
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(run_dir.glob("map-*.md")):
        for m in SEEN_LINE.finditer(p.read_text(encoding="utf-8")):
            name = id_to_name.get(m.group("id") or "", m.group("name").strip())
            what = m.group("what")
            row = out.setdefault(name, {"entries": None, "billing_dates": None, "reason": None})
            e, b = re.search(r"entries:\s*(\d+)", what), re.search(r"billing-dates:\s*(\d+)", what)
            if e:
                row["entries"] = (row["entries"] or 0) + int(e.group(1))
            if b:
                row["billing_dates"] = (row["billing_dates"] or 0) + int(b.group(1))
            if what.startswith("nothing extractable:"):
                row["reason"] = what[len("nothing extractable:") :].strip()
    return out


def billing_stage_dates(slug_dir: Path) -> dict[str, int]:
    """Per billing_extract row, the distinct service dates the billing stage holds."""
    out: dict[str, int] = {}
    for row in read_jsonl(slug_dir / "billing_extract.jsonl"):
        days: set[str] = set()
        for c in row.get("chunks") or []:
            if not isinstance(c, dict) or "FAILED_PAGE" in c:
                continue
            items = [i for i in c.get("line_items") or [] if isinstance(i, dict)]
            got = [dt(i.get("date")) for i in items] or [dt(c.get("date_first")), dt(c.get("date_last"))]
            days |= {d for d in got if d}
        out[str(row.get("file"))] = len(days)
    return out


def block_share(run_dir: Path) -> dict[str, int]:
    """Characters of composed output, in all and per removed block."""
    out = {"total": 0, **dict.fromkeys(("INDEX", "BILLING-DATES", "CONFLICTS", "FILES-SEEN"), 0)}
    for p in sorted(run_dir.glob("map-*.md")):
        txt = p.read_text(encoding="utf-8")
        out["total"] += len(txt)
        for name in REMOVED_BLOCKS:
            short = "CONFLICTS" if name.startswith("CONFLICTS") else name
            out[short] += len(assemble.section(txt, name))
        out["FILES-SEEN"] += len(assemble.section(txt, "FILES-SEEN"))
    return out


def _table(slug_dir: Path, unit: str) -> list[dict[str, Any]]:
    run_dir = slug_dir / "runs" / unit
    unit_files = read_json(slug_dir / "units" / f"{unit}.json", []) or []
    code = assemble.file_entry_counts(run_dir, unit_files)
    gate = entry_counts(run_dir, unit_files)
    said = composer_said(run_dir, unit_files)
    billing = billing_stage_dates(slug_dir)
    rows = []
    for f in unit_files:
        name = file_key(f)
        s = said.get(name) or {"entries": None, "billing_dates": None, "reason": None}
        rows.append(
            {
                "file": name,
                "entries_code": code.get(name, 0),
                "entries_composer": s["entries"],
                "entries_gate": gate.get(name),
                "billing_dates_composer": s["billing_dates"],
                "billing_dates_stage": billing.get(billing_stem(name), billing.get(name, 0)),
                "reason": s["reason"],
                "composed": bool(f.get("compose", True)) and not f.get("compose_skip"),
            }
        )
    return rows


def _differences(r: dict[str, Any]) -> list[str]:
    out = []
    composer, counted = r["entries_composer"], r["entries_code"]
    if composer is not None and composer != counted:
        out.append(f"composer entries: {composer}, code counts {counted}")
    if r["billing_dates_composer"] is not None and r["billing_dates_composer"] != r["billing_dates_stage"]:
        out.append(
            f"composer billing-dates: {r['billing_dates_composer']}, billing stage holds {r['billing_dates_stage']}"
        )
    silent = composer is None and r["billing_dates_composer"] is None and r["reason"] is None
    if silent and counted == 0 and r["composed"]:
        out.append(
            "no entry cites it and the composer gave no account of it: the gate holds it unless a rule explains it"
        )
    return out


def _key_lines(rows: list[dict[str, Any]], key: dict[str, Any] | None, unit: str) -> list[str]:
    if key is None:
        return []
    want = ((key.get("units") or {}).get(unit) or {}).get("files") or {}
    by_file = {r["file"]: r for r in rows}
    out = []
    missed = 0
    for name, exp in sorted(want.items()):
        r = by_file.get(name)
        if r is None:
            out.append(f"KEY MISS {name}: not a file of this unit")
            missed += 1
            continue
        bad = []
        if "entries" in exp and exp["entries"] != r["entries_code"]:
            bad.append(f"entries key {exp['entries']}, code {r['entries_code']}")
        if "billing_dates" in exp and exp["billing_dates"] != r["billing_dates_stage"]:
            bad.append(f"billing dates key {exp['billing_dates']}, stage {r['billing_dates_stage']}")
        if bad:
            out.append(f"KEY MISS {name}: " + "; ".join(bad))
            missed += 1
    unlisted = sorted(set(by_file) - set(want))
    out += [f"KEY NOT CHECKED {name}" for name in unlisted]
    out.insert(0, f"answer key: {len(want)} file(s) checked, {missed} missed, {len(unlisted)} not listed")
    return out


def report(slug_dir: Path, unit: str, key: dict[str, Any] | None = None) -> list[str]:
    """The report lines, and the full table beside the run's artifacts. Lines
    say `no maps` rather than a clean zero when composition has not run: a
    check that had nothing to read has not passed."""
    run_dir = slug_dir / "runs" / unit
    if not any(run_dir.glob("map-*.md")):
        return ["no composed maps on this job; nothing to compare"]
    rows = _table(slug_dir, unit)
    share = block_share(run_dir)
    (run_dir / REPORT).write_text(json.dumps({"files": rows, "blocks": share}, indent=1), encoding="utf-8")
    differing = [(r["file"], d) for r in rows for d in _differences(r)]
    no_encounter = sum(1 for r in rows if r["reason"] == NO_ENCOUNTER_REASON)
    head = (
        f"{len(rows)} unit file(s); {sum(1 for r in rows if r['entries_code'])} with entries by code; "
        f"{sum(1 for r in rows if r['entries_composer'] is not None)} with a composer count; "
        f"{no_encounter} stated no dated encounter; {len(differing)} difference(s)"
    )
    lines = [head, *(f"DIFFERS {name}: {what}" for name, what in differing)]
    removed = share["INDEX"] + share["BILLING-DATES"] + share["CONFLICTS"]
    pct = 100 * removed / share["total"] if share["total"] else 0.0
    lines.append(
        f"composed output {share['total']} chars; INDEX {share['INDEX']}, BILLING-DATES {share['BILLING-DATES']}, "
        f"CONFLICTS {share['CONFLICTS']}, FILES-SEEN {share['FILES-SEEN']}; the first three are {pct:.1f}% of it"
    )
    return lines + _key_lines(rows, key, unit)
