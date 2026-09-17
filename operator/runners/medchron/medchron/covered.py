"""What a delivered chronology COVERED, in document ids. $0.

Routine 11 has one product in two request modes: a run builds the chronology,
an update brings a delivered one current by reading only what the delivery did
not cover (agreement Exhibit A, "The medical chronology (routine 11)"). That
sentence needs a record, and until 2026-09-17 no layer held one: the ledger row
carried counts, the runner stored the files it DELIVERED, and the delivered
document's review note names unread documents by name, not by id. So an update
either re-read the whole file or guessed.

The record comes from the coverage gate's own accounting, deliberately, and not
from the download log. `raw_manifest.jsonl` marks a row `ok` when bytes arrived
and hashed, which says nothing about whether the document reached the
chronology: a glyph-junk scan, a name-excluded vendor order form and an orphan
are all `ok`. Sourcing coverage from that flag would mark a contentless scan
covered and drop those records from every later update, permanently and
silently, in a litigation document. The coverage gate already answers the right
question — EVERY pulled file is cited in the delivered document or carries a
stated exclusion reason (`stages/coverage.py`) — so this module reads the same
artifacts and reports the same two sets, keyed by document id:

    covered    cited in the delivered document, a byte-duplicate of something
               cited, in the authored billing-chart set, or excluded by an
               authored name rule: accounted for, and an update skips it.
    uncovered  the composer read it and found nothing citable, the unit marked
               it `compose_skip` (a contentless or unreadable scan), retrieval
               failed, or it is a documented orphan: NOT in the delivered
               record, so an update reads it again by default.

An id in neither set would be an unexplained loss, which the coverage gate
itself refuses to ship, so the two sets sum to every pulled document and the
broker refuses a payload where they do not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .stages.base import read_json, read_jsonl
from .stages.coverage import (
    EXT_OK,
    cited_exhibits,
    classify_name,
    composer_dispositions,
    exclusions,
    file_key,
)
from .stages.units import billing_stem


def _cited_names(slug_dir: Path, unit: str, run_dir: Path) -> set[str]:
    """File names the delivered document actually cites, via the page map when
    one exists (it maps exhibit -> files) and the exhibit map otherwise."""
    body = ""
    for cand in ("entries_scoped_final.md", "entries_scoped.md", "entries_final.md"):
        if (run_dir / cand).is_file():
            body = (run_dir / cand).read_text(encoding="utf-8")
            break
    if not body:
        return set()
    cited_ex, _hollow = cited_exhibits(body)
    page_map = read_json(slug_dir / "out" / unit / "page_map.json", None)
    if page_map is not None:
        return {f["file"] for e in page_map if e["exhibit"] in cited_ex for f in e["files"]}
    return {n for n, num in read_json(run_dir / "exhibit_map.json", {}).items() if num in cited_ex}


def covered_sets(slug_dir: Path, unit: str, cfg: Any) -> dict[str, Any]:
    """`{"covered": [ids], "uncovered": [ids], "pulled": N}` for one unit.

    Pure reads of the run's own artifacts, so it costs nothing and cannot be
    satisfied by a stage claiming success. Ids come from the unit file rows;
    a pulled row the unit set never adopted (an orphan, a name-excluded file)
    has no unit row, so its id comes from the manifest.
    """
    run_dir = slug_dir / "runs" / unit
    unit_files = read_json(slug_dir / "units" / f"{unit}.json", [])
    by_name: dict[str, dict[str, Any]] = {file_key(f): f for f in unit_files}
    id_of: dict[str, str] = {file_key(f): str(f["id"]) for f in unit_files if f.get("id")}
    for row in read_jsonl(slug_dir / "raw_manifest.jsonl"):
        # Every manifest row with an id, including a FAILED retrieval: a document
        # whose bytes never arrived is exactly what an update must read next time,
        # so leaving it unmapped would silently drop it from both sets.
        if row.get("id"):
            id_of.setdefault(file_key(row), str(row["id"]))

    pulled: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    failed: set[str] = set()
    for row in read_jsonl(slug_dir / "raw_manifest.jsonl"):
        key = file_key(row)
        if not row.get("ok"):
            failed.add(key)
            continue
        if row.get("duplicate_of"):
            duplicates.add(key)
        pulled[key] = row

    orphans = {
        o.get("name") for o in (read_json(slug_dir / "orphans.json", {}) or {}).get("orphans") or [] if o.get("name")
    }
    billing_spec = read_json(slug_dir / "billing_docs.json", [])
    billing_names = {
        b["name"] for b in (billing_spec.get("docs") if isinstance(billing_spec, dict) else billing_spec) or []
    }
    rules = exclusions(cfg)
    said = composer_dispositions(run_dir, unit_files)
    cited = _cited_names(slug_dir, unit, run_dir)

    covered: set[str] = set()
    uncovered: set[str] = set()
    for key in sorted(set(pulled) | failed):
        if key in failed:
            uncovered.add(key)
            continue
        if key in cited or key in duplicates:
            covered.add(key)
            continue
        row = by_name.get(key, pulled.get(key, {}))
        if row.get("compose_skip"):
            uncovered.add(key)
            continue
        if key in orphans:
            uncovered.add(key)
            continue
        if billing_stem(key) in billing_names or key in billing_names:
            covered.add(key)
            continue
        if classify_name(key, rules) or (pulled.get(key, {}).get("ext") or "").lower() not in EXT_OK:
            covered.add(key)
            continue
        disposition = said.get(key)
        if disposition and disposition.startswith("composer:"):
            uncovered.add(key)
            continue
        # A file the delivered document neither cites nor explains cannot ship
        # (the coverage gate holds the run), so reaching here means the gate was
        # bypassed. Treat it as uncovered: an update re-reads it.
        uncovered.add(key)

    def ids(names: set[str]) -> list[str]:
        return sorted({id_of[n] for n in names if n in id_of})

    covered_ids, uncovered_ids = ids(covered), ids(uncovered)
    # An id that landed in both sets (two rows sharing an id) would let an update
    # decide either way, so the uncovered reading wins: re-reading is recoverable,
    # skipping a record is not.
    covered_ids = [i for i in covered_ids if i not in set(uncovered_ids)]
    return {
        "covered": covered_ids,
        "uncovered": uncovered_ids,
        "pulled": len(covered_ids) + len(uncovered_ids),
    }


def covered_payload(slug_dir: Path, units: list[str], cfg: Any) -> dict[str, Any] | None:
    """The union across the units this job delivered, or None when the run left
    no coverage artifacts to read (nothing is guessed: the skill reports the
    record as unknown and submits no update)."""
    covered: set[str] = set()
    uncovered: set[str] = set()
    seen = False
    for unit in units:
        if not (slug_dir / "units" / f"{unit}.json").is_file():
            continue
        seen = True
        sets = covered_sets(slug_dir, unit, cfg)
        covered |= set(sets["covered"])
        uncovered |= set(sets["uncovered"])
    if not seen:
        return None
    covered -= uncovered
    return {
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
        "pulled": len(covered) + len(uncovered),
    }


def write_debug(path: Path, payload: dict[str, Any]) -> None:
    """The payload as sent, beside the run, for a later hand-reconstruction."""
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")


def merge_covered(outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Union the units' coverage records from a job's outcomes, or None when no
    unit reported one (the daemon sends nothing rather than an empty claim).

    A document uncovered in ANY unit stays uncovered in the union: re-reading it
    in an update is recoverable, and dropping a record from a litigation
    chronology is not.
    """
    covered: set[str] = set()
    uncovered: set[str] = set()
    seen = False
    for o in outcomes:
        rec = o.get("covered") if isinstance(o, dict) else None
        if not isinstance(rec, dict):
            continue
        seen = True
        covered |= {str(x) for x in rec.get("covered") or []}
        uncovered |= {str(x) for x in rec.get("uncovered") or []}
    if not seen:
        return None
    covered -= uncovered
    return {
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
        "pulled": len(covered) + len(uncovered),
    }


def delivery_fields(worst: dict[str, Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """The ledger fields a DELIVERED job carries beyond its counts.

    `folder_id` and the delivered file list come from the upload stage's
    read-back; `covered` is the union of the units' coverage records and is
    OMITTED when no unit reported one, because a job with no record must read as
    unknown rather than as "covered nothing" (the two send an update in opposite
    directions). The broker refuses a malformed record outright.
    """
    fields: dict[str, Any] = {
        "folder_id": worst.get("folder_id"),
        "delivery": {"files": list(worst.get("files") or [])},
    }
    merged = merge_covered(outcomes)
    if merged is not None:
        fields["covered"] = merged
    return fields
