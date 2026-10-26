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
               cited, in the authored billing-chart set, excluded by an
               authored name rule, or withheld by an identity decision (the
               firm's direction or the rule's): accounted for, and an update skips it.
    uncovered  the composer read it and found nothing citable, the unit marked
               it `compose_skip` (a contentless or unreadable scan), retrieval
               failed, or it is a documented orphan: NOT in the delivered
               record, so an update reads it again by default.

An id in neither set would be an unexplained loss, which the coverage gate
itself refuses to ship, so the two sets sum to every pulled document and the
broker refuses a payload where they do not.

Each uncovered id may also carry a reason TOKEN in ``why`` (``uncovered_why``),
so a follow-up question about an unread file is answered from the record
rather than guessed. Tokens, never names or text: the ledger holds ids only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import uncovered_why as W
from . import unopened
from .gates.foreign_identity import WITHHELD_REASONS
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


def _orphan_names(slug_dir: Path) -> tuple[dict[str, str], set[str]]:
    """({documented orphan: its reason}, the ones withheld: at the firm's direction or by the identity rule)."""
    rows = (read_json(slug_dir / "orphans.json", {}) or {}).get("orphans") or []
    named = [o for o in rows if o.get("name")]
    reasons = {o["name"]: str(o.get("reason") or "") for o in named}
    return reasons, {o["name"] for o in named if o.get("reason") in WITHHELD_REASONS}


def _orphan_token(reason: str) -> str:
    """The fixed token for an orphan's stated reason (``decisions.orphans``)."""
    if reason == unopened.REASON:
        return W.PASSWORD_PROTECTED
    if reason.endswith("a type this review does not read"):
        return W.NOT_PDF_OR_IMAGE
    return W.NOT_EXPLAINED


def _skip_token(reason: str) -> str:
    """The fixed token for a unit row's ``compose_skip``: a contentless scan
    (``transcript.contentless_reason``) carries almost no text; any other skip
    is not one the record words."""
    return W.NO_TEXT if reason.startswith("read in full;") else W.NOT_EXPLAINED


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

    orphans, withheld = _orphan_names(slug_dir)
    # Withheld (the firm's direction or the identity rule) is accounted for,
    # the way a byte-duplicate is: an update must not read it and ask again.
    accounted = duplicates | withheld
    billing_spec = read_json(slug_dir / "billing_docs.json", [])
    billing_names = {
        b["name"] for b in (billing_spec.get("docs") if isinstance(billing_spec, dict) else billing_spec) or []
    }
    rules = exclusions(cfg)
    said = composer_dispositions(run_dir, unit_files)
    cited = _cited_names(slug_dir, unit, run_dir)

    covered: set[str] = set()
    uncovered: set[str] = set()
    why: dict[str, str] = {}
    for key in sorted(set(pulled) | failed):
        if key in failed:
            uncovered.add(key)
            why[key] = W.RETRIEVAL_FAILED
            continue
        if key in cited or key in accounted:
            covered.add(key)
            continue
        row = by_name.get(key, pulled.get(key, {}))
        if row.get("compose_skip"):
            uncovered.add(key)
            why[key] = _skip_token(str(row["compose_skip"]))
            continue
        if key in orphans:
            uncovered.add(key)
            why[key] = _orphan_token(orphans[key])
            continue
        if billing_stem(key) in billing_names or key in billing_names:
            covered.add(key)
            continue
        if classify_name(key, rules) or (pulled.get(key, {}).get("ext") or "").lower() not in EXT_OK:
            covered.add(key)
            continue
        # The composer read it and found nothing citable (``composer:``), or the
        # delivered document neither cites nor explains it, which the coverage
        # gate holds, so reaching here means the gate was bypassed. Either way
        # it is uncovered and an update re-reads it; neither is a reason the
        # record words for a reader.
        uncovered.add(key)
        why[key] = W.NOT_EXPLAINED

    def ids(names: set[str]) -> list[str]:
        return sorted({id_of[n] for n in names if n in id_of})

    covered_ids, uncovered_ids = _as_matter_ids(slug_dir, ids(covered)), _as_matter_ids(slug_dir, ids(uncovered))
    # An id that landed in both sets (two rows sharing an id) would let an update
    # decide either way, so the uncovered reading wins: re-reading is recoverable,
    # skipping a record is not.
    covered_ids = [i for i in covered_ids if i not in set(uncovered_ids)]
    out: dict[str, Any] = {
        "covered": covered_ids,
        "uncovered": uncovered_ids,
        "pulled": len(covered_ids) + len(uncovered_ids),
    }
    reasons = _why_by_id(slug_dir, {id_of[n]: t for n, t in why.items() if n in id_of}, uncovered_ids)
    if reasons:
        out["why"] = reasons
    return out


