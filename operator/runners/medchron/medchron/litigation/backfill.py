"""Backfill the sent-out-for-service date onto a lane's stored state.

``python -m medchron.litigation.backfill --state <state dir> --rows <rows.json> [--dry-run]``

``rows.json`` is a JSON list of ``{"matter_id", "defendant", "date",
"source": {"file_id", "name", "doc_date"} | null}`` (``baseline.merge_sent_for_service``).
Prints the counts as one JSON line. ``--dry-run`` writes nothing.

Client data: the rows file is read from the path given and lives where the
client's data lives; nothing here carries any.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .baseline import merge_sent_for_service


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m medchron.litigation.backfill",
        description="Backfill the sent-out-for-service date onto a lane's stored state.",
    )
    ap.add_argument("--state", required=True, type=Path, help="the lane's state dir (holds matters/<id>.json)")
    ap.add_argument("--rows", required=True, type=Path, help="a JSON list of rows")
    ap.add_argument("--dry-run", action="store_true", help="count only; write nothing")
    a = ap.parse_args(argv)
    if not (a.state / "matters").is_dir():
        print(f"no matters/ under {a.state}", file=sys.stderr)
        return 2
    rows = json.loads(a.rows.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("--rows must hold a JSON list", file=sys.stderr)
        return 2
    counts = merge_sent_for_service(a.state, rows, dry_run=a.dry_run)
    print(json.dumps({**counts, "rows": len(rows), "dry_run": a.dry_run}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
