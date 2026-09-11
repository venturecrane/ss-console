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
    for r in read_jsonl(d / "raw_manifest.jsonl"):
        if not r.get("ok"):
            continue
        key = file_key(r)
        (dupes.add(key) if r.get("duplicate_of") else pulled.__setitem__(key, r))
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
            reason = classify_name(name, rules)
        (explained.append((name, reason)) if reason else unexplained.append(name))
    sr.log(
        f"{unit}: {len(in_unit)} source file(s) in the composition set; cited {len(cited_files & set(in_unit))}; "
        f"excluded with reason {len(explained)}; UNEXPLAINED {len(unexplained)}"
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
