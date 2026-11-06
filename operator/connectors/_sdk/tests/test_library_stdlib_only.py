"""The library resolver stays case-system neutral and standard library only.

Two guards, both read from the source so they fail on an added import before
anything runs:

* no file anywhere in the SDK imports a connector package (every directory
  under ``operator/connectors/`` other than the SDK and the records contract is
  a connector, and its Python package is the one its pyproject builds);
* every file of ``operator_connector_sdk.library`` imports only the standard
  library and its own siblings. The rest of the SDK already depends on ``mcp``
  and ``pydantic`` (``server.py``, ``manifest.py``); the library package is the
  part the design requires to need nothing.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SDK = Path(__file__).resolve().parents[1] / "operator_connector_sdk"
CONNECTORS = SDK.parents[1]
LIBRARY = SDK / "library"


def _imports(path: Path) -> list[tuple[str, int]]:
    """(top-level module, relative level) for every import in ``path``."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            out += [(a.name.split(".")[0], 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(((node.module or "").split(".")[0], node.level))
    return out


def _connector_packages() -> set[str]:
    names: set[str] = set()
    for d in CONNECTORS.iterdir():
        if d.is_dir() and d.name not in {"_sdk", "_contract"} and (d / "pyproject.toml").exists():
            names |= {p.name for p in d.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return names


def test_there_are_connector_packages_to_guard_against() -> None:
    assert "smokeball_connector" in _connector_packages()


def test_the_sdk_imports_no_connector_package() -> None:
    connectors = _connector_packages()
    found = [
        f"{p.relative_to(SDK)} imports {mod}"
        for p in SDK.rglob("*.py")
        for mod, level in _imports(p)
        if level == 0 and (mod in connectors or mod.endswith("_connector"))
    ]
    assert found == []


def test_the_library_package_imports_only_the_standard_library() -> None:
    files = sorted(LIBRARY.glob("*.py"))
    assert {f.name for f in files} >= {"store.py", "ledger.py", "resolver.py", "memory.py", "__init__.py"}
    found = [
        f"{p.name} imports {mod}"
        for p in files
        for mod, level in _imports(p)
        if level == 0 and mod not in sys.stdlib_module_names and mod != "__future__"
    ]
    assert found == []


def test_the_library_package_reaches_no_sibling_outside_itself() -> None:
    """A relative import of level 2 or more would pull in the SDK's server
    (and with it ``mcp``) through the back door."""
    found = [f"{p.name}" for p in LIBRARY.glob("*.py") for _mod, level in _imports(p) if level > 1]
    assert found == []
