"""Function-size census for the Python operator tree: length and complexity.

WHY. The TypeScript half enforces `max-lines-per-function: 75` and
`complexity: 15` at error level with zero violations (eslint.config.js). The
Python half, 451 files, had neither: 99 non-test functions over 75 logical
lines and 32 over McCabe 15 on 2026-09-10, the worst a 313-line, cx-51
authorization if-chain. This module is the counter; the ratchet test
(bin/tests/test_function_size_ratchet.py) both enforces and regenerates
operator/contracts/operator-function-size.json from it, on the model of
tests/operator-module-size.test.ts: one counter, one artifact, so the gate
fires on growth and never on a disagreement between two counters.

WHAT IT COUNTS.
  * length: logical lines of a `def`/`async def` body, blanks, comment-only
    lines and the docstring excluded, the same yardstick the module-size
    ratchet and eslint's `skipBlankLines/skipComments` use. Nested functions
    count toward their enclosing function (they are its complexity too).
  * complexity: ruff's McCabe (C901) with max-complexity 15, read from
    `ruff check --isolated --select C901 --output-format json`, so the number
    here is the number ruff would print, not a re-implementation.

Test files (test_*.py, *_test.py, anything under a tests/ dir) are exempt for
the same reason eslint.config.js exempts them: a long table of cases is not
the complexity the ceiling is aimed at.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

LENGTH_CEILING = 75
COMPLEXITY_CEILING = 15
SKIP_PARTS = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".rendered", "node_modules"}


def is_test_file(rel: Path) -> bool:
    base = rel.name
    return base.startswith("test_") or base.endswith("_test.py") or "tests" in rel.parts


def python_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if SKIP_PARTS.intersection(rel.parts) or is_test_file(rel):
            continue
        out.append(p)
    return out


def _logical_lines(source_lines: list[str], node: ast.AST) -> int:
    """Non-blank, non-comment lines of a def, docstring excluded."""
    start = node.lineno  # 1-based, the `def` line
    end = node.end_lineno or start
    doc_start = doc_end = -1
    body = getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        if isinstance(body[0].value.value, str):
            doc_start, doc_end = body[0].lineno, body[0].end_lineno or body[0].lineno
    count = 0
    for ln in range(start, end + 1):
        if doc_start <= ln <= doc_end:
            continue
        text = source_lines[ln - 1].strip()
        if not text or text.startswith("#"):
            continue
        count += 1
    return count


def function_lengths(path: Path, rel: str) -> dict[str, int]:
    """`{ "rel/path.py::qualname": logical_lines }` for every def in the file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    lines = source.splitlines()
    out: dict[str, int] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                out[f"{rel}::{qual}"] = _logical_lines(lines, child)
                walk(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def ruff_complexities(root: Path, ruff: str = "ruff") -> dict[str, int]:
    """`{ "rel/path.py::name": complexity }` for every def over the ceiling, from ruff."""
    proc = subprocess.run(
        [
            ruff,
            "check",
            "--isolated",
            "--select",
            "C901",
            "--config",
            f"lint.mccabe.max-complexity={COMPLEXITY_CEILING}",
            "--target-version",
            "py311",
            "--output-format",
            "json",
            "--no-cache",
            ".",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ruff C901 run failed: {proc.stderr.strip() or proc.stdout.strip()}")
    out: dict[str, int] = {}
    for item in json.loads(proc.stdout or "[]"):
        rel = Path(item["filename"]).resolve().relative_to(root.resolve())
        if is_test_file(rel) or SKIP_PARTS.intersection(rel.parts):
            continue
        # message: "`name` is too complex (23 > 15)"
        msg = item["message"]
        name = msg.split("`")[1]
        cx = int(msg.rsplit("(", 1)[1].split(" ")[0])
        out[f"{rel.as_posix()}::{name}"] = cx
    return out


def census(root: Path, ruff: str = "ruff") -> dict:
    """Everything over either ceiling, in the baseline's shape."""
    lengths: dict[str, int] = {}
    for p in python_files(root):
        rel = p.relative_to(root).as_posix()
        for key, n in function_lengths(p, rel).items():
            if n > LENGTH_CEILING:
                lengths[key] = n
    complexity = ruff_complexities(root, ruff)
    return {
        "length_ceiling": LENGTH_CEILING,
        "complexity_ceiling": COMPLEXITY_CEILING,
        "length": dict(sorted(lengths.items())),
        "complexity": dict(sorted(complexity.items())),
    }


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[2]
    print(json.dumps(census(root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
