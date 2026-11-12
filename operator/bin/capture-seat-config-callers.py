#!/usr/bin/env python3
"""Capture the seat-config caller table (wave 2, Phase A, item A2).

Every customer.yaml hand parser, broker socket client and seat-slug environment read in
the operator tree is EXECUTED over a fixed input set and what it returned or
raised is written to operator/contracts/seat-config-callers.json. The table is
the instrument the Phase B readers migration is checked against: a migrated
site must reproduce its row, or the difference is a reviewed change.

    python3 operator/bin/capture-seat-config-callers.py            # print the summary
    python3 operator/bin/capture-seat-config-callers.py --write    # regenerate the table
    python3 operator/bin/capture-seat-config-callers.py --check    # exit 1 if the table is stale

Runs in the environment of the operator-substrate pytest step (pytest, pyyaml,
httpx, lxml, pydantic and mcp at the seat's pins, the operator tree and the
overlay installed): a site whose import needs a module that is absent is
recorded not-executable rather than run. The package doing the work is
operator/bin/lib/seat_callers/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "operator"))

from bin.lib.seat_callers.capture import capture, document  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.diff import differences  # noqa: E402 - imported only once sys.path holds the operator tree

TABLE = REPO / "operator" / "contracts" / "seat-config-callers.json"


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write the table")
    ap.add_argument("--check", action="store_true", help="exit 1 when the committed table differs")
    ap.add_argument("--out", type=Path, default=None, help="write the table here instead")
    ap.add_argument("--only", default="", help="only sites whose file path contains this text")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--with-real-digests", action="store_true", help="also digest real customer.yaml results")
    args = ap.parse_args(argv)
    doc = document(capture(REPO, jobs=args.jobs, only=args.only, real_digests=args.with_real_digests))
    text = render(doc)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    if args.write:
        TABLE.write_text(text, encoding="utf-8")
    if args.check:
        found = differences(json.loads(TABLE.read_text(encoding="utf-8")), doc)
        for line in found:
            print(line)
        if found:
            print(
                f"\n{len(found)} difference(s). Regenerate: python3 operator/bin/capture-seat-config-callers.py --write"
            )
            return 1
    print(json.dumps(doc["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
