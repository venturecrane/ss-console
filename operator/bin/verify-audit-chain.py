#!/usr/bin/env python3
"""Verify an audit-ledger hash chain (#1686).

Walks a full ledger and proves the chain: every chained row's ``row_hash``
recomputes from its content + its parent's hash, exactly one chain start
(GENESIS or the legacy anchor), no forks, no unreachable segments. A mutated,
deleted, or inserted row is reported with its id and reason.

TAIL TRUNCATION IS NOT AN INTERNAL PROPERTY (ss#2500). Cutting rows off the END
leaves a valid chain behind, so this walk alone reports it INTACT. Measured, not
reasoned about: against a live 1,473-row export, deleting the last 50 rows,
deleting the last 1 row, and mutate-then-re-hash-everything-after all passed
(vfy_01M0H8D1CV2X8J9ZACMAC8E6E2). This header used to promise that comparing the
reported head "against an externally pinned head" caught it, and nothing pinned
one, so the promise was load-bearing and unfulfilled.

  --pinned-head HEX   a head recorded earlier, off the Machine. The export must
                      still contain it as some row's ``row_hash``; if it does
                      not, rows that existed when it was pinned are gone and
                      that is reported as a BREAK. Without this flag the tail is
                      unchecked, and the summary says so rather than implying a
                      completeness it did not test.

The console pins a head from every heartbeat into ``audit_head_history``
(migration 0108); ``.github/workflows/audit-chain-verify.yml`` supplies it daily.

Two input modes:

  --sqlite PATH   read audit_log directly (local/test ledgers)
  --json PATH     verify an audit_export payload pulled over the runtime-read
                  seam ({"entries": [...]} or a bare row array) — the offline
                  compliance-verification path. '-' reads stdin.

Exit 0 with a summary line when intact; exit 1 with the break list otherwise.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workspace_broker"))
from chain import CHAIN_COLUMNS, verify_chain

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.chain_pin import PIN_NOT_SUPPLIED, check_pinned_head


def rows_from_sqlite(path: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = [*CHAIN_COLUMNS, "prev_hash", "row_hash"]
        cur = conn.execute(f"SELECT {', '.join(cols)} FROM audit_log ORDER BY rowid")
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def rows_from_json(path: str) -> list[dict]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text()
    data = json.loads(raw)
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise SystemExit("json input must be an audit_export payload or a row array")
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sqlite", help="path to a ledger sqlite file")
    src.add_argument("--json", help="audit_export JSON path, or '-' for stdin")
    ap.add_argument(
        "--pinned-head",
        default=None,
        help=(
            "A chain head recorded earlier, off the Machine (audit_head_history "
            "in the console D1). The export must still contain it; if it does "
            "not, the tail was truncated or rewritten and that is a BREAK."
        ),
    )
    args = ap.parse_args()

    rows = rows_from_sqlite(args.sqlite) if args.sqlite else rows_from_json(args.json)
    report = verify_chain(rows)
    pin = check_pinned_head(rows, pinned_head=args.pinned_head, current_head=report["head"])

    # Both halves gate the verdict. An internally consistent chain whose pinned
    # head has vanished is not intact in any sense a client cares about, and
    # printing INTACT above a BREAK line would be read as the headline.
    ok = report["ok"] and pin["ok"]

    print(
        f"chain {'INTACT' if ok else 'BROKEN'}: "
        f"{report['chained']} chained, {report['legacy']} legacy, head={report['head']}"
    )
    for b in report["breaks"]:
        print(f"  BREAK id={b['id']}: {b['reason']}")
    if pin["verdict"] == PIN_NOT_SUPPLIED:
        # Never silent. A run with no pin proves strictly less than a run with
        # one, and a summary that does not say so is how this file's header came
        # to describe a check nobody was performing.
        print(f"  TAIL UNCHECKED: {pin['reason']}")
    elif pin["ok"]:
        print(f"  pin {pin['verdict']}: {pin['reason']}")
    else:
        print(f"  BREAK pin={pin['pinned_head']}: {pin['reason']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
