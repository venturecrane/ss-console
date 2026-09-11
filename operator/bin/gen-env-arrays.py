#!/usr/bin/env python3
"""Generate operator/templates/_env-arrays.generated.sh from the env contract.

Phase B Cut B. The generated file is COMMITTED (reviewable, version-controlled)
and CI asserts it is in sync with operator/contracts/env-consumption.yaml. A
malformed contract fails THIS generator in CI — never customer-zero's live boot.

Usage:
    python3 operator/bin/gen-env-arrays.py            # write the generated file
    python3 operator/bin/gen-env-arrays.py --check    # exit 1 if out of sync
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import env_arrays as ea


def main(argv: list[str]) -> int:
    check = "--check" in argv[1:]
    root = ea.repo_root()
    rendered = ea.render_from_contract(ea.contract_path(root))
    out = ea.generated_path(root)

    if check:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        if current != rendered:
            print(
                f"OUT OF SYNC: {out} does not match the contract projection.\n"
                "Run: python3 operator/bin/gen-env-arrays.py",
                file=sys.stderr,
            )
            return 1
        print(f"in sync: {out}")
        return 0

    out.write_text(rendered, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
