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

from ..gates import foreign_identity as FI
from .base import StageRun, read_json, read_jsonl
from .transcript import ABSENT, CONTENTLESS, UNREAD, contentless_reason, merged, recorded_ids, transcript_state

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
            elif r.get("compose") is False:
                # Clear only a mark THIS pass wrote (it always writes the pair),
                # so a stale billing mark still goes when billing_docs changes.
                # A `compose_skip` another stage authored -- a scan read in full
                # that carries nothing citable -- is not ours to drop: popping it
                # sends that file to the coverage gate with no explanation, which
                # is a HOLD this pass has no view of. Live on 2026-09-15: four
                # phone screenshots got their disposition in _attach_transcripts
                # and lost it eleven lines later, here.
                r.pop("compose", None)
                r.pop("compose_skip", None)
    return skipped


def unique_names(files: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Two files with one name are two files. Every stage after this one keys
    on `name + ext` (the chunk header the model cites, the exhibit map, the
    exhibit builder's file lookup), so a duplicate name collapses two records
    into one exhibit slot and a citation to the second lands on the first's
    page. Live-caught 2026-09-15: four email attachments named image001.jpg.
    The later file becomes `name (2)`, `(3)`, in list order; ids never change.
    Returns the (old, new) renames for the log."""
    seen: dict[str, int] = {}
    renamed: list[tuple[str, str]] = []
    for f in files:
        full = f["name"] + (f.get("ext") or "")
        n = seen.get(full, 0) + 1
        seen[full] = n
        if n > 1:
            new = f"{f['name']} ({n})"
            renamed.append((full, new + (f.get("ext") or "")))
            f["name"] = new
    return renamed


def _rename_duplicates(files: list[dict[str, Any]], log) -> None:
    for old, new in unique_names(files):
        log(f"  two files named '{old[:50]}': the later one is '{new[:50]}' from here on")


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


def _attach_transcripts(d: Path, recs: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str, int]]:
    """Give every read document its ``text_path``; return the ones not read yet.

    A vision-scanned file has no ``text_path`` in ``extracted.jsonl`` -- the
    vision stage writes ``text/<id>.txt`` afterwards and does not patch the
    record -- so selecting on ``text_path`` alone silently drops every scanned
    document.

    The disposition is the point of this function. A document that was READ but
    carries nothing citable gets a ``compose_skip`` rather than stopping the
    run: on 2026-09-11 four phone screenshots the Operator had read correctly
    halted a firm's chronology for four days, because "short" was being read as
    "not read".
    """
    recorded = recorded_ids(d)
    awaiting: list[tuple[dict[str, Any], str, int]] = []
    for r in recs:
        if r.get("vision_pages") and not merged(d / "text" / f"{r['id']}.txt", [int(p) for p in r["vision_pages"]]):
            # A text-layer document whose image pages vision has not read yet.
            awaiting.append((r, ABSENT, 0))
            continue
        if r.get("text_path"):
            continue
        state, measure = transcript_state(d, r["id"], recorded)
        if state in UNREAD:
            if r.get("scan"):
                # `measure` is the byte size for an unread state; there is no
                # text to count words in, and bytes are what the refusal reports.
                awaiting.append((r, state, measure))
            continue
        tp = d / "text" / f"{r['id']}.txt"
        r["text_path"] = str(tp)
        # `chars` is CHARACTERS everywhere it is read -- budget.py sizes the run
        # from it -- so it is not the disposition's word count. Two different
        # measurements of the same file, kept apart on purpose.
        r["chars"] = len(tp.read_text(encoding="utf-8", errors="replace"))
        if state == CONTENTLESS:
            r["compose_skip"] = contentless_reason(measure)
    return awaiting


def _leave_out_withheld(d: Path, usable: list[dict[str, Any]], log) -> list[dict[str, Any]]:
    """Drop every document the firm said to leave out (an identity decision):
    it belongs to no unit, so no stage reads it, and decisions.orphans
    discloses it."""
    withheld = FI.withheld(d)
    if not withheld:
        return usable
    keep, left_out = [], []
    for r in usable:
        (left_out if FI.matches(str(r.get("id") or ""), withheld) else keep).append(r)
    log(f"left out at the firm's direction (identity decision): {len(left_out)}")
    for r in left_out:
        log(f"  - {r.get('id')}")
    return keep


#: Exit code: a pulled file no unit owns is a question for the firm (whose is
#: it?), held here with an "identity:" reason (dag.py) before anything paid.
IDENTITY_HELD = 3
ORPHAN_HOLDS_FILE = "orphan_holds.json"


def _orphan_named(r: dict[str, Any]) -> str:
    """How the hold line names one file: its id, its name as the firm sees it,
    and its folder. Never what its text says about anyone."""
    out = f'{r.get("id")} "{r.get("name")}{r.get("ext") or ""}"'
    return out + (f" in {r['folder']}" if r.get("folder") else "")


def _resolve_orphans(sr: StageRun, units: dict[str, list], unassigned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decide every readable file the routing left without a unit; return the
    ones held for the firm (empty when every one was placed).

    WHY (2026-10-23). These files used to reach ``decisions.orphans`` after the
    strip -- every paid stage already spent -- and hold there as residue for
    SMD to sort out by hand. They are decided here instead, after vision, so a
    scanned file's transcription is on disk and its own text can be read:

    * one client on the job: a file whose text names no one else (or that a
      person released) is the client's and joins the unit; one whose text
      names someone else is the firm's question, with an "identity:" reason;
    * more than one client: the routing already tried every folder and name
      token, and a release does not say which client, so whose it is is the
      firm's to say: held, unless the firm said to leave it out (withheld
      files never reach this point, ``_leave_out_withheld``).

    No synthetic unit is made, and nothing is dropped: a file is placed or
    held. The detail of what was found stays in ``orphan_holds.json``."""
    if not unassigned:
        return []
    released = FI.released(sr.slug_dir)
    held: list[dict[str, Any]] = []
    if len(units) == 1:
        only = next(iter(units))
        surnames, dobs = FI.identities(sr.job.units)
        firsts = FI.first_names(sr.job.units)
        for r in unassigned:
            tp = Path(str(r.get("text_path") or ""))
            text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else ""
            found = FI.findings(text, surnames, dobs, firsts)
            if found and not FI.matches(str(r.get("id") or ""), released):
                held.append({**{k: r.get(k) for k in ("id", "name", "ext", "folder")}, "found": found})
            else:
                units[only].append(r)
                sr.log(
                    f"  + {r['name'][:66]} | {r.get('folder')}: owned by no folder rule; names no one else, joins {only}"
                )
    else:
        held = [{**{k: r.get(k) for k in ("id", "name", "ext", "folder")}, "found": []} for r in unassigned]
    if not held:
        return []
    (sr.slug_dir / ORPHAN_HOLDS_FILE).write_text(json.dumps(held, indent=1), encoding="utf-8")
    (sr.slug_dir / "units" / "_unassigned.json").write_text(
        json.dumps([{k: h.get(k) for k in ("id", "name", "folder")} for h in held], indent=1), encoding="utf-8"
    )
    why = "name a different person than the client" if len(units) == 1 else "belong to no client's folder"
    sr.log(
        f"IDENTITY HOLD: {len(held)} pulled file(s) {why}: {'; '.join(_orphan_named(h) for h in held[:5])}; "
        f"the firm says whose each is, or to leave it out (medchron.identity_decide)"
    )
    return held


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    (d / "units").mkdir(parents=True, exist_ok=True)
    recs = read_jsonl(d / "extracted.jsonl")
    # A vision-scanned file has no text_path in extracted.jsonl; the vision
    # stage writes text/<id>.txt afterwards and does not patch the record.
    # Selecting on text_path alone silently drops every scanned document.
    awaiting = _attach_transcripts(d, recs)
    usable = [r for r in recs if r.get("text_path")]
    if awaiting:
        # Say what was OBSERVED. The old wording asserted "NO transcription
        # yet" about documents that were transcribed, and two sessions spent
        # four days looking for an image-rendering fault it had invented.
        sr.log(f"!! {len(awaiting)} scan-queued file(s) are not readable yet:")
        for r, state, size in awaiting:
            sr.log(f"   ! {(r.get('name') or '')[:60]} | text/{r['id']}.txt: {state} ({size}B)")
        return 2
    is_excluded = _excluder([str(p) for p in (sr.cfg.get("units", "exclude_name_patterns") or [])])
    usable = _leave_out_withheld(d, usable, sr.log)
    spec = read_json(d / "units.json", None)
    units, excluded, unassigned = _route(usable, spec, sr.slug, is_excluded)

    held = _resolve_orphans(sr, units, unassigned)
    if held:
        return IDENTITY_HELD
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
        _rename_duplicates(files, sr.log)
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
