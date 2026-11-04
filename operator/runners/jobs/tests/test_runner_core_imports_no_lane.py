"""The runner core imports no lane (ADR 0103).

Until 2026-11-04 the chronology lane WAS the daemon's base class, so the core
imported the chronology's firm config, coverage, allowance and update modules,
and every lane inherited them. The core is generic now: each module below is
imported in a FRESH interpreter (nothing the test process already loaded can
hide an import), and afterwards no lane package (``jobs.medchron``,
``jobs.demand``, ``jobs.drafting``, ``jobs.litigation``, ``jobs.casefile``,
``jobs.negotiation``, ``jobs.records``) and no lane module (``jobs.*_lane``)
may be in ``sys.modules``.

``jobs.lanes`` is in the list on purpose: it names every lane, but imports each
one only inside ``_module`` when the lane is started, never at import.

THE CHECK CAN FAIL: ``test_the_check_fails_on_a_core_module_that_imports_a_lane``
copies the package, adds ``from .medchron import config`` to the copy's
``daemon.py`` and asserts the same check reports it. Run by hand on 2026-11-04
before the extraction, the check named nine ``jobs.medchron.*`` modules under
``jobs.daemon`` and two under ``jobs.llm``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

CORE = (
    "jobs.daemon",
    "jobs.lanes",
    "jobs.child",
    "jobs.llm",
    "jobs.firm_levers",
    "jobs.budget",
    "jobs.ledger",
    "jobs.limits",
    "jobs.proof_run",
    "jobs.proof_mode",
    "jobs.lane_proof",
)
LANE_PACKAGES = frozenset({"medchron", "demand", "drafting", "litigation", "casefile", "negotiation", "records"})

_PROBE = r"""
import importlib, json, sys
root = sys.argv[2]
if root:
    sys.path.insert(0, root)
importlib.import_module(sys.argv[1])
lanes = set(json.loads(sys.argv[3]))
print(json.dumps(sorted(
    m for m in sys.modules
    if m.startswith("jobs.") and (m.split(".")[1] in lanes or m.split(".")[1].endswith("_lane"))
)))
"""


def lane_modules_after_import(module: str, root: Path | None = None) -> list[str]:
    """The lane modules in ``sys.modules`` after importing ``module`` alone, in
    a fresh interpreter (``root``: a package copy to import from instead)."""
    r = subprocess.run(  # noqa: S603 - argv is this interpreter, a literal script and module names from CORE
        [sys.executable, "-c", _PROBE, module, str(root or ""), json.dumps(sorted(LANE_PACKAGES))],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),  # never the source tree: import what is installed
        timeout=120,
    )
    assert r.returncode == 0, f"importing {module} failed:\n{r.stderr[-2000:]}"
    return list(json.loads(r.stdout.strip().splitlines()[-1]))


@pytest.mark.parametrize("module", CORE)
def test_a_core_module_imports_no_lane(module: str) -> None:
    assert lane_modules_after_import(module) == [], f"{module} pulls in a lane"


def test_the_check_fails_on_a_core_module_that_imports_a_lane(tmp_path: Path) -> None:
    """FALSIFIER of the check above: the same probe on a copy whose daemon.py
    imports the chronology config must name it."""
    import jobs

    src = Path(jobs.__file__).resolve().parent
    dst = tmp_path / "pkg" / "jobs"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    daemon_py = dst / "daemon.py"
    daemon_py.write_text(daemon_py.read_text(encoding="utf-8") + "\nfrom .medchron import config as _bad\n")
    found = lane_modules_after_import("jobs.daemon", root=tmp_path / "pkg")
    assert "jobs.medchron.config" in found
