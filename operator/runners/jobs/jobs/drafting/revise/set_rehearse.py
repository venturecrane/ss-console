"""The $0 rehearsal of a set revision (``set_run.py``), on real filed
responses, against hand-revised answer keys.

Law 18: nothing runs first on a client seat. This runs the job's own code over
each filed document: the base proof, the response pinning, the scope the request
names, the model's values (answered from RECORDINGS, ``replay/recorded.py``,
never the vendor), the firm's phrases written by code, the in-place edit and its
proof. It passes only when, for every document, the responses it changed are
exactly the responses the answer key changed, and nothing outside them changed::

    python -m jobs.drafting.revise.rehearse \\
        --units DIR            # one folder per filed document: base.docx (as filed) and key.docx (hand-revised)
        --request request.txt  # the attorney's email, his words only
        --rules wording.json   # the rules in force: {"rules": [...]} (a job's wording-rules.json, or the store's)
        [--recordings recordings.jsonl]   # the model's answers; default DIR/recordings.jsonl
        [--inputs <the drafting firm-inputs dir>]  # adds the format check
        [--model <id>] [--out DIR]

A call no recording answers is listed with its sha, so a recording can be
added for exactly that call. Exit 0 only when every document matches its key.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from ..replay import recorded
from . import responses as rs, rules as rules_mod, set_units as su

DEFAULT_MODEL = "recorded"


def _ask(work: Path, client: recorded.RecordedClient, model: str) -> rules_mod.Ask:
    door = recorded.doorway(work, client)

    def ask(stage: str, system: str, user: str, max_tokens: int) -> str:
        client.stage = stage
        r = door.call(
            stage, model=model, system=system, messages=[{"role": "user", "content": user}], max_tokens=max_tokens
        )
        return r.text

    return ask


def _unit(
    d: Path, request: str, rules: list[dict[str, Any]], ask: rules_mod.Ask, fmt: dict[str, Any] | None, work: Path
) -> dict[str, Any]:
    base, key = d / "base.docx", d / "key.docx"
    rep: dict[str, Any] = {"document": d.name}
    try:
        md, doc = su.read_base(base, work)
        _kmd, _kdoc = su.read_base(key, work)
    except su.BaseUnproven as exc:
        return {**rep, "ok": False, "why": f"base or key unproven: {exc}"}
    from .prior import docx_to_md

    key_changed, key_outside = rs.changed(md, docx_to_md(key))
    rep.update(set_type=doc.set_type, key_changed=key_changed, key_outside=key_outside)
    if not su.in_scope(request, [doc.set_type])[0]:
        ok = not key_changed and not key_outside
        return {**rep, "ok": ok, "in_scope": False, "changed": [], "why": "" if ok else "out of scope, key changed it"}
    try:
        values, conflicts = rules_mod.ask_values(rules, doc, ask)
    except recorded.NeedsModelCall as exc:
        return {**rep, "ok": False, "needs": exc.shas, "why": str(exc)}
    except rules_mod.ValuesRefused as exc:
        return {**rep, "ok": False, "why": f"values refused: {exc}"}
    out = work / d.name / "revised.docx"
    try:
        rec = su.revise_one(md, base, values, out, "discovery_response", fmt)
    except su.Refused as exc:
        return {**rep, "ok": False, "why": str(exc)}
    ok = set(key_changed) <= set(rec["changed"])
    rep.update(
        in_scope=True,
        changed=rec["changed"],
        skipped=rec["skipped"],
        conflicts=[vars(c) for c in conflicts],
        ok=ok,
        why=""
        if ok
        else f"changed {rec['changed']}, the key changed {key_changed}"
        + (" and text outside the responses" if key_outside else ""),
    )
    return rep


def rehearse_set(
    units_dir: Path,
    request: str,
    rules: list[dict[str, Any]],
    recordings: Path,
    *,
    fmt: dict[str, Any] | None = None,
    model: str = DEFAULT_MODEL,
    out: Path | None = None,
) -> dict[str, Any]:
    can, cannot = rules_mod.writable(rules)
    client = recorded.RecordedClient(_rows(recordings) if recordings.is_file() else {})
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(out or tmp)
        work.mkdir(parents=True, exist_ok=True)
        ask = _ask(work, client, model)
        docs = [
            _unit(d, request, can, ask, fmt, work) for d in sorted(units_dir.iterdir()) if (d / "base.docx").is_file()
        ]
    report = {
        "documents": docs,
        "rules": [r["rule_id"] for r in can],
        "without_wording": [r["rule_id"] for r in cannot],
        "needed": client.needed,
        "ok": bool(docs) and bool(can) and all(d["ok"] for d in docs),
    }
    if out is not None:
        (Path(out) / "rehearsal.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def _rows(path: Path) -> dict[str, dict[str, Any]]:
    """Recordings read from a file of any name (``load_recordings`` looks beside a key)."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("sha"), str) and isinstance(row.get("text"), str):
            out[row["sha"]] = row
    return out


def main_set(a: Any) -> int:
    fmt = None
    if a.inputs:
        from .. import firm as firm_mod

        fmt = firm_mod.load(a.inputs).data["format"]
    raw = json.loads(a.rules.read_text(encoding="utf-8"))
    rules = raw.get("rules") if isinstance(raw, dict) else raw
    rec = a.recordings or a.units / "recordings.jsonl"
    rep = rehearse_set(
        a.units, a.request.read_text(encoding="utf-8"), list(rules or []), rec, fmt=fmt, model=a.model, out=a.out
    )
    print(f"rules with the firm's words: {', '.join(rep['rules']) or 'none'}")
    if rep["without_wording"]:
        print(f"rules without exact words (never applied): {', '.join(rep['without_wording'])}")
    for d in rep["documents"]:
        print(
            f"  {d['document']:<40} {'OK' if d['ok'] else 'NO':<3} changed {d.get('changed')} key {d.get('key_changed')} {d['why']}"
        )
    for n in rep["needed"]:
        print(f"  NEEDS A RECORDING: stage {n['stage']} sha {n['sha']}")
    print("REHEARSAL OK" if rep["ok"] else "REHEARSAL REFUSED")
    return 0 if rep["ok"] else 1
