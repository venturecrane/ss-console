"""`coverage_gate`: END-TO-END COVERAGE. $0.

Every defect this pipeline produced was the same species: a step dropped
source content while everything downstream still looked complete. This is
the invariant that subsumes the class: EVERY FILE PULLED FROM THE MATTER MUST
BE ACCOUNTED FOR IN THE FINAL DOCUMENT, either cited in it or on an explicit
exclusion list with a stated reason. Anything in neither set is unexplained
loss, and the run does not ship (exit 1, which the driver holds on).

Set arithmetic over artifacts that already exist, so it costs nothing and
cannot be satisfied by a step merely claiming success. The pre-gate walks
the whole matter: a pulled file must belong to SOME unit, be a
byte-duplicate, be name-excluded, or be a documented orphan
(`orphans.json`, written by `decisions.orphans` with a reason per row). The
exclusion reasons come from the firm config (`coverage.exclusions`) and
print in full every run, because a silent explanation is how an omission
class enters. A citation counts only where prose carries it: a bare
citation on a line of its own once let two exhibits pass this gate while
appearing nowhere in the chronology.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import StageRun, read_json, read_jsonl
from .units import billing_stem

EXT_OK = {".pdf", ".docx", ".doc", ".tif", ".tiff", ".jpg", ".jpeg", ".png"}
CITE_ALONE = re.compile(r"\(Exhibit \d+(?: - p\. [0-9,\s\-]+)?\)")


def file_key(r: dict[str, Any]) -> str:
    return r["name"] + (r.get("ext") or "")


def exclusions(cfg: Any) -> list[tuple[re.Pattern, str]]:
    return [(re.compile(str(x["match"])), str(x["reason"])) for x in (cfg.get("coverage", "exclusions") or [])]


def classify_name(name: str, rules: list[tuple[re.Pattern, str]]) -> str | None:
    return next((reason for rx, reason in rules if rx.search(name)), None)


SEEN_LINE = re.compile(
    r"=== FILE: (?P<name>.+?)(?: \(fileId (?P<id>[\w.-]+)[^)]*\))?(?: \[part \d+/\d+\])?(?: \[continued\])? ===\s*\|?\s*"
    r"(?P<what>nothing extractable: [^\n]+|billing-dates: \d+|entries: \d+[^\n]*)"
)


def composer_dispositions(run_dir: Path, unit_files: list[dict[str, Any]]) -> dict[str, str]:
    """Per unit file, the composer's own account of it from the maps' FILES-SEEN
    blocks: a file it read and found nothing citable in says why (`nothing
    extractable: <reason>`), and a file that fed only the billing chart says
    `billing-dates: N`. That is the record's own statement, written by the
    stage that read every page, and it is the reason this gate lacked on
    2026-09-16: 97 pleadings, filings, photos and letters, each with a stated
    reason, held the run because the firm's name table had never met them.
    `entries: 0` with no reason explains nothing, so it is not returned."""
    id_to_name = {f["id"]: file_key(f) for f in unit_files}
    out: dict[str, str] = {}
    for p in sorted(run_dir.glob("map-*.md")):
        for m in SEEN_LINE.finditer(p.read_text(encoding="utf-8")):
            name = id_to_name.get(m.group("id") or "", m.group("name").strip())
            what = m.group("what").strip()
            if what.startswith("nothing extractable:"):
                out.setdefault(name, "composer: " + what[len("nothing extractable:") :].strip())
            elif what.startswith("billing-dates:") and what != "billing-dates: 0":
                out.setdefault(name, "evidenced billing dates only; the billing chart carries them")
    return out


def cited_exhibits(body: str) -> tuple[set[int], set[int]]:
    """(cited, hollow): exhibits with prose in front of a citation, and
    exhibits referenced only by a bare citation line."""
    cited: set[int] = set()
    hollow: set[int] = set()
    for m in re.finditer(r"\(Exhibit (\d+)", body):
        n = int(m.group(1))
        before = body[: m.start()].split("\n")[-1].strip()
        if not before:
            prev = [ln for ln in body[: m.start()].split("\n") if ln.strip()]
            before = prev[-1].strip() if prev else ""
            if before.endswith(")") and CITE_ALONE.fullmatch(before):
                before = ""
        (cited if before else hollow).add(n)
    return cited, hollow - cited


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half, for `medchron rehearse`: for every file in this unit, what
    the gate would say if NOTHING cited it (composition may not have run yet).
    A "needs a citation" line is a file that has neither a disposition, a
    billing-set membership, nor an exclusion class -- the exact hold this gate
    raises when the composer does not cite it. On the tree that died at merge
    this is the answer the run would otherwise have paid four stages to reach.
    """
    d = sr.slug_dir
    uf = d / "units" / f"{sr.unit.unit}.json"
    if not uf.is_file():
        return ["no units file yet (build_units has not run)"]
    unit_files = read_json(uf, [])
    in_unit = {file_key(f): f for f in unit_files}
    rules = exclusions(sr.cfg)
    spec = read_json(d / "billing_docs.json", []) or []
    billing_names = {b["name"] for b in (spec.get("docs") if isinstance(spec, dict) else spec) or []}
    said = composer_dispositions(d / "runs" / sr.unit.unit, unit_files)
    explained = 0
    needs: list[str] = []
    for name in sorted(in_unit):
        if in_unit[name].get("compose_skip"):
            reason: str | None = f"compose skipped: {in_unit[name]['compose_skip']}"
        elif billing_stem(name) in billing_names or name in billing_names:
            reason = "in the authored billing-chart set"
        else:
            reason = classify_name(name, rules) or said.get(name)
        if reason:
            explained += 1
        else:
            needs.append(f"  needs a citation: {name[:70]}")
    head = f"{len(in_unit)} unit file(s); with nothing cited: {explained} explained, {len(needs)} would need a citation"
    return [head, *needs[:25]]


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    unit = sr.unit.unit
    rd = d / "runs" / unit
    unit_files = read_json(d / "units" / f"{unit}.json", [])
    in_unit = {file_key(f): f for f in unit_files}
    exclude_names = [re.compile(str(p)) for p in (sr.cfg.get("units", "exclude_name_patterns") or [])]
    rules = exclusions(sr.cfg)

    pulled: dict[str, dict[str, Any]] = {}
    dupes: set[str] = set()
    for r in (x for x in read_jsonl(d / "raw_manifest.jsonl") if x.get("ok")):
        (dupes.add(file_key(r)) if r.get("duplicate_of") else pulled.__setitem__(file_key(r), r))
    owners: dict[str, set[str]] = {}
    udir = d / "units"
    for p in sorted(udir.glob("*.json")) if udir.is_dir() else []:
        if p.name.startswith("_"):
            continue
        for f in read_json(p, []):
            owners.setdefault(file_key(f), set()).add(p.stem)
    orphans: dict[str, str] = {}
    for o in (read_json(d / "orphans.json", {}) or {}).get("orphans") or []:
        name, reason = o.get("name"), (o.get("reason") or "").strip()
        if not name or not reason:
            sr.log(f"orphans.json: entry without a name and a reason: {o}")
            return 1
        orphans[name] = reason
    dropped = [
        k
        for k, r in pulled.items()
        if k not in owners
        and k not in dupes
        and k not in orphans
        and (r.get("ext") or "").lower() in EXT_OK
        and not any(rx.search(r.get("name") or "") for rx in exclude_names)
    ]
    if dropped:
        sr.log(
            f"COVERAGE GATE FAIL: {len(dropped)} pulled file(s) never reached the composition set (not duplicates, "
            f"not excluded):"
        )
        for k in sorted(dropped):
            sr.log(f"   x {k[:78]}")
        return 1
    units_seen = {u for us in owners.values() for u in us}
    if len(units_seen) > 1:
        shared = [k for k, us in owners.items() if len(us) > 1]
        elsewhere = sum(1 for k in pulled if k not in in_unit and k in owners)
        sr.log(
            f"  matter has {len(units_seen)} units; {elsewhere} pulled file(s) belong to other units, {len(shared)} in more than one"
        )
    for name in sorted(orphans):
        sr.log(f"    ~ {name[:62]:62s} {orphans[name][:48]}")

    spec = read_json(d / "billing_docs.json", [])
    billing_names = {b["name"] for b in (spec.get("docs") if isinstance(spec, dict) else spec) or []}
    said = composer_dispositions(rd, unit_files)
    body = ""
    for cand in ("entries_scoped_final.md", "entries_scoped.md", "entries_final.md"):
        if (rd / cand).is_file():
            body = (rd / cand).read_text(encoding="utf-8")
            break
    if not body:
        sr.log("COVERAGE GATE: no assembled body found")
        return 1
    cited_ex, hollow = cited_exhibits(body)
    if hollow:
        sr.log(f"  HOLLOW citations (exhibit referenced with no prose): {sorted(hollow)}")
    pm = read_json(d / "out" / unit / "page_map.json", None)
    if pm is not None:
        cited_files = {f["file"] for e in pm if e["exhibit"] in cited_ex for f in e["files"]}
    else:
        cited_files = {n for n, num in read_json(rd / "exhibit_map.json", {}).items() if num in cited_ex}
    unexplained: list[str] = []
    explained: list[tuple[str, str]] = []
    for name in sorted(in_unit):
        if name in cited_files:
            continue
        if in_unit[name].get("compose_skip"):
            reason: str | None = f"compose skipped: {in_unit[name]['compose_skip']}"
        elif billing_stem(name) in billing_names or name in billing_names:
            reason = "in the authored billing-chart set (billing chart carries it)"
        else:
            reason = classify_name(name, rules) or said.get(name)
        (explained.append((name, reason)) if reason else unexplained.append(name))
    cited_n = len(cited_files & set(in_unit))
    sr.log(
        f"{unit}: {len(in_unit)} file(s); cited {cited_n}; excluded with reason {len(explained)}; UNEXPLAINED {len(unexplained)}"
    )
    for name, reason in explained:
        sr.log(f"    ~ {name[:62]:62s} {reason[:48]}")
    if unexplained:
        for name in unexplained[:25]:
            sr.log(f"  - {name[:66]:66s} {in_unit[name].get('chars') or 0:>8} chars")
        (rd / "coverage_unexplained.json").write_text(json.dumps(unexplained, indent=1), encoding="utf-8")
        return 1
    sr.log("COVERAGE GATE PASS: every source file is cited or explained")
    return 0
