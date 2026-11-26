"""The $0 rehearsal of a propounded set's revision (``propounded_run.py``), on
real filed sets, against hand-revised answer keys.

Law 18: nothing runs first on a client seat. This runs the job's own code over
each filed set: the base proof, the set read (type, party, count), the plan
and its scope and number checks, the model's values (answered from
RECORDINGS, ``replay/recorded.py``, never the vendor), every change written by
code, the in-place edit and its proof. It passes only when every set's
requests, definitions and declaration equal its key's::

    python -m jobs.drafting.revise.propounded_rehearse \\
        --units DIR            # one folder per filed set: base.docx (as filed) and key.docx (hand-revised)
        --request request.txt  # the attorney's email, his words only
        [--recordings recordings.jsonl]   # the model's answers; default DIR/recordings.jsonl
        [--model <id>] [--out DIR]

A unit folder may hold a ``set_type`` file naming the kind the set was
delivered as. A call no recording answers is listed with its sha, so a
recording can be added for exactly that call. Exit 0 only when every set
matches its key and the job would deliver; a key equal to its base is how a
rehearsal says the job must hold, and then the job holding passes.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from .. import set_items
from ..replay import recorded
from . import prior, propounded as pp, propounded_ask as pa, set_units as su
from .set_rehearse import _ask, _rows

DEFAULT_MODEL = "recorded"


def _hint(d: Path) -> str:
    p = d / "set_type"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def _norm(s: str) -> str:
    return " ".join(s.replace("**", "").split())


def shape(md: str, variant: str) -> dict[str, Any]:
    """What a key is compared on: each request's number and words, the defined
    terms in order, and the declaration's words."""
    items = [(it.number, _norm(it.text(md))) for it in set_items.read(md, variant)]
    got = pp._defs(md)
    terms = [t for *_x, t in got[2]] if got else []
    span = pp.decl_block(md)
    return {"items": items, "terms": terms, "declaration": _norm(md[span[0] : span[1]]) if span else ""}


def _base(d: Path, work: Path, name: str) -> tuple[str, pp.Unit]:
    md = prior.docx_to_md(d / name)
    ed, diffs = su.edit_file(d / name, md, work / f"{d.name}-{name}-roundtrip.docx")
    if diffs or ed.touched or ed.removed:
        raise pp.Refused("revise_base_unproven", f"{d.name}/{name} does not read back element for element")
    return md, pp.read_unit(d.name + ".docx", _hint(d), md)


def rehearse(units_dir: Path, request: str, recordings: Path, *, model: str = DEFAULT_MODEL, out: Path | None = None):
    client = recorded.RecordedClient(_rows(recordings) if recordings.is_file() else {})
    dirs = [d for d in sorted(units_dir.iterdir()) if (d / "base.docx").is_file()]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(out or tmp)
        work.mkdir(parents=True, exist_ok=True)
        ask = _ask(work, client, model)
        report = _run(dirs, request, ask, work)
    report["needed"] = client.needed
    if out is not None:
        (Path(out) / "rehearsal.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def _run(dirs: list[Path], request: str, ask: pa.Ask, work: Path) -> dict[str, Any]:
    docs: list[dict[str, Any]] = [{"document": d.name} for d in dirs]
    try:
        bases = [_base(d, work, "base.docx") for d in dirs]
        keys = [_base(d, work, "key.docx")[0] for d in dirs]
    except pp.Refused as exc:
        return _end(docs, "failed", f"{exc.code}: {exc.why}")
    mds, units = [b[0] for b in bases], [b[1] for b in bases]
    want_change = any(shape(k, u.set_type) != shape(m, u.set_type) for k, m, u in zip(keys, mds, units))
    try:
        ops, scoped = pa.settle_plan(request, units, ask)
        oracle = pa.ModelOracle(ask, request)
        results = {}
        for k in sorted({k for ks in scoped for k in ks}):
            mine = [o for o, ks in zip(ops, scoped) if k in ks]
            results[k] = pp.revise_unit(mds[k], units[k], mine, oracle, pp.foreign_refs(units, mds, k))
    except recorded.NeedsModelCall as exc:
        return _end(docs, "incomplete", f"needs_recording: {exc}")
    except pp.Held as exc:
        return _end(docs, "held", str(exc), ok=not want_change)
    except pp.Refused as exc:
        return _end(docs, "failed", str(exc))
    for k, (d, u) in enumerate(zip(dirs, units)):
        res = results.get(k)
        md = res.md if res else mds[k]
        if res and res.md != mds[k]:
            ed, diffs = su.edit_file(d / "base.docx", res.md, work / d.name / "revised.docx")
            if diffs:
                return _end(docs, "failed", f"revise_layout_lost: {d.name}: " + "; ".join(diffs)[:300])
        got, key = shape(md, u.set_type), shape(keys[k], u.set_type)
        docs[k].update(
            set_type=u.set_type,
            party=u.party,
            before=u.count,
            after=len(got["items"]),
            key=len(key["items"]),
            lines=pp.summary(d.name, res, u.set_type) if res else [],
            ok=got == key,
            why="" if got == key else _why(got, key),
        )
    if not any(r.changed for r in results.values()):
        return _end(docs, "held", "revise_unchanged: no filed set would change", ok=not want_change, keep=True)
    return {"documents": docs, "request": {"outcome": "delivered", "reason": ""}, "ok": all(d["ok"] for d in docs)}


def _why(got: dict[str, Any], key: dict[str, Any]) -> str:
    out = []
    if got["terms"] != key["terms"]:
        out.append(f"definitions {got['terms']}, the key {key['terms']}")
    if got["items"] != key["items"]:
        diff = [n for (n, t), (m, u) in zip(got["items"], key["items"]) if (n, t) != (m, u)]
        out.append(f"{len(got['items'])} requests, the key {len(key['items'])}; first differing No. {diff[:5]}")
    if got["declaration"] != key["declaration"]:
        out.append("the declaration differs")
    return "; ".join(out)


def _end(docs: list[dict[str, Any]], outcome: str, reason: str, ok: bool = False, keep: bool = False) -> dict[str, Any]:
    if not keep:
        for d in docs:
            d.setdefault("ok", ok)
    return {"documents": docs, "request": {"outcome": outcome, "reason": reason[:400]}, "ok": ok}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobs.drafting.revise.propounded_rehearse")
    ap.add_argument("--units", type=Path, required=True)
    ap.add_argument("--request", type=Path, required=True)
    ap.add_argument("--recordings", type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    rep = rehearse(
        a.units, a.request.read_text(encoding="utf-8"), a.recordings or a.units / "recordings.jsonl", model=a.model,
        out=a.out,
    )  # fmt: skip
    for d in rep["documents"]:
        print(
            f"  {d['document']:<44} {'OK' if d.get('ok') else 'NO':<3} {d.get('before')}->{d.get('after')} {d.get('why', '')}"
        )
        for ln in d.get("lines") or []:
            print(f"      {ln}")
    req = rep["request"]
    print(f"request: {req['outcome']}" + (f" ({req['reason']})" if req["reason"] else ""))
    for n in rep["needed"]:
        print(f"  NEEDS A RECORDING: stage {n['stage']} sha {n['sha']}")
    print("REHEARSAL OK" if rep["ok"] else "REHEARSAL REFUSED")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
