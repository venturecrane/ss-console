#!/usr/bin/env python3
"""pyright, ratcheted: no file's error count may grow; the baseline only tightens.

WHY. 451 Python files and no type checker (code review 2026-09-10, Code
Quality 7); 67% of defs carried a return annotation that nothing verified.
Turning pyright on outright would need every existing error fixed in one PR.
This is the module-size ratchet's shape instead: contracts/
operator-pyright-baseline.json records errors per file at adoption (111 in 45
files on 2026-09-10 after the three undefined names it found were fixed;
basic mode, pyproject.toml [tool.pyright]); a file whose
count grows fails, a file whose count shrinks fails with "regenerate", so the
number only moves down and a new error anywhere is a red check.

RUN (the substrate job does exactly this):

    cd operator && pyright --outputjson > /tmp/pyright.json || true
    python3 bin/pyright-ratchet.py /tmp/pyright.json            # exit 1 on growth
    python3 bin/pyright-ratchet.py /tmp/pyright.json --update   # regenerate

Pinned by bin/tests/test_pyright_ratchet.py (the comparator, on fixtures, and
the baseline's shape). pyright itself is not run under pytest: it needs node,
which the bare pytest venv does not have.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[1]
BASELINE = OPERATOR / "contracts" / "operator-pyright-baseline.json"
COMMENT = (
    "pyright errors per file at adoption, generated and enforced by bin/pyright-ratchet.py. "
    "Do not hand-edit. Regenerate with `pyright --outputjson > r.json; python3 bin/pyright-ratchet.py r.json --update`. "
    "A file leaves this list by reaching zero; no count may grow."
)


def errors_per_file(report: dict, root: Path = OPERATOR) -> dict[str, int]:
    """Errors per operator-relative file. A diagnostic outside the root is the
    interpreter's own stdlib (pyright 1.1.406 cannot parse Python 3.14's
    string/templatelib.py, for one) and is dropped: it is not our code, and
    its absolute path differs between a laptop and the runner, so it would
    make the baseline machine-specific (it did, on 2026-09-10)."""
    counts: dict[str, int] = {}
    for d in report.get("generalDiagnostics", []):
        if d.get("severity") != "error":
            continue
        f = Path(d["file"])
        try:
            rel = f.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        counts[rel] = counts.get(rel, 0) + 1
    return dict(sorted(counts.items()))


def compare(baseline: dict[str, int], current: dict[str, int]) -> list[str]:
    problems = []
    for f, n in current.items():
        was = baseline.get(f, 0)
        if n > was:
            problems.append(f"GREW: {f} {was} -> {n} pyright error(s); fix them, the ceiling is not a target")
        elif n < was:
            problems.append(f"SHRANK (good): {f} {was} -> {n}; regenerate with --update so it ratchets down")
    for f, was in baseline.items():
        if f not in current:
            problems.append(f"CLEAN (good): {f} {was} -> 0; regenerate with --update so it leaves the list")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("report", type=Path, help="pyright --outputjson output")
    p.add_argument("--update", action="store_true")
    p.add_argument("--baseline", type=Path, default=BASELINE)
    args = p.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    current = errors_per_file(report)
    total = sum(current.values())
    if args.update:
        args.baseline.write_text(
            json.dumps({"_comment": COMMENT, "total": total, "files": current}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"baseline written: {total} error(s) across {len(current)} file(s)")
        return 0
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["files"]
    problems = compare(baseline, current)
    growth = [x for x in problems if x.startswith("GREW")]
    for line in problems:
        print(line)
    if growth:
        print(f"pyright ratchet: {len(growth)} file(s) grew; {total} error(s) now")
        return 1
    if problems:
        print(f"pyright ratchet: the tree improved; regenerate the baseline (--update) so it ratchets down")
        return 1
    print(f"pyright ratchet: holding at {total} error(s) across {len(current)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
