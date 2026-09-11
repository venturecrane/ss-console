"""The pyright ratchet's comparator, on fixtures, plus the baseline's shape.

pyright itself needs node and runs in the substrate job, not here; what this
pins is that the comparator fails on growth, demands a regenerate on
improvement, holds on equality, and that the committed baseline is the shape
the comparator reads.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2]
SCRIPT = OPERATOR / "bin" / "pyright-ratchet.py"
BASELINE = OPERATOR / "contracts" / "operator-pyright-baseline.json"

_spec = importlib.util.spec_from_file_location("pyright_ratchet", SCRIPT)
assert _spec and _spec.loader
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


def _report(errors: dict[str, int], warnings: int = 0) -> dict:
    diags = []
    for f, n in errors.items():
        diags += [{"file": str(OPERATOR / f), "severity": "error", "message": "x"}] * n
    diags += [{"file": str(OPERATOR / "a.py"), "severity": "warning", "message": "w"}] * warnings
    return {"generalDiagnostics": diags, "summary": {}}


def test_counts_errors_only_per_file() -> None:
    counts = pr.errors_per_file(_report({"a/b.py": 2, "c.py": 1}, warnings=5))
    assert counts == {"a/b.py": 2, "c.py": 1}


def test_diagnostics_outside_the_operator_root_are_not_ours() -> None:
    """The interpreter's stdlib is analyzed but never baselined: its path is
    machine-specific and its errors are pyright's, not the tree's."""
    report = _report({"a.py": 1})
    report["generalDiagnostics"].append(
        {"file": "/opt/hostedtoolcache/Python/3.14.7/x64/lib/python3.14/string/templatelib.py", "severity": "error", "message": "x"}
    )
    assert pr.errors_per_file(report) == {"a.py": 1}


def test_growth_fails_and_improvement_asks_to_regenerate() -> None:
    base = {"a.py": 2, "b.py": 1}
    assert pr.compare(base, {"a.py": 2, "b.py": 1}) == []
    grew = pr.compare(base, {"a.py": 3, "b.py": 1})
    assert len(grew) == 1 and grew[0].startswith("GREW: a.py 2 -> 3")
    new_file = pr.compare(base, {"a.py": 2, "b.py": 1, "c.py": 1})
    assert len(new_file) == 1 and new_file[0].startswith("GREW: c.py 0 -> 1")
    shrank = pr.compare(base, {"a.py": 1, "b.py": 1})
    assert len(shrank) == 1 and shrank[0].startswith("SHRANK")
    clean = pr.compare(base, {"a.py": 2})
    assert len(clean) == 1 and clean[0].startswith("CLEAN")


def test_cli_exit_codes(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report({"a.py": 2})), encoding="utf-8")
    assert pr.main([str(report), "--update", "--baseline", str(baseline)]) == 0
    assert json.loads(baseline.read_text())["files"] == {"a.py": 2}
    assert pr.main([str(report), "--baseline", str(baseline)]) == 0
    report.write_text(json.dumps(_report({"a.py": 3})), encoding="utf-8")
    assert pr.main([str(report), "--baseline", str(baseline)]) == 1
    report.write_text(json.dumps(_report({"a.py": 1})), encoding="utf-8")
    assert pr.main([str(report), "--baseline", str(baseline)]) == 1  # improved: regenerate


def test_committed_baseline_is_well_formed() -> None:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(data) == {"_comment", "total", "files"}
    assert data["total"] == sum(data["files"].values())
    for f, n in data["files"].items():
        assert (OPERATOR / f).is_file(), f"baseline names a file that no longer exists: {f}"
        assert isinstance(n, int) and n > 0