def _why_by_id(slug_dir: Path, why: dict[str, str], uncovered_ids: list[str]) -> dict[str, str]:
    """Each uncovered document's token, keyed the way the uncovered set is
    (``_as_matter_ids``). A locked PDF reads as password-protected whichever
    path left it unread, unless its bytes never arrived. Two attachments of
    one email with different tokens: the first in sorted id order wins; the
    token words an answer, it decides nothing."""
    locked = set(unopened.password_protected(slug_dir))
    parent = _parents(slug_dir)
    out: dict[str, str] = {}
    for rid in sorted(why):
        token = why[rid]
        if rid in locked and token != W.RETRIEVAL_FAILED:
            token = W.PASSWORD_PROTECTED
        out.setdefault(parent.get(rid, rid), token)
    return W.restricted(out, uncovered_ids)


def _parents(slug_dir: Path) -> dict[str, str]:
    """``{msgatt id: the email's id}`` for every folded attachment that recorded one."""
    return {
        str(r["id"]): str(r["container_id"])
        for r in read_jsonl(slug_dir / "raw_manifest.jsonl")
        if str(r.get("id") or "").startswith("msgatt-") and r.get("container_id")
    }


def _as_matter_ids(slug_dir: Path, ids: list[str]) -> list[str]:
    """Each attachment folded out of an email (``msgatt-<hash>``, the runner's
    own id) as the id of the email it came from, which is a document on the
    matter. 2026-10-25: a delivered chronology listed 37 attachment ids as
    uncovered, the Operator named them in an update, and the update held
    because no such document is on the matter. A row from before the email's
    id was recorded (``container_id``) keeps its own id; the update's pull
    resolves those (``stages/email_parents``)."""
    parent = _parents(slug_dir)
    return sorted({parent.get(i, i) for i in ids})


def covered_payload(slug_dir: Path, units: list[str], cfg: Any) -> dict[str, Any] | None:
    """The union across the units this job delivered, or None when the run left
    no coverage artifacts to read (nothing is guessed: the skill reports the
    record as unknown and submits no update)."""
    records = [covered_sets(slug_dir, u, cfg) for u in units if (slug_dir / "units" / f"{u}.json").is_file()]
    if not records:
        return None
    return _union(records)


def _union(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Several units' records as one: uncovered in ANY unit stays uncovered,
    and each still-uncovered id keeps the first reason a unit gave it."""
    covered = {str(x) for r in records for x in r.get("covered") or []}
    uncovered = {str(x) for r in records for x in r.get("uncovered") or []}
    covered -= uncovered
    out: dict[str, Any] = {
        "covered": sorted(covered),
        "uncovered": sorted(uncovered),
        "pulled": len(covered) + len(uncovered),
    }
    why = W.union([r.get("why") if isinstance(r.get("why"), dict) else None for r in records], uncovered)
    if why:
        out["why"] = why
    return out


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
    records = [o["covered"] for o in outcomes if isinstance(o, dict) and isinstance(o.get("covered"), dict)]
    if not records:
        return None
    return _union(records)


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
    if worst.get("replaced"):
        # An update into the delivered chronology retired the document it
        # replaced (by id, after the new one was proven): the record names it.
        fields["delivery"]["replaced"] = dict(worst["replaced"])
    if worst.get("current"):
        # An update that found nothing new filed nothing: the record says so,
        # with the counts per reason (``update/outcome.summary``).
        fields["delivery"]["already_current"] = dict(worst["current"])
    merged = merge_covered(outcomes)
    if merged is not None:
        fields["covered"] = merged
    return fields


def documents_pulled(slug_dir: Path) -> int:
    """Matter files pulled this run (`raw_manifest.jsonl` rows that landed)."""
    p = slug_dir / "raw_manifest.jsonl"
    if not p.is_file():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("ok"):
                n += 1
        except ValueError:
            continue
    return n
