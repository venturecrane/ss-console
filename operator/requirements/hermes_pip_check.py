#!/usr/bin/env python3
"""`uv pip check` for the Hermes venv, with named exceptions and no others.

A plain ``uv pip check`` on /opt/hermes/.venv reports two kinds of deliberate
mismatch, and the image runs this instead so the build fails on anything else.

1. **Our overrides** (``TOLERATED``). hermes-security.txt moves a package off
   the version hermes-agent's own pyproject exact-pins, to the release that
   fixes a named advisory. At v0.22.5 that is brotlicffi: hermes-agent pins
   ``brotlicffi==1.2.0.2`` in its ``messaging`` extra, and the image installs
   1.2.1.0 (PYSEC-2026-4323). The table names each package with the ONE
   requirer whose pin it breaks, and the mismatch is tolerated only while the
   installed version is exactly the version hermes-security.txt pins.

2. **Hermes' own overrides.** Since v0.22 Hermes' ``[tool.uv]
   override-dependencies`` replace exact pins its dependencies declare
   (browser-harness pins ``pillow==12.3.0``; the lock resolves Pillow 13.0.0).
   ``uv sync --frozen`` installs the locked version, the installed distribution
   still declares the original pin, and ``uv pip check`` reports it. uv.lock
   records the overrides it applied (``[manifest] overrides``); a report line
   is tolerated exactly when it names an overridden dependency installed at the
   version the lock resolved. This is the rule Hermes' own installer applies
   (``pm/pip_check.py`` at v0.22.5).

Every other incompatibility uv reports (any other package, any other requirer,
a missing package, an installed version that is not the pin) fails, as does any
uv output this script cannot read. Remove a ``TOLERATED`` row with its line in
hermes-security.in once Hermes' own pin carries the fix.

Usage (the Dockerfile's call)::

    python3 -I hermes_pip_check.py /opt/hermes/.venv/bin/python \\
        /app/requirements/hermes-security.txt /opt/hermes/uv.lock

and compile.sh's, which prints the lock's overrides as requirement lines for
``uv pip compile --override``::

    python3 -I hermes_pip_check.py --lock-overrides <uv.lock>
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

#: package -> the single requirer whose exact pin our override breaks.
#: Each row is a line in hermes-security.in; nothing else is negotiable.
TOLERATED: dict[str, str] = {"brotlicffi": "hermes-agent"}

_COUNT = re.compile(r"^Found (\d+) incompatibilit(?:y|ies)$")
_LINE = re.compile(r"^The package `([^`]+)` requires `([^`]+)`, but (.+)$")
_INSTALLED = re.compile(r"^`([^`]+)` is installed$")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned_version(requirements: Path, package: str) -> str | None:
    for line in requirements.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m and _norm(m.group(1)) == _norm(package):
            return m.group(2)
    return None


def _lock(lock: Path) -> dict:
    with lock.open("rb") as fh:
        return tomllib.load(fh)


def lock_override_requirements(lock: Path) -> list[str]:
    """uv.lock's ``[manifest] overrides`` as requirement lines (``uv pip compile --override``)."""
    lines: list[str] = []
    for entry in _lock(lock).get("manifest", {}).get("overrides", []):
        if set(entry) - {"name", "specifier", "marker"}:
            # A source override (URL, path, git) has no requirement-line form.
            raise ValueError(f"unsupported uv.lock override: {entry}")
        line = entry["name"] + entry.get("specifier", "")
        if entry.get("marker"):
            line += " ; " + entry["marker"]
        lines.append(line)
    return lines


def lock_overridden_versions(lock: Path) -> dict[str, set[str]]:
    """The versions uv.lock resolved for every dependency its manifest overrides."""
    data = _lock(lock)
    overridden = {_norm(e["name"]) for e in data.get("manifest", {}).get("overrides", [])}
    versions: dict[str, set[str]] = {}
    for package in data.get("package", []):
        name = _norm(package.get("name", ""))
        if name in overridden and "version" in package:
            versions.setdefault(name, set()).add(package["version"])
    return versions


def judge(
    output: str,
    returncode: int,
    pins: dict[str, str | None],
    lock_versions: dict[str, set[str]] | None = None,
) -> list[str]:
    """Return the problems in a ``uv pip check`` result; empty means the venv passes.

    ``pins`` maps each ``TOLERATED`` package to the version hermes-security.txt
    pins (None when the file does not pin it, which tolerates nothing).
    ``lock_versions`` is ``lock_overridden_versions`` of Hermes' uv.lock.
    """
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if returncode == 0:
        return []
    count = next((int(m.group(1)) for ln in lines if (m := _COUNT.match(ln))), None)
    incompat = [ln for ln in lines if ln.startswith("The package `")]
    if count is None or count != len(incompat):
        return [f"cannot read uv pip check output (exit {returncode}):\n{output.strip()}"]
    lock_versions = lock_versions or {}
    problems: list[str] = []
    for ln in incompat:
        m = _LINE.match(ln)
        if m is None:
            problems.append(ln)
            continue
        requirer, requirement, rest = m.groups()
        req_name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        installed = _INSTALLED.match(rest)
        if req_name is None or installed is None:
            problems.append(ln)
            continue
        package, version = _norm(req_name.group(0)), installed.group(1)
        ours = (
            package in TOLERATED
            and _norm(requirer) == TOLERATED[package]
            and pins.get(package) is not None
            and version == pins[package]
        )
        hermes = version in lock_versions.get(package, set())
        if ours:
            print(f"tolerated: {ln} (hermes-security.txt override)")
        elif hermes:
            print(f"tolerated: {ln} (Hermes uv.lock override)")
        else:
            problems.append(ln)
    return problems


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--lock-overrides":
        try:
            print("\n".join(lock_override_requirements(Path(argv[2]))))
        except ValueError as exc:
            print(f"hermes_pip_check: {exc}", file=sys.stderr)
            return 1
        return 0
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    python, requirements, lock = argv[1], Path(argv[2]), Path(argv[3])
    if not lock.is_file():
        print(f"hermes venv: no uv.lock at {lock}", file=sys.stderr)
        return 2
    pins = {package: pinned_version(requirements, package) for package in TOLERATED}
    proc = subprocess.run(  # noqa: S603 - fixed uv argv, no shell; only the venv path varies
        ["uv", "pip", "check", "--python", python],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr
    print(output.strip())
    problems = judge(output, proc.returncode, pins, lock_overridden_versions(lock))
    if problems:
        print("hermes venv: unmet requirements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("hermes venv: every requirement met (named overrides tolerated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
