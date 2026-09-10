"""Function-size ratchet: no Python function grows past 75 logical lines or
McCabe 15 unless it already had, and the baselined ones only shrink.

Model: tests/operator-module-size.test.ts. ONE counter (bin/lib/function_size.py)
both enforces and regenerates operator/contracts/operator-function-size.json,
so the gate can only fire on real growth, never on two counters disagreeing.

REGENERATE (after shrinking a baselined function, or after a deliberate,
reviewed addition that the PR explains):

    UPDATE_OPERATOR_FUNCTION_SIZE_BASELINE=1 python3 -m pytest bin/tests/test_function_size_ratchet.py -q

THE RATCHET ONLY TIGHTENS. A baselined function that grows fails. One that
shrinks (or crosses back under the ceiling) fails too, with a message saying
to regenerate, so the recorded number tracks reality downward. A function not
in the baseline fails the moment it crosses either ceiling.

Why it exists: the 2026-09-10 code review measured 99 non-test functions over
75 lines and 32 over complexity 15, against a TypeScript half holding both at
zero. Nothing in CI could see the count move. This can. The broker's 313-line
cx-51 `handle()` is entry number one, and wave 3 of that review takes it out.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

OPERATOR = Path(__file__).resolve().parents[2]
BASELINE = OPERATOR / "contracts" / "operator-function-size.json"
sys.path.insert(0, str(OPERATOR / "bin" / "lib"))

import function_size as fs  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

RUFF = shutil.which("ruff")
pytestmark = pytest.mark.skipif(RUFF is None, reason="ruff is not on PATH (the substrate job installs it)")

COMMENT = (
    "Functions over the ceilings, generated and enforced by "
    "bin/tests/test_function_size_ratchet.py from bin/lib/function_size.py. Do not hand-edit. "
    "Regenerate with UPDATE_OPERATOR_FUNCTION_SIZE_BASELINE=1 python3 -m pytest "
    "bin/tests/test_function_size_ratchet.py. A function leaves this file by getting smaller "
    "or simpler; nothing may enter it by getting bigger."
)


def _current() -> dict:
    return fs.census(OPERATOR, RUFF or "ruff")


def _load() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _write(current: dict) -> None:
    BASELINE.write_text(json.dumps({"_comment": COMMENT, **current}, indent=2) + "\n", encoding="utf-8")


def _compare(kind: str, baseline: dict[str, int], current: dict[str, int]) -> list[str]:
    problems = []
    for key, n in current.items():
        was = baseline.get(key)
        if was is None:
            problems.append(f"NEW over the {kind} ceiling: {key} = {n} (split it; the ceiling is not a target)")
        elif n > was:
            problems.append(f"GREW: {key} {kind} {was} -> {n} (split it, or shrink it back)")
        elif n < was:
            problems.append(f"SHRANK (good): {key} {kind} {was} -> {n}; regenerate the baseline so it ratchets down")
    for key in baseline:
        if key not in current:
            problems.append(f"LEFT the {kind} list (good): {key}; regenerate the baseline")
    return problems


def test_function_size_ratchet() -> None:
    current = _current()
    if os.environ.get("UPDATE_OPERATOR_FUNCTION_SIZE_BASELINE") == "1":
        _write(current)
        pytest.skip("baseline regenerated")
    assert BASELINE.exists(), f"missing {BASELINE}; run with UPDATE_OPERATOR_FUNCTION_SIZE_BASELINE=1"
    baseline = _load()
    assert baseline["length_ceiling"] == fs.LENGTH_CEILING
    assert baseline["complexity_ceiling"] == fs.COMPLEXITY_CEILING
    problems = _compare("length", baseline["length"], current["length"])
    problems += _compare("complexity", baseline["complexity"], current["complexity"])
    assert not problems, "\n".join(problems)


def test_the_counter_can_fail(tmp_path: Path) -> None:
    """Law 12: a check that cannot fail has measured nothing."""
    src = tmp_path / "pkg"
    src.mkdir()
    body = "\n".join(f"    x{i} = {i}" for i in range(fs.LENGTH_CEILING + 5))
    (src / "big.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")
    branches = "\n".join(f"    if n == {i}:\n        return {i}" for i in range(fs.COMPLEXITY_CEILING + 2))
    (src / "twisty.py").write_text(f"def twisty(n):\n{branches}\n    return -1\n", encoding="utf-8")
    (src / "test_big.py").write_text(f"def big():\n{body}\n    return x0\n", encoding="utf-8")
    result = fs.census(src, RUFF or "ruff")
    assert result["length"] == {"big.py::big": fs.LENGTH_CEILING + 7}, result["length"]  # def + 80 assignments + return
    assert "twisty.py::twisty" in result["complexity"]
    assert not any(k.startswith("test_") for k in result["length"]), "test files must be exempt"


def test_docstrings_and_comments_do_not_count() -> None:
    lines = ['def f():', '    """doc', '    more doc', '    """', '    # comment', '', '    return 1']
    import ast

    tree = ast.parse("\n".join(lines))
    fn = tree.body[0]
    assert fs._logical_lines(lines, fn) == 2  # the def line and the return


def test_baseline_shape_matches_the_counter() -> None:
    baseline = _load()
    assert set(baseline) == {"_comment", "length_ceiling", "complexity_ceiling", "length", "complexity"}
    for key, n in {**baseline["length"], **baseline["complexity"]}.items():
        assert "::" in key and isinstance(n, int) and n > 0, key


def test_the_broker_handle_is_baselined_not_forgotten() -> None:
    """The review's headline entry. If it leaves this file it was split, which is the point."""
    baseline = _load()
    key = "workspace_broker/server.py::handle"  # ruff keys C901 by bare function name
    if key in baseline["complexity"]:
        assert baseline["complexity"][key] > fs.COMPLEXITY_CEILING
    else:
        assert not any(k.startswith("workspace_broker/server.py::") for k in baseline["complexity"]), (
            "handle() left the baseline but another server.py function is over the ceiling"
        )


@pytest.fixture(autouse=True)
def _module_is_loadable() -> None:
    spec = importlib.util.find_spec("function_size")
    assert spec is not None
