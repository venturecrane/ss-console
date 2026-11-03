"""The package has no runtime dependency, and the copy of the overlay's capitals list matches it.

Two venvs with different closures (the Hermes venv and the jobs-bundle venv)
install this package, so it may import the standard library and nothing else.
FALSIFIER: add ``import yaml`` to any module, or a name to ``dependencies``,
and the first two tests fail.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
import venv
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OVERLAY_CHECKLIST = PACKAGE.parents[1] / "hermes-smd-overlay" / "shared" / "output_checklist.py"


def test_pyproject_declares_no_dependency() -> None:
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []
    assert "optional-dependencies" not in project


def test_imports_with_no_site_packages() -> None:
    # -I: no user site, no PYTHONPATH, no cwd; -S: no site-packages at all.
    code = f"import sys; sys.path.insert(0, {str(PACKAGE)!r}); import records_contract.facts, records_contract.batches, records_contract.review_row"
    subprocess.run([sys.executable, "-I", "-S", "-c", code], check=True)  # noqa: S603 - our own interpreter, a fixed argv


def test_installs_into_a_bare_venv_and_imports_isolated(tmp_path: Path) -> None:
    env = tmp_path / "v"
    venv.create(env, with_pip=True)
    py = env / "bin" / "python"
    subprocess.run(  # noqa: S603 - the scratch venv's own pip, a fixed argv
        [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", str(PACKAGE)],
        check=True,
    )
    probe = (
        "import importlib.metadata as m, records_contract; "
        "assert not m.requires('operator-records-contract'), m.requires('operator-records-contract'); "
        "print(records_contract.__file__)"
    )
    out = subprocess.run(  # noqa: S603 - the scratch venv's interpreter, a fixed argv
        [str(py), "-I", "-c", probe], check=True, capture_output=True, text=True
    ).stdout
    assert str(env) in out


def test_the_overlay_takes_known_caps_from_this_package() -> None:
    """One source: the overlay's checklist assigns KNOWN_CAPS from records_contract.facts.
    FALSIFIER: give output_checklist.py its own literal list again and this fails."""
    tree = ast.parse(OVERLAY_CHECKLIST.read_text(encoding="utf-8"))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "records_contract.facts"
        for alias in node.names
        if alias.name == "KNOWN_CAPS"
    }
    assert imported, "output_checklist.py does not import KNOWN_CAPS from records_contract.facts"
    assigned = [
        node.value
        for node in tree.body
        if isinstance(node, (ast.AnnAssign, ast.Assign))
        and any(getattr(t, "id", "") == "KNOWN_CAPS" for t in getattr(node, "targets", [getattr(node, "target", None)]))
    ]
    assert assigned, "KNOWN_CAPS is no longer assigned in output_checklist.py"
    assert all(isinstance(v, ast.Name) and v.id in imported for v in assigned), (
        "output_checklist.KNOWN_CAPS must be the records_contract value, not its own list"
    )
