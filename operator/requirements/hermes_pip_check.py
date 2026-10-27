#!/usr/bin/env python3
"""`uv pip check` for the Hermes venv, with exactly one named exception.

The seat image installs hermes-security.txt over Hermes' locked set, moving
certifi off the version hermes-agent's own pyproject exact-pins
(``certifi==2026.5.20``) to the release that fixes PYSEC-2026-4320 (emSign
roots with compromised intermediate CA keys). A plain ``uv pip check`` then
reports that one requirement as unmet and fails the build, so the image runs
this instead.

It tolerates ONE incompatibility, named here in code: hermes-agent's
requirement on certifi, and only while the installed certifi is exactly the
version hermes-security.txt pins. Every other incompatibility uv reports
(any other package, any other requirer, a missing package, an installed
certifi that is not our pin) fails, as does any uv output this script cannot
read. Delete the exception once HERMES_UPSTREAM_SHA moves to a Hermes whose
own pin carries the fix: the image then needs no override and this reduces to
``uv pip check``.

Usage (the Dockerfile's call)::

    python3 -I hermes_pip_check.py /opt/hermes/.venv/bin/python /app/requirements/hermes-security.txt
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: The single tolerated (requirer, package) pair. Nothing else is negotiable.
TOLERATED_REQUIRER = "hermes-agent"
TOLERATED_PACKAGE = "certifi"

_COUNT = re.compile(r"^Found (\d+) incompatibilit(?:y|ies)$")
_LINE = re.compile(r"^The package `([^`]+)` requires `([^`]+)`, but (.+)$")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned_version(requirements: Path, package: str) -> str | None:
    for line in requirements.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m and _norm(m.group(1)) == _norm(package):
            return m.group(2)
    return None


def judge(output: str, returncode: int, override_version: str | None) -> list[str]:
    """Return the problems in a ``uv pip check`` result; empty means the venv passes."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if returncode == 0:
        return []
    count = next((int(m.group(1)) for ln in lines if (m := _COUNT.match(ln))), None)
    incompat = [ln for ln in lines if ln.startswith("The package `")]
    if count is None or count != len(incompat):
        return [f"cannot read uv pip check output (exit {returncode}):\n{output.strip()}"]
    problems: list[str] = []
    for ln in incompat:
        m = _LINE.match(ln)
        if m is None:
            problems.append(ln)
            continue
        requirer, requirement, rest = m.groups()
        req_name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        tolerated = (
            _norm(requirer) == TOLERATED_REQUIRER
            and req_name is not None
            and _norm(req_name.group(0)) == TOLERATED_PACKAGE
            and override_version is not None
            and rest == f"`{override_version}` is installed"
        )
        if tolerated:
            print(f"tolerated: {ln} (hermes-security.txt override)")
        else:
            problems.append(ln)
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    python, requirements = argv[1], Path(argv[2])
    override = pinned_version(requirements, TOLERATED_PACKAGE)
    proc = subprocess.run(  # noqa: S603 - fixed uv argv, no shell; only the venv path varies
        ["uv", "pip", "check", "--python", python],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr
    print(output.strip())
    problems = judge(output, proc.returncode, override)
    if problems:
        print("hermes venv: unmet requirements:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("hermes venv: every requirement met (one named override tolerated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
