#!/usr/bin/env python3
"""Rebuild every broker job ledger's HISTORICAL schemas from git, as fixtures.

WHY. A ledger on a seat was created by whatever version of its ``ensure_schema``
ran first there, then migrated forward by every later one. A refactor of the
ledgers must open each of those aged files exactly as today's code does. The
schemas a seat can hold are the schemas each historical version created on an
empty file; this script recreates them and writes them to
``workspace_broker/tests/goldens/ledgers/<lane>/aged.json``, which
``workspace_broker/tests/test_ledger_goldens.py`` opens with today's ledger
class and compares (``PRAGMA table_info``, ``sqlite_master``) with its golden.

HOW. For each commit that changed a lane's schema-owning files
(``git log``; a copy from another lane's file is not its history, so renames are not followed), the whole ``workspace_broker`` package is copied to a
temp dir with those files replaced by their content at that commit, imported
under a fresh name, and the lane's ledger class is built on an empty file: its
own ``ensure_schema`` of that day runs. A version that cannot be imported any
more (a sibling it named is gone) falls back to executing, in source order,
every ``CREATE``/``ALTER`` string constant that version declares. Consecutive
commits that produce the same schema are one step. Run from ``operator/``:

    python3 bin/goldens/ledger-aged-schemas.py

It needs the full history (``fetch-depth: 0``) and is NOT run by CI: its output
is the committed fixture, regenerated only when a ledger's schema changes.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

OPERATOR = Path(__file__).resolve().parents[2]
REPO = OPERATOR.parent
BROKER = OPERATOR / "workspace_broker"
OUT = BROKER / "tests" / "goldens" / "ledgers"
# A copied sibling that imports ``workspace_broker.<x>`` absolutely (audit_ledger
# does, for chain) gets today's module: only the lane's own files are aged.
if str(OPERATOR) not in sys.path:
    sys.path.insert(0, str(OPERATOR))

#: Each ledger: (the files whose history is its schema, the module and class
#: built on an empty file). Its lane, the golden dir's name, is its first
#: file's name without ``_ledger.py`` (``lane_of``): the lane list has one
#: home, ``contracts/job-lanes.yaml``, and this table names files, not lanes.
LEDGERS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("medchron_ledger.py", "medchron_credits.py"), "medchron_ledger", "MedchronLedger"),
    (("demand_ledger.py",), "demand_ledger", "DemandLedger"),
    (("drafting_ledger.py",), "drafting_ledger", "DraftingLedger"),
    (("litigation_ledger.py",), "litigation_ledger", "LitigationLedger"),
    (("negotiation_ledger.py",), "negotiation_ledger", "NegotiationLedger"),
    (("casefile_ledger.py", "casefile_store.py"), "casefile_store", "CasefileLedger"),
    (("records_ledger.py", "records_store.py"), "records_store", "RecordsLedger"),
    (("note_ledger.py",), "note_ledger", "NoteLedger"),
    (("library_setup_ledger.py",), "library_setup_ledger", "SetupLedger"),
)


def lane_of(owners: tuple[str, ...]) -> str:
    return owners[0].removesuffix("_ledger.py")


LANES = {lane_of(row[0]): row for row in LEDGERS}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout  # noqa: S603 - fixed git argv in this repo


def history(path: str) -> list[tuple[str, str]]:
    """(commit, path), oldest first."""
    rel = f"operator/workspace_broker/{path}"
    out = git("log", "--name-only", "--format=@%H %cI", "--", rel)
    rows: list[tuple[str, str, str]] = []
    cur: tuple[str, str] | None = None
    for line in out.splitlines():
        if line.startswith("@"):
            sha, when = line[1:].split(" ", 1)
            cur = (sha, when)
        elif line.strip() and cur is not None:
            rows.append((cur[1], cur[0], line.strip()))
            cur = None
    return [(sha, p) for _when, sha, p in sorted(rows)]


def show(sha: str, path: str) -> str | None:
    try:
        return git("show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None


def schema(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    conn.close()
    return [f"{t} {n}: {s}" for t, n, s in rows]


def ddl_strings(source: str) -> list[str]:
    """Every module-level CREATE/ALTER string constant, in source order."""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.strip()
            if v.upper().startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE", "ALTER TABLE")):
                out.append(v)
    return out


_N = [0]


def _load(pkg: Path, name: str, module: str) -> Any:
    """``<name>.<module>`` from the copied package dir, by file location (the
    package registered first, so the module's relative imports resolve inside
    the copy)."""
    spec = importlib.util.spec_from_file_location(name, pkg / "__init__.py", submodule_search_locations=[str(pkg)])
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    sub = importlib.util.spec_from_file_location(f"{name}.{module}", pkg / f"{module}.py")
    assert sub is not None and sub.loader is not None
    mod = importlib.util.module_from_spec(sub)
    sys.modules[f"{name}.{module}"] = mod
    sub.loader.exec_module(mod)
    return mod


def build(files: dict[str, str], module: str, cls: str, db: Path, queue: Path) -> str:
    """Build the lane's ledger of that commit on ``db``. Returns the method."""
    _N[0] += 1
    name = f"wbaged{_N[0]}"
    tmp = Path(tempfile.mkdtemp(prefix="wbaged"))
    pkg = tmp / name
    shutil.copytree(BROKER, pkg, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    for fname, text in files.items():
        (pkg / fname).write_text(text.replace("from workspace_broker.", f"from {name}."), encoding="utf-8")
    sys.path.insert(0, str(tmp))
    try:
        klass = getattr(_load(pkg, name, module), cls)
        if cls == "NoteLedger":
            klass(str(db))
        else:
            klass(str(db), queue)
        return "import"
    except Exception as exc:  # noqa: BLE001 - an old version that no longer imports falls back to its DDL
        conn = sqlite3.connect(db)
        for text in files.values():
            for stmt in ddl_strings(text):
                try:
                    conn.execute(stmt)
                except sqlite3.Error:
                    pass
        conn.commit()
        conn.close()
        return f"ddl ({type(exc).__name__})"
    finally:
        sys.path.remove(str(tmp))
        shutil.rmtree(tmp, ignore_errors=True)


def lane_steps(lane: str) -> list[dict[str, Any]]:
    owners, module, cls = LANES[lane]
    hist = {owner: history(owner) for owner in owners}
    order: list[str] = []
    for owner in owners:
        order += [sha for sha, _p in hist[owner] if sha not in order]
    dated = sorted(order, key=lambda s: git("show", "-s", "--format=%cI", s).strip())
    steps: list[dict[str, Any]] = []
    last: list[str] | None = None
    for sha in dated:
        files: dict[str, str] = {}
        for owner in owners:
            # The owner's content at this commit, at the path it had at its
            # latest change on or before it (a rename keeps its history).
            mine = [p for s, p in hist[owner] if dated.index(s) <= dated.index(sha)]
            text = show(sha, mine[-1]) if mine else None
            if text is not None:
                files[owner] = text
        if not files:
            continue
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "audit.db"
            q = Path(td) / "queue"
            q.mkdir()
            method = build(files, module, cls, db, q)
            got = schema(db)
        if got and got != last:
            steps.append({"commit": sha[:12], "method": method, "schema": got})
            last = got
    return steps


def main() -> int:
    for lane in sorted(LANES):
        steps = lane_steps(lane)
        dest = OUT / lane
        dest.mkdir(parents=True, exist_ok=True)
        doc = {
            "_why": "Each schema an empty file got from one historical version of this lane's ledger "
            "(bin/goldens/ledger-aged-schemas.py, from git history). test_ledger_goldens.py opens each "
            "with today's ledger class and compares the migrated schema with its golden.",
            "steps": steps,
        }
        (dest / "aged.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(f"{lane}: {len(steps)} historical schema steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
