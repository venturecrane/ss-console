"""An update reuses the delivered run's finding on a file instead of paying to
read it again.

WHY (2026-10-25, the Captain: "make sure we're leveraging any previous work
... where we don't have to reread things"). An update reads the files a
delivered chronology did not cite. The delivered run had read them: its
composer wrote, per file, why nothing in it was citable ("nothing
extractable: scene photograph, no record text"), or the run set the file
aside on a stated rule before composing ("billing-only source ...", "read in
full; N words of text; nothing in it was cited"). On the first real update,
about 200 of 222 requested files carried such a finding; the update would
have paid the model to compose them again and reach the same answer. The
delivered chronology already relied on those findings (its coverage gate
accepted each one).

The rule. A requested file whose bytes are the same (sha256) as a file in a
DELIVERED job on the same matter (``vision_seed.delivered_same_matter``) and
for which that job's unit recorded a finding is decided by that finding: it
is set aside before the paid reading, under ``PREFIX + finding``, and the
outcome record and the records review name the finding. Only the composer's
"nothing extractable" accounts and a stated ``compose_skip`` count; a file
that gave entries, or one the composer passed over without a reason, has no
finding here and is read.
"""

from __future__ import annotations

from typing import Any

from .. import unopened
from ..medchron.covered import cited_names
from .base import StageRun, read_json, read_jsonl
from .coverage import composer_dispositions, file_key
from .vision_seed import delivered_same_matter

PREFIX = "read for the delivered chronology: "


def _hashes(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {str(r["id"]): str(r["sha256"]) for r in rows if r.get("ok") and r.get("id") and r.get("sha256")}


def decided(sr: StageRun, ids: set[str]) -> dict[str, str]:
    """{file id: the delivered run's finding} for the requested ``ids`` it covers."""
    mine = _hashes(read_jsonl(sr.slug_dir / "raw_manifest.jsonl"))
    out: dict[str, str] = {}
    for src in delivered_same_matter(getattr(sr, "job", None), sr.slug_dir.name):
        unit_files = read_json(src / "units" / f"{sr.unit.unit}.json", []) or []
        if not isinstance(unit_files, list):
            continue
        theirs = _hashes(read_jsonl(src / "raw_manifest.jsonl"))
        said = composer_dispositions(src / "runs" / sr.unit.unit, unit_files)
        for f in unit_files:
            rid = str(f.get("id") or "")
            if rid not in ids or rid in out or not mine.get(rid) or mine.get(rid) != theirs.get(rid):
                continue
            why = said.get(file_key(f)) or (f"compose skipped: {f['compose_skip']}" if f.get("compose_skip") else "")
            if why:
                out[rid] = why
        # A file the run set aside before composing, with its stated reason
        # (``orphans.json``: an identity document, a records request, ...).
        # A locked one is not settled: the update discloses it and the next
        # one reads it once it opens (``update_select``, ``unopened``).
        orphans = {str(x.get("name")): str(x.get("reason")) for x in _orphans(src)}
        for r in read_jsonl(src / "raw_manifest.jsonl"):
            rid, why = str(r.get("id") or ""), orphans.get(file_key(r)) if r.get("name") else None
            same = bool(mine.get(rid)) and mine.get(rid) == theirs.get(rid)
            if rid in ids and rid not in out and why and why != unopened.REASON and same:
                out[rid] = f"set aside: {why}"
    return out


def cited_by_id(slug_dir: Any, unit: str) -> dict[str, str]:
    """``{file id: sha256}`` of every SOURCE file the chronology in this job's
    ``slug_dir`` cites for ``unit``: the unit file rows its page map names,
    hashed by its own manifest. What a delivered job records in its ledger row
    (``covered.delivery_fields``, ``delivery.cited_sources``) and what
    ``cited_sources`` reads back from a delivered job dir still on the volume."""
    unit_files = read_json(slug_dir / "units" / f"{unit}.json", []) or []
    if not isinstance(unit_files, list):
        return {}
    cited = cited_names(slug_dir, unit, slug_dir / "runs" / unit)
    ids = {str(f["id"]) for f in unit_files if isinstance(f, dict) and f.get("id") and file_key(f) in cited}
    return {rid: sha for rid, sha in _hashes(read_jsonl(slug_dir / "raw_manifest.jsonl")).items() if rid in ids}


def cited_sources(sr: StageRun) -> set[str]:
    """The sha256 of every SOURCE file a delivered chronology on this matter
    cites for this unit: read from each delivered job dir still on the volume
    (``vision_seed.delivered_same_matter``), and from the delivered jobs'
    ledger records the broker stamped on the update (``job.delivered_cited``).

    WHY (2026-10-28). An update skipped a candidate only when its bytes
    matched a delivered EXHIBIT PDF, and an exhibit is a rewritten copy of
    its source (``exhibits`` re-writes it, ``strip`` re-saves it), so a
    re-pulled source never matched. The page pictures then had to prove the
    match, look-alike flowsheet pages drifted a few pixels, and a file the
    firm already had was filed again as a new exhibit. The delivered job's
    own manifest holds each source's hash, and its page map names the files
    it cited, so a byte-identical source is recognised before any picture
    is compared.

    WHY THE RECORD TOO (2026-11-06). A delivered job dir is wiped 72 hours
    after delivery (``retention.py``), and an update requested later found no
    cited source at all: on a client seat 0 of 3 re-pulled cited files were
    recognised. The ledger row outlives the dir."""
    unit = sr.unit.unit
    out: set[str] = set()
    for src in delivered_same_matter(getattr(sr, "job", None), sr.slug_dir.name):
        out |= set(cited_by_id(src, unit).values())
    recorded = (getattr(getattr(sr, "job", None), "delivered_cited", None) or {}).get(unit) or {}
    return out | {str(sha) for sha in recorded.values()}


def _orphans(src: Any) -> list[dict[str, Any]]:
    raw = read_json(src / "orphans.json", {}) or {}
    items = raw.get("orphans") if isinstance(raw, dict) else None
    return [x for x in items or [] if isinstance(x, dict) and x.get("name") and x.get("reason")]
