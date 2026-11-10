#!/usr/bin/env python3
"""Seat check: the live seat matches its declared folder layout.

Run as root with the hermes venv python (stdlib only). The boot smoke sends this
file's text to the Machine over ssh; the in-image self-test runs the image's copy
at /app/seat-layout-probe.py. One program, two transports
(operator/templates/seat-checks.sh, check id ``seat-layout-live``).

WHY. Seat paths are declared once in operator/contracts/seat-layout.yaml and
generated into each install tree (operator/bin/gen-seat-layout.py). A generated
constant is only as good as its agreement with the running seat, and a seat is
where a wrong path would show: this probe reads the seat back.

THREE THINGS, against the image's /app/seat-layout.generated.json:

1. Each declared path with an ``expect`` holds: ``present`` exists with its
   ``kind``; ``absent`` does not exist; ``lazy`` may be missing but has its kind
   when it exists. A ``when: lanes`` path is judged only on a seat that runs a
   job lane, decided the way the self-test decides it (shared.capabilities).
2. Each install tree whose interpreter is on the seat imports its own generated
   ``seat_layout`` and agrees with the image on every id both declare. A tree
   with no interpreter here is skipped (a seat that runs no lane mounts no jobs
   bundle); a tree whose interpreter cannot import its module fails.
3. Every running process that carries a declared ``env`` variable carries the
   declared value. Values are read from /proc/<pid>/environ as root and only the
   declared names are compared or printed: never another variable.

NOTHING TO COMPARE, A PASS THAT SAYS SO: an image built before the layout
existed has no /app/seat-layout.generated.json, and a rollback re-smoke runs
this checkout's probe against it.

``--capture`` prints what the seat has for every declared path and env name, as
JSON, for a before/after comparison around a release. It judges nothing.

FALSIFIER: move a declared directory, plant /opt/data/customer.yaml, give one
tree's module a different value, or start a process with JOBS_DATA_DIR pointing
elsewhere, and this exits 1 naming it. operator/templates/tests/test_selftest.py
plants each one.

SMD_SEAT_ROOT prefixes every seat path; it is unset on a seat and set by the
tests to a fixture tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = os.environ.get("SMD_SEAT_ROOT", "")
LAYOUT = Path(f"{ROOT}/app/seat-layout.generated.json")
# Restated in operator/contracts/seat-layout.yaml: this file travels over ssh
# alone, so it cannot import a tree's generated module. The generation test pins
# these to the yaml.
HERMES_PYTHON3 = "/opt/hermes/.venv/bin/python3"
JOBS_PYTHON = "/opt/jobs/.venv/bin/python"
BROKER_PYTHON = "/opt/workspace-broker/.venv/bin/python"
BROKER_ROOT = "/opt/workspace-broker"
TREES = (
    ("overlay", HERMES_PYTHON3, "shared.seat_layout", ""),
    ("jobs", JOBS_PYTHON, "jobs.seat_layout", ""),
    ("broker", BROKER_PYTHON, "workspace_broker.seat_layout", BROKER_ROOT),
)
IMPORT = (
    "import importlib, json, sys\n"
    "if sys.argv[2]: sys.path.insert(0, sys.argv[2])\n"
    "print(json.dumps(importlib.import_module(sys.argv[1]).PATHS))\n"
)
TIMEOUT_S = 30


def _lanes_run(paths: dict) -> bool:
    """True when the seat runs a job lane; unknown counts as yes, as in selftest.sh."""
    yaml_path = paths.get("customer_yaml", {}).get("path", "")
    try:
        done = subprocess.run(  # noqa: S603 - the seat's own interpreter and module
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - fixed argv and no shell; the one env-derived token, SMD_SEAT_ROOT, is unset on a seat and set only by the self-test fixtures to a tmp tree
            [f"{ROOT}{HERMES_PYTHON3}", "-I", "-m", "shared.capabilities", "lanes", f"{ROOT}{yaml_path}"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return done.returncode != 0 or bool(done.stdout.strip())


def _path_problem(pid: str, spec: dict) -> str | None:
    target = Path(f"{ROOT}{spec['path']}")
    expect, kind = spec.get("expect"), spec.get("kind")
    exists = os.path.lexists(target)
    if expect == "absent":
        return f"{pid}: {spec['path']} must be absent and exists" if exists else None
    if not exists:
        return None if expect == "lazy" else f"{pid}: {spec['path']} is missing"
    if kind == "dir" and not target.is_dir():
        return f"{pid}: {spec['path']} is not a directory"
    if kind == "file" and not target.is_file():
        return f"{pid}: {spec['path']} is not a regular file"
    return None


def check_paths(paths: dict) -> list[str]:
    lanes: bool | None = None
    problems = []
    for pid, spec in paths.items():
        if not spec.get("expect"):
            continue
        if spec.get("when") == "lanes":
            lanes = _lanes_run(paths) if lanes is None else lanes
            if not lanes:
                continue
        problem = _path_problem(pid, spec)
        if problem:
            problems.append(problem)
    return problems


def check_trees(paths: dict) -> tuple[list[str], list[str]]:
    problems, seen = [], []
    declared = {pid: spec["path"] for pid, spec in paths.items()}
    for name, interpreter, module, extra in TREES:
        exe = f"{ROOT}{interpreter}"
        if not os.access(exe, os.X_OK):
            continue
        try:
            done = subprocess.run(  # noqa: S603 - the seat's own interpreter and generated module
                # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - fixed argv and no shell; the one env-derived token, SMD_SEAT_ROOT, is unset on a seat and set only by the self-test fixtures to a tmp tree
                [exe, "-I", "-c", IMPORT, module, f"{ROOT}{extra}" if extra else ""],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                check=False,
            )
            theirs = json.loads(done.stdout) if done.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            theirs = None
        if not isinstance(theirs, dict):
            problems.append(f"{name}: {interpreter} cannot import {module}")
            continue
        seen.append(name)
        problems += [
            f"{name}: {pid} is {theirs[pid]}, the image declares {path}"
            for pid, path in sorted(declared.items())
            if pid in theirs and theirs[pid] != path
        ]
    return problems, seen


def _environs() -> list[tuple[str, dict[str, str]]]:
    out = []
    for proc in sorted(Path(f"{ROOT}/proc").glob("[0-9]*")):
        try:
            raw = (proc / "environ").read_bytes()
        except OSError:
            continue  # the process ended, or is a kernel thread
        pairs = (item.split(b"=", 1) for item in raw.split(b"\0") if b"=" in item)
        out.append((proc.name, {k.decode(errors="replace"): v.decode(errors="replace") for k, v in pairs}))
    return out


def check_env(paths: dict) -> tuple[list[str], int]:
    """An EMPTY value is an unset one: entrypoint-lanes.sh passes every lane's
    queue var to the broker and sets it empty when the lane is off, so its verbs
    fail closed. Only a non-empty value that differs is a mismatch."""
    wanted = {name: spec["path"] for spec in paths.values() for name in spec.get("env", [])}
    problems, scanned = [], 0
    for pid, env in _environs():
        scanned += 1
        problems += [
            f"pid {pid}: {name} is {env[name]}, the layout declares {path}"
            for name, path in sorted(wanted.items())
            if env.get(name) and env[name] != path
        ]
    return problems, scanned


def capture(paths: dict) -> dict:
    facts = {}
    for pid, spec in paths.items():
        target = Path(f"{ROOT}{spec['path']}")
        kind = "dir" if target.is_dir() else "file" if target.is_file() else "other"
        facts[pid] = {"path": spec["path"], "exists": os.path.lexists(target), "kind": kind}
    names = {name for spec in paths.values() for name in spec.get("env", [])}
    values: dict[str, dict[str, int]] = {}
    for _, env in _environs():
        for name in names & env.keys():
            bucket = values.setdefault(name, {})
            bucket[env[name]] = bucket.get(env[name], 0) + 1
    return {"paths": facts, "env": values}


def main(argv: list[str]) -> int:
    if not LAYOUT.is_file():
        print(f"no {LAYOUT.name} on this image (built before the seat layout); nothing to compare")
        return 0
    paths = json.loads(LAYOUT.read_text(encoding="utf-8"))["paths"]
    if "--capture" in argv[1:]:
        print(json.dumps(capture(paths), indent=2, sort_keys=True))
        return 0
    problems = check_paths(paths)
    tree_problems, trees = check_trees(paths)
    env_problems, scanned = check_env(paths)
    problems += tree_problems + env_problems
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"layout holds: {len(paths)} ids, trees {','.join(trees) or 'none here'}, {scanned} processes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
