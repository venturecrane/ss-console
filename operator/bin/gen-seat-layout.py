#!/usr/bin/env python3
"""Generate every seat-layout output from operator/contracts/seat-layout.yaml.

The outputs are COMMITTED (each tree on a seat imports its own copy) and CI
asserts they are in sync (operator/bin/tests/test_seat_layout_generation.py).

Usage (from operator/):
    python3 bin/gen-seat-layout.py            # write every output
    python3 bin/gen-seat-layout.py --check    # exit 1 naming each stale output
"""

from __future__ import annotations

import sys

from bin.lib import seat_layout_gen as gen


def main(argv: list[str]) -> int:
    check = "--check" in argv[1:]
    root = gen.repo_root()
    stale = []
    for path, text in gen.outputs(root).items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == text:
            continue
        if check:
            stale.append(path.relative_to(root))
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(root)}")
    if stale:
        names = "\n  ".join(str(p) for p in stale)
        print(
            f"OUT OF SYNC with {gen.CONTRACT_REL}:\n  {names}\nRun: python3 operator/bin/gen-seat-layout.py",
            file=sys.stderr,
        )
        return 1
    if check:
        print("seat layout outputs in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
