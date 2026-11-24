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
        --rules rules.json     # the rules in force: {"rules": [...]} (a job's wording-rules.json, or the store's)
        [--recordings recordings.jsonl]   # the model's answers; default DIR/recordings.jsonl
        [--inputs <the drafting firm-inputs dir>]  # adds the format check
        [--model <id>] [--out DIR]

A call no recording answers is listed with its sha, so a recording can be
added for exactly that call. Exit 0 only when every document matches its key.

It also SCORES the revision (``score.py``): per response made, correct (its
words now equal the key's), missed and extra, the same for the Index of
Exhibits' lines, totals over every document, and whether the request would be
delivered or held, with the reason the job would give. A unit folder may hold a
``set_type`` file naming the kind the document was delivered as, read as the
job reads it (``responses.read``).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from ..replay import recorded
from . import responses as rs, rules as rules_mod, score, set_units as su

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


def _hint(d: Path) -> str | None:
    p = d / "set_type"
    return (p.read_text(encoding="utf-8").strip() or None) if p.is_file() else None


def _score(md: str, out: str | None, key_md: str, hint: str | None) -> dict[str, Any]:
    from .prior import docx_to_md

    base, key = rs.read(md, hint), rs.read(key_md, hint)
    done = rs.read(docx_to_md(Path(out)), hint) if out else base
    return {"responses": score.responses(base, done, key), "index": score.index(base, done, key)}


def _unit(
    d: Path, request: str, rules: list[dict[str, Any]], ask: rules_mod.Ask, fmt: dict[str, Any] | None, work: Path
) -> dict[str, Any]:
    base, key, hint = d / "base.docx", d / "key.docx", _hint(d)
    rep: dict[str, Any] = {"document": d.name}
    try:
        md, doc = su.read_base(base, work, hint)
        su.read_base(key, work, hint)
    except su.BaseUnproven as exc:
        return {**rep, "ok": False, "error": "revise_base_unproven", "why": f"base or key unproven: {exc}"}
    from .prior import docx_to_md

    key_md = docx_to_md(key)
    key_changed, key_outside = rs.changed(md, key_md, hint)
    rep.update(set_type=doc.set_type, key_changed=key_changed, key_outside=key_outside)
    if doc.is_index:  # kept as filed, as the job keeps it; its lines scored against the key
        ok = not key_outside
        why = "" if ok else "an index file: the key changed its lines, which are not revised yet"
        return {**rep, "ok": ok, "index_file": True, "in_scope": False, "changed": [], "why": why,
                **_score(md, None, key_md, hint)}  # fmt: skip
    if not su.in_scope(request, [doc.set_type])[0]:
        ok = not key_changed and not key_outside
        why = "" if ok else "out of scope, key changed it"
        return {**rep, "ok": ok, "in_scope": False, "changed": [], "why": why, **_score(md, None, key_md, hint)}
    try:
        values, conflicts = rules_mod.ask_values(rules, doc, ask)
    except recorded.NeedsModelCall as exc:
        return {**rep, "ok": False, "error": "needs_recording", "needs": exc.shas, "why": str(exc)}
    except rules_mod.ValuesRefused as exc:
        return {**rep, "ok": False, "error": "revise_unchecked", "why": f"values refused: {exc}"}
    try:
        rec = su.revise_one(md, base, values, work / d.name / "revised.docx", "discovery_response", fmt, hint)
    except su.Refused as exc:
        return {**rep, "ok": False, "error": exc.code, "why": str(exc)}
    ok = set(key_changed) == set(rec["changed"]) and not key_outside
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
        **_score(md, rec["out"], key_md, hint),
    )
    return rep


def _request(docs: list[dict[str, Any]], can: list[dict[str, Any]]) -> dict[str, str]:
    """Whether the job would deliver this request, or hold it and why (``set_run``'s endings)."""
    if not can:
        return {"outcome": "held", "reason": "revise_unchanged: no standing rule gives the exact wording to use"}
    bad = next((d for d in docs if d.get("error")), None)
    if bad is not None:
        how = "incomplete" if bad["error"] == "needs_recording" else "failed"
        return {"outcome": how, "reason": f"{bad['error']}: {bad['document']}: {bad['why']}"[:400]}
    if not any(d.get("in_scope") for d in docs):
        return {"outcome": "held", "reason": "revise_unchanged: the request names no kind of response filed"}
    if not any(d.get("changed") for d in docs):
        why = "; ".join(s for d in docs for s in d.get("skipped") or []) or "no rule applies to any response"
        return {"outcome": "held", "reason": f"revise_unchanged: no filed response would change: {why}"[:400]}
    return {"outcome": "delivered", "reason": ""}


def _totals(docs: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    scored = [d for d in docs if "responses" in d]
    return {
        "responses": score.totals([d["responses"] for d in scored]),
        "index": score.totals([d["index"] for d in scored]),
    }


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
        "request": _request(docs, can),
        "totals": _totals(docs),
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
    if getattr(a, "book", None):
        return _main_book(a, list(rules or []), rec, fmt)
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
    for what in ("responses", "index"):
        t = rep["totals"][what]
        print(f"  {what}: " + ", ".join(f"{c} {t[c]}" for c in score.COUNTS))
    req = rep["request"]
    print(f"request: {req['outcome']}" + (f" ({req['reason']})" if req["reason"] else ""))
    for n in rep["needed"]:
        print(f"  NEEDS A RECORDING: stage {n['stage']} sha {n['sha']}")
    print("REHEARSAL OK" if rep["ok"] else "REHEARSAL REFUSED")
    return 0 if rep["ok"] else 1


def _main_book(a: Any, rules: list[dict[str, Any]], rec: Path, fmt: dict[str, Any] | None) -> int:
    """``--book``: the firm-rules path (``book_rehearse.py``), scored per kind of change."""
    from . import book_rehearse

    rows = _rows(rec) if rec.is_file() else {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(a.out or tmp)
        work.mkdir(parents=True, exist_ok=True)
        rep = book_rehearse.rehearse(a.units, a.request.read_text(encoding="utf-8"), rules, rows, a.book, work, fmt)
        (work / "rehearsal.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    for d in rep["documents"]:
        r = d["responses"]
        print(
            f"  {d['document']:<40} changed {d['changed']} correct {r['correct']} missed {r['missed']} extra {r['extra']}"
        )
    for what in ("responses", "index"):
        t = rep["totals"][what]
        print(f"  {what}: " + ", ".join(f"{c} {t[c]}" for c in score.COUNTS))
    for kind, t in rep["totals"]["kinds"].items():
        print(f"  {kind}: correct {t['correct']}, missed {t['missed']}, extra {t['extra']}")
    req = rep["request"]
    print(f"request: {req['outcome']}" + (f" ({req['reason']})" if req["reason"] else ""))
    for n in rep["needed"]:
        print(f"  NEEDS A RECORDING: stage {n['stage']} sha {n['sha']}")
    ok = req["outcome"] == "delivered" and all(d["responses"]["extra"] == 0 for d in rep["documents"])
    print("REHEARSAL OK" if ok else "REHEARSAL REFUSED")
    return 0 if ok else 1
