"""`build_units`: assign extracted files to composition units and apply the
firm's exclusions. $0.

Units are AUTHORED (`units.json`, written by `decisions.units` from the job
envelope), never inferred from the slug: a predecessor knew two matters by
name and silently produced nothing for a third. Files route by folder prefix
first, then by a client-name token that survives underscore-joined filenames
(`\\b` does not fire between an underscore and a letter, which made four files
on a family matter unroutable by any token, including a 21.7 MB record that
existed in no plaintiff folder).

Billing-only files stay in the unit (the coverage gate's denominator is the
unit) but are marked `compose: false` once `billing_extract.jsonl` proves the
transcription captured them on EVERY page; a partial extraction keeps
composing, because the map pass is then the only read the file gets.

Exit 2 when a scan-queued file has no transcription (vision has not finished)
or when billing_docs.json exists without billing_extract.jsonl: marking on a
guess would drop those files from every read.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import StageRun, read_json, read_jsonl

BILLING_ONLY_TYPES = {"MEDICAL_BILL", "LEDGER", "LIEN_SUBROGATION", "VENDOR_INVOICE"}


def token_hit(tok: str, name: str) -> bool:
    """Letters-only boundary: match inside underscore joins, refuse inside
    another word. Any test of this must use an underscore-joined name."""
    return re.search(rf"(?i)(?<![A-Za-z]){re.escape(tok)}(?![A-Za-z])", name or "") is not None


def billing_stem(name_with_ext: str) -> str:
    """The name billing_docs.json / billing_extract.jsonl use for a unit file;
    one rule shared with the coverage gate so the two never disagree."""
    n = name_with_ext
    return n[:-4] if n.lower().endswith((".pdf", ".doc")) else n


def compose_skip_reason(rec: dict[str, Any] | None) -> str | None:
    """Why a billing_extract row lets its file skip composition, or None."""
    if not rec:
        return None
    chunks = rec.get("chunks") or []
    pages = int(rec.get("pages") or 0)
    if not chunks or rec.get("failures") or pages < 1:
        return None
    types: set[str] = set()
    evidenced: set[int] = set()
    for c in chunks:
        if "FAILED_PAGE" in c:
            return None
        t = c.get("doc_type")
        if t not in BILLING_ONLY_TYPES or not (c.get("line_items") or c.get("printed_totals")):
            return None
        types.add(t)
        for item in (c.get("line_items") or []) + (c.get("printed_totals") or []):
            try:
                evidenced.add(int(item.get("page")))
            except (TypeError, ValueError):
                pass
    # Every page must carry a billing artifact: the extractor types a mixed
    # file by its first page, and pages 2-3 may hold the clinical report.
    if set(range(1, pages + 1)) - evidenced:
        return None
    return (
        f"billing-only source ({'/'.join(sorted(types))}), {len(chunks)} chunk(s) fully captured by "
        f"billing_extract (a line item or printed total on every one of {pages} page(s), no failed page)"
    )


def mark_compose_skips(d: Path, units: dict[str, list[dict[str, Any]]], log) -> list[tuple[str, str, int]] | None:
    """None means refused: the billing set is authored but not extracted."""
    bp, ep = d / "billing_docs.json", d / "billing_extract.jsonl"
    if not bp.is_file():
        return []
    if not ep.is_file():
        log(
            f"REFUSING: {bp.name} exists but {ep.name} does not; run billing_extract first so the billing files "
            f"are marked from evidence rather than a guess"
        )
        return None
    rows = {r["file"]: r for r in read_jsonl(ep) if r.get("file")}
    skipped: list[tuple[str, str, int]] = []
    for u, files in units.items():
        for r in files:
            rec = rows.get(billing_stem(r["name"] + (r.get("ext") or ""))) or rows.get(r["name"])
            reason = compose_skip_reason(rec)
            if reason:
                r["compose"] = False
                r["compose_skip"] = reason
                skipped.append((u, r["name"], int((rec or {}).get("pages") or r.get("pages") or 0)))
            else:
                r.pop("compose", None)
                r.pop("compose_skip", None)
    return skipped


def _excluder(patterns: list[str]):
    """One compiled pattern per config row (each may carry its own inline
    flags, which a single joined expression would reject)."""
    compiled = [re.compile(p) for p in patterns]
    return lambda name: any(c.search(name or "") for c in compiled)


def _route(
    usable: list[dict[str, Any]], spec: dict[str, dict[str, Any]] | None, slug: str, is_excluded
) -> tuple[dict[str, list], list[str], list[dict[str, Any]]]:
    excluded: list[str] = []
    unassigned: list[dict[str, Any]] = []
    if spec is None or len(spec) == 1:
        # A single-unit matter has no routing question: every non-excluded
        # file belongs to the one client (the frozen tree ran these matters
        # with no units.json at all; the envelope always authors one).
        only = slug if spec is None else next(iter(spec))
        units: dict[str, list] = {only: []}
        for r in usable:
            (excluded.append(r["name"]) if is_excluded(r["name"]) else units[only].append(r))
        return units, excluded, unassigned
    units = {u: [] for u in spec}
    for r in usable:
        if is_excluded(r["name"]):
            excluded.append(r["name"])
            continue
        hit = next(
            (
                u
                for u, rule in spec.items()
                if rule.get("folder_prefix") and (r.get("folder") or "").startswith(rule["folder_prefix"])
            ),
            None,
        )
        if not hit:
            hit = next(
                (u for u, rule in spec.items() if rule.get("name_token") and token_hit(rule["name_token"], r["name"])),
                None,
            )
        (units[hit].append(r) if hit else unassigned.append(r))
    return units, excluded, unassigned


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    (d / "units").mkdir(parents=True, exist_ok=True)
    recs = read_jsonl(d / "extracted.jsonl")
    # A vision-scanned file has no text_path in extracted.jsonl; the vision
    # stage writes text/<id>.txt afterwards and does not patch the record.
    # Selecting on text_path alone silently drops every scanned document.
    for r in recs:
        if not r.get("text_path"):
            tp = d / "text" / f"{r['id']}.txt"
            if tp.is_file() and tp.stat().st_size > 50:
                r["text_path"] = str(tp)
                r["chars"] = tp.stat().st_size
    usable = [r for r in recs if r.get("text_path")]
    awaiting = [r for r in recs if r.get("scan") and not r.get("text_path")]
    if awaiting:
        sr.log(f"!! {len(awaiting)} scan-queued file(s) have NO transcription yet; vision has not finished:")
        for r in awaiting:
            sr.log(f"   ! {(r.get('name') or '')[:70]}")
        return 2
    is_excluded = _excluder([str(p) for p in (sr.cfg.get("units", "exclude_name_patterns") or [])])
    spec = read_json(d / "units.json", None)
    units, excluded, unassigned = _route(usable, spec, sr.slug, is_excluded)

    if unassigned:
        (d / "units" / "_unassigned.json").write_text(
            json.dumps([{k: r.get(k) for k in ("id", "name", "folder")} for r in unassigned], indent=1),
            encoding="utf-8",
        )
        sr.log(f"UNASSIGNED files: {len(unassigned)}; every one must be assigned or excluded before composition")
        for r in unassigned:
            sr.log(f"  ? {r['name'][:70]} | {r.get('folder')}")
    if spec and len(units) > 1:
        # Outcome cross-check: a routed file whose NAME matches another unit's
        # token and not its own. Sees only filename-visible misrouting.
        toks = {u: rule.get("name_token") for u, rule in spec.items() if rule.get("name_token")}
        crossed = 0
        for u, files in units.items():
            own = toks.get(u)
            for r in files:
                for u2, t2 in toks.items():
                    if u2 != u and token_hit(t2, r["name"]) and not (own and token_hit(own, r["name"])):
                        crossed += 1
                        sr.log(f"CROSS-UNIT? '{r['name'][:60]}' sits in {u} but names {u2}")
        sr.log(
            f"cross-unit name check: {crossed} flag(s) across {sum(len(f) for f in units.values())} routed files "
            f"(filename-visible misrouting only)"
        )
    skipped = mark_compose_skips(d, units, sr.log)
    if skipped is None:
        return 2
    for u, files in units.items():
        (d / "units" / f"{u}.json").write_text(json.dumps(files, indent=1), encoding="utf-8")
        chars = sum(int(r.get("chars") or 0) for r in files)
        n_skip = sum(1 for r in files if not r.get("compose", True))
        sr.log(
            f"{u}: {len(files)} files, {chars / 1000:.0f}k chars" + (f" ({n_skip} compose-skipped)" if n_skip else "")
        )
    if skipped:
        sr.log(
            f"compose-skipped (billing fully extracted): {len(skipped)} file(s), {sum(p for _, _, p in skipped)} pages"
        )
        for u, name, pages in skipped:
            sr.log(f"  - {name[:62]:62s} {pages:>4} p  [{u}]")
    sr.log(f"excluded: {len(excluded)}")
    for e in excluded:
        sr.log(f"  - {e[:70]}")
    return 0
