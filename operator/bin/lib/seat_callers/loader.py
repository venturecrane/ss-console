"""Import a site's module the way its own tree does.

Each tree is importable from a root (the overlay, the operator tree, the job
runner, a connector); a file under a root whose path is a dotted name is
imported by that name, so its relative imports resolve as they do in its CI
job. Anything else (a hyphenated script, a skill's pre_run.py, a plugin folder)
is loaded from its path with its own folder first on sys.path, which is how
`python <file>` and the scheduler's staging run it.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

IDENT = re.compile(r"^[A-Za-z_]\w*$")
# Staged as files on a seat and run as scripts: loaded from their path, never as a package.
SCRIPT_TREES = {"templates", "skills"}


def roots(repo: Path) -> list[Path]:
    op = repo / "operator"
    found = [op / "runners" / "jobs"]
    connectors = op / "connectors"
    if connectors.is_dir():
        found += sorted(p for p in connectors.iterdir() if p.is_dir() and not p.name.startswith("."))
    found += [op / "hermes-smd-overlay", op / "bin", op]
    return found


def tree_of(rel: str) -> str:
    """The tree a site belongs to, by path."""
    parts = rel.split("/")
    table = {
        "hermes-smd-overlay": "overlay",
        "workspace_broker": "broker",
        "runners": "runner",
        "connectors": "connector",
        "skills": "skill",
        "bin": "bin",
        "templates": "templates",
    }
    return table.get(parts[1], parts[1]) if len(parts) > 1 else "other"


def setup_path(repo: Path) -> None:
    for root in reversed(roots(repo)):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))


def _dotted(path: Path, repo: Path) -> str | None:
    for root in roots(repo):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and parts[0] in SCRIPT_TREES:
            return None
        if parts and all(IDENT.match(p) for p in parts) and (root / parts[0]).is_dir():
            return ".".join(parts)
    return None


def _from_path(name: str, path: Path, package: bool) -> ModuleType:
    locations = [str(path.parent)] if package else None
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=locations)
    if spec is None or spec.loader is None:
        raise ImportError(f"no spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sanitise(text: str) -> str:
    return re.sub(r"\W", "_", text)


def load(path: Path, repo: Path) -> ModuleType:
    dotted = _dotted(path, repo)
    if dotted is not None:
        # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import - the name is derived from a tracked repo file the capture discovered; importing it is the instrument's purpose
        return importlib.import_module(dotted)
    sys.path.insert(0, str(path.parent))
    if (path.parent / "__init__.py").exists() and not IDENT.match(path.parent.name):
        package = "seatcall_" + _sanitise(path.parent.name)
        if package not in sys.modules:
            _from_path(package, path.parent / "__init__.py", package=True)
        if path.name == "__init__.py":
            return sys.modules[package]
        # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import - a submodule of a tracked plugin folder the capture discovered
        return importlib.import_module(f"{package}.{path.stem}")
    return _from_path("seatcall_" + _sanitise(path.stem), path, package=False)


def resolve(module: ModuleType, qualname: str) -> object:
    obj: object = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj
