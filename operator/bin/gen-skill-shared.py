#!/usr/bin/env python3
"""Write every shared-module copy declared in operator/contracts/skill-shared-modules.yaml,
and every shared SKILL.md section declared in operator/contracts/skill-text-blocks.yaml.

The copies are COMMITTED and byte-identical to their sources (each skill folder
ships alone, and the broker, gateway, job runner and connectors each run in a
venv of their own), and CI asserts they are in sync
(operator/bin/tests/test_skill_shared_generation.py).

Usage:
    python3 operator/bin/gen-skill-shared.py            # write every copy that differs
    python3 operator/bin/gen-skill-shared.py --check    # exit 1 naming each problem
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run from anywhere: put operator/ on the path, as gen-seat-layout.py does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bin.lib import skill_shared_gen as gen
from bin.lib import skill_text_blocks as blocks_gen


def main(argv: list[str]) -> int:
    check = "--check" in argv[1:]
    root = gen.repo_root()
    modules = gen.load(root / gen.CONTRACT_REL)
    blocks = blocks_gen.load(root / blocks_gen.CONTRACT_REL)
    if not check:
        for path in [*gen.stamp(root, modules), *blocks_gen.stamp(root, blocks)]:
            print(f"wrote {path.relative_to(root)}")
    failed = False
    for contract, found in (
        (gen.CONTRACT_REL, gen.problems(root, modules)),
        (blocks_gen.CONTRACT_REL, blocks_gen.problems(root, blocks)),
    ):
        if found:
            failed = True
            lines = "\n  ".join(found)
            print(
                f"OUT OF SYNC with {contract}:\n  {lines}\n"
                f"Edit the source, never a copy, then run: python3 {gen.GENERATOR}",
                file=sys.stderr,
            )
    if failed:
        return 1
    if check:
        count = len(gen.copies(root, modules))
        sections = sum(len(b.skills) for b in blocks)
        print(
            f"shared modules in sync: {len(modules)} modules, {count} copies; "
            f"shared SKILL.md sections in sync: {len(blocks)} blocks, {sections} stamped"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
