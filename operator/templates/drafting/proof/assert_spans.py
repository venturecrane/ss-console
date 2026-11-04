#!/usr/bin/env python3
"""Prove every cite, quote, figure and date of a rendered brief is code's.

    assert_spans.py --ledger ledger.jsonl --spans spans.jsonl --rendered brief.md
                    [--authorities <store dir>] [--charges charges.json] [--json out.json]

A thin command over ``evidence.render.assert_spans``: each span re-renders
from the ledger to exactly its text, still stands in the rendered brief, and
nothing outside the spans is a hand-typed cite, quotation, figure or date.
Exit 0 when every span traces, 1 otherwise. Offline: no network, no model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import runner_path


def run(
    ledger: Path, spans: Path, rendered: Path, authorities: Path | None = None, charges: Path | None = None
) -> dict[str, Any]:
    render = runner_path.render()
    if render is None:
        raise SystemExit("evidence.render is not in this tree")
    sh = runner_path.shapes()
    led = sh.Ledger.from_jsonl(ledger.read_text(encoding="utf-8"))
    ss = [sh.Span.from_json(json.loads(ln)) for ln in spans.read_text(encoding="utf-8").splitlines() if ln.strip()]
    store = None
    if authorities is not None:
        runner_path.ensure()
        from jobs.drafting.verify import authorities as auth

        store = auth.Store.from_dir(authorities)
    table = json.loads(charges.read_text(encoding="utf-8")) if charges else None
    rep = render.assert_spans(rendered.read_text(encoding="utf-8"), ss, led, charges=table, authorities=store)
    return {
        "ok": rep.ok,
        "spans": rep.spans,
        "traced": rep.traced,
        "problems": [{"code": f.code, "message": f.message, "snippet": f.snippet[:120]} for f in rep.problems],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--spans", required=True, type=Path)
    ap.add_argument("--rendered", required=True, type=Path)
    ap.add_argument("--authorities", type=Path)
    ap.add_argument("--charges", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    r = run(a.ledger, a.spans, a.rendered, a.authorities, a.charges)
    if a.json:
        a.json.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    pct = 100.0 * r["traced"] / r["spans"] if r["spans"] else 100.0
    print(f"spans {r['spans']}  traced {r['traced']} ({pct:.1f}%)  problems {len(r['problems'])}")
    for p in r["problems"][:40]:
        print(f"  [{p['code']}] {p['message']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
