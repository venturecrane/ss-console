"""`python -m jobs.medchron.sample <spec.yaml> [...] --out DIR`

Builds each matter's record set and its sample chronology, $0. One
subdirectory per spec under DIR (the repo's gitignored
``operator/customers/pilot-smokeball/seed/records/out/`` by convention)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline, spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("specs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    failed = 0
    for path in args.specs:
        plan = spec.load(path)
        b = pipeline.build(plan, args.out / plan.matter["slug"])
        report = json.loads((b.root / "build.json").read_text(encoding="utf-8"))
        print(json.dumps(report, indent=1))
        failed += len(b.problems)
    # A chronology that departs from its answer key is a failed rehearsal.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
