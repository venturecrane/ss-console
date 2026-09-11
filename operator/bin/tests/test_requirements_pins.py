"""The seat image's Python is pinned, hashed, and complete (2026-09-10 review).

Every third-party package a seat venv installs comes from a file under
``operator/requirements/`` that ``compile.sh`` wrote with ``--generate-hashes``,
and the Dockerfile installs each with ``--require-hashes``. Two things can
silently undo that, and this test refuses both:

1. A pyproject grows a dependency and nobody re-runs ``compile.sh``. The image
   would install the local package with ``--no-deps`` and the connector or
   runner would fail at import on a live seat, not at build. So: every
   declared dependency of every pyproject the image installs must have an
   exact ``==`` pin in its matching requirements file.
2. A requirements file is edited by hand and loses its hashes, or gains a
   range. So: every pin line carries at least one ``--hash=sha256:`` and no
   line carries a range operator.

Run::

    cd operator && python3 -m pytest bin/tests/test_requirements_pins.py -q
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

OPERATOR = Path(__file__).resolve().parents[2]
REQUIREMENTS = OPERATOR / "requirements"

#: Our own packages, resolved from the tree and installed with --no-deps; they
#: are deliberately absent from the requirement files (a path has no hash).
LOCAL_PACKAGES = {"operator-connector-sdk", "smokeball-connector", "medchron"}

PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")
RANGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*(>=|<=|~=|!=|<|>)")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    names: set[str] = set()
    for dep in deps:
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", dep)
        assert m, f"{pyproject}: cannot parse dependency {dep!r}"
        names.add(_normalize(m.group(1)))
    return names


def _pinned(requirements: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in requirements.read_text(encoding="utf-8").splitlines():
        m = PIN_RE.match(line)
        if m:
            pins[_normalize(m.group(1))] = m.group(2)
    return pins


def _image_venvs() -> list[tuple[Path, Path]]:
    """(pyproject, requirements file) for every venv the Dockerfile builds from a pyproject."""
    pairs: list[tuple[Path, Path]] = []
    for cdir in sorted((OPERATOR / "connectors").iterdir()):
        if cdir.name == "_sdk" or not (cdir / "pyproject.toml").is_file():
            continue
        pairs.append((cdir / "pyproject.toml", REQUIREMENTS / f"connector-{cdir.name}.txt"))
    pairs.append((OPERATOR / "runners/medchron/pyproject.toml", REQUIREMENTS / "medchron.txt"))
    return pairs


def test_every_connector_and_runner_has_a_requirements_file() -> None:
    missing = [str(req.name) for _, req in _image_venvs() if not req.is_file()]
    assert missing == [], f"run operator/requirements/compile.sh; missing: {missing}"


@pytest.mark.parametrize("pyproject,requirements", _image_venvs(), ids=lambda p: p.name)
def test_every_declared_dependency_is_pinned(pyproject: Path, requirements: Path) -> None:
    declared = _declared(pyproject) - LOCAL_PACKAGES
    # The connector SDK is installed into every connector venv too; its own
    # dependencies must be pinned in the connector's file.
    if "connectors" in pyproject.parts:
        declared |= _declared(OPERATOR / "connectors/_sdk/pyproject.toml") - LOCAL_PACKAGES
    if pyproject.parent.name == "medchron":
        declared |= _declared(OPERATOR / "connectors/smokeball/pyproject.toml") - LOCAL_PACKAGES
        declared |= _declared(OPERATOR / "connectors/_sdk/pyproject.toml") - LOCAL_PACKAGES
    pinned = _pinned(requirements)
    unpinned = sorted(declared - set(pinned))
    assert unpinned == [], (
        f"{pyproject.relative_to(OPERATOR)} declares {unpinned} but "
        f"requirements/{requirements.name} does not pin them; run compile.sh"
    )


def test_broker_inputs_are_pinned() -> None:
    inputs = {
        _normalize(line.strip())
        for line in (REQUIREMENTS / "broker.in").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    pinned = _pinned(REQUIREMENTS / "broker.txt")
    assert inputs <= set(pinned), f"broker.in names {sorted(inputs - set(pinned))} unpinned"


@pytest.mark.parametrize("requirements", sorted(REQUIREMENTS.glob("*.txt")), ids=lambda p: p.name)
def test_every_pin_is_exact_and_hashed(requirements: Path) -> None:
    text = requirements.read_text(encoding="utf-8")
    lines = text.splitlines()
    ranges = [ln for ln in lines if RANGE_RE.match(ln)]
    assert ranges == [], f"{requirements.name} carries ranges: {ranges}"
    # A pin's hashes follow on continuation lines; walk each pin to its block end.
    unhashed: list[str] = []
    i = 0
    while i < len(lines):
        m = PIN_RE.match(lines[i])
        if m:
            block = [lines[i]]
            j = i + 1
            while j < len(lines) and lines[j].startswith(" "):
                block.append(lines[j])
                j += 1
            if not any("--hash=sha256:" in b for b in block):
                unhashed.append(m.group(1))
            i = j
        else:
            i += 1
    assert unhashed == [], f"{requirements.name} pins without hashes: {unhashed}"
    assert _pinned(requirements), f"{requirements.name} pins nothing"


def test_the_local_packages_are_never_pinned_by_a_file() -> None:
    # They install from the tree with --no-deps; a stale pin here would shadow
    # the tree's version and make the image lie about what it runs.
    for requirements in REQUIREMENTS.glob("*.txt"):
        leaked = LOCAL_PACKAGES & set(_pinned(requirements))
        assert leaked == set(), f"{requirements.name} pins local package(s) {sorted(leaked)}"
