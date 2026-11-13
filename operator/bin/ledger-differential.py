#!/usr/bin/env python3
"""Do two versions of the broker's job ledger classes read a seat's audit.db
identically? Read-only, and it prints nothing from the file but counts.

WHY. Wave 2 moved the nine job ledgers onto one base class
(``workspace_broker/lane_ledger.py``). The goldens prove the new classes
write what the old ones wrote, on fixtures. This proves the new classes READ
what the old ones read, on a real file: every row a seat's ledger holds,
including rows written by versions no fixture remembers.

WHAT IT DOES. It opens ``--db`` (a COPY of an audit.db; never the live file)
read-only and copies it twice into a private temp dir. One copy is opened by
the OLD classes (``--base <sha>``: ``operator/workspace_broker`` at that
commit, from ``git archive``; or ``--old-tree <operator dir>``), the other by
the NEW classes (``--new-tree``, default this tree), each in its own Python
process so the two packages never meet. Each process constructs every ledger
class on its copy (its schema step runs, as at a seat's boot), then reads,
per table the class owns, every row raw through the class's connection, every
key through ``read()`` and ``project()``, and ``recent()`` / ``list_recent()``
and ``spend_between()`` where the class has them. It hands back a sha256 per
read, never a value; this process compares the two sides.

WHAT IT PRINTS. Per table: its row count and PASS or FAIL per read. Per class:
PASS or FAIL for its schema after opening and its whole-table reads. Never a
row, an id, a name or any text from the file: the file holds client request
text. A child that fails prints only its exception's type. The temp copies are
deleted on exit.

    python3 bin/ledger-differential.py --db /path/to/COPY-of-audit.db --base <merge-base sha>
    python3 bin/ledger-differential.py --db COPY.db --old-tree /tmp/old/operator [--new-tree operator]

Exit 0 when every read matches; 1 on any mismatch; 2 when it could not run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

OPERATOR = Path(__file__).resolve().parents[1]
REPO = OPERATOR.parent


def ledger_classes() -> list[tuple[str, Any]]:
    """Every broker job ledger class, by name (the same names at every version
    since the ledger goldens: ``bin/goldens/ledger-aged-schemas.py``), from
    whichever ``workspace_broker`` this process imports."""
    from workspace_broker import (
        casefile_store,
        demand_ledger,
        drafting_ledger,
        library_setup_ledger,
        litigation_ledger,
        medchron_ledger,
        negotiation_ledger,
        note_ledger,
        records_store,
    )

    return [
        ("MedchronLedger", medchron_ledger.MedchronLedger),
        ("DemandLedger", demand_ledger.DemandLedger),
        ("DraftingLedger", drafting_ledger.DraftingLedger),
        ("SetupLedger", library_setup_ledger.SetupLedger),
        ("LitigationLedger", litigation_ledger.LitigationLedger),
        ("CasefileLedger", casefile_store.CasefileLedger),
        ("NegotiationLedger", negotiation_ledger.NegotiationLedger),
        ("RecordsLedger", records_store.RecordsLedger),
        ("NoteLedger", note_ledger.NoteLedger),
    ]


# ---------------------------------------------------------------------------
# The child: one version's classes over one copy
# ---------------------------------------------------------------------------


def _digest(obj: Any) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _isolate(tree: Path) -> None:
    """Import ``workspace_broker`` from ``tree`` only: drop every other path
    that holds one and any editable-install finder that could supply one."""
    sys.path[:] = [str(tree)] + [p for p in sys.path if p and not (Path(p) / "workspace_broker").is_dir()]
    sys.meta_path[:] = [f for f in sys.meta_path if "editable" not in repr(f).lower()]


def _open(name: str, klass: Any, db: Path, queue: Path) -> Any:
    return klass(str(db)) if name == "NoteLedger" else klass(str(db), queue)


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schema(conn: sqlite3.Connection, tables: list[str]) -> Any:
    out = {}
    for t in tables:
        master = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = ? ORDER BY type, name", (t,)
        ).fetchall()
        info = conn.execute("SELECT * FROM pragma_table_info(?)", (t,)).fetchall()
        out[t] = {"master": [list(r) for r in master], "info": [list(r) for r in info]}
    return out


def _attempt(fn: Any, *args: Any) -> Any:
    try:
        return {"ok": fn(*args)}
    except Exception as exc:  # noqa: BLE001 - a refusal is a read result both versions must share
        return {"raised": type(exc).__name__}


def child(tree: Path, db: Path) -> dict[str, Any]:
    _isolate(tree)
    import workspace_broker

    if not Path(str(workspace_broker.__file__)).resolve().is_relative_to(tree.resolve()):
        raise RuntimeError("workspace_broker did not come from the tree asked for")
    work = Path(tempfile.mkdtemp(prefix="ledger-diff-child-"))
    out: dict[str, Any] = {"tables": {}, "classes": {}}
    try:
        for class_name, klass in ledger_classes():
            # The tables the class owns: what it creates on an empty file.
            fresh = work / f"{class_name}.db"
            _open(class_name, klass, fresh, work / "q-fresh")
            with sqlite3.connect(fresh) as c:
                owned = [t for t in _tables(c) if not t.startswith("sqlite_")]
            led = _open(class_name, klass, db, work / "q")
            conn = led._connect()
            try:
                schema = _schema(conn, owned)
                whole: dict[str, Any] = {"schema": schema}
                for t in owned:
                    # t is a table name read from sqlite_master of a file this class just
                    # created, quoted as an identifier (an identifier takes no bound parameter).
                    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    raw = [dict(r) for r in conn.execute(f"SELECT * FROM {_quoted(t)} ORDER BY rowid")]  # noqa: S608 - t is an identifier this class created, quoted
                    pk = [r[1] for r in conn.execute("SELECT * FROM pragma_table_info(?)", (t,)) if r[5]]
                    reads, projects = [], []
                    if len(pk) == 1:
                        for row in raw:
                            got = _attempt(led.read, row[pk[0]])
                            reads.append(got)
                            row_read = got.get("ok")
                            if isinstance(row_read, dict) and hasattr(type(led), "project"):
                                projects.append(_attempt(type(led).project, row_read))
                    out["tables"][t] = {
                        "class": class_name,
                        "rows": len(raw),
                        "raw": _digest(raw),
                        "read": _digest(reads),
                        "project": _digest(projects),
                    }
            finally:
                conn.close()
            recent = getattr(led, "recent", None) or getattr(led, "list_recent", None)
            if recent is not None:
                whole["recent"] = _attempt(recent, 1_000_000)
            if hasattr(led, "spend_between"):
                whole["spend"] = _attempt(led.spend_between, "0000", "9999")
            out["classes"][class_name] = {"tables": owned, "whole": _digest(whole)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


# ---------------------------------------------------------------------------
# The parent: two copies, two children, one comparison
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed git argv over this repo
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def old_tree_at(sha: str, dest: Path, repo: Path = REPO) -> Path:
    """``operator/workspace_broker`` as it was at ``sha``, written under ``dest``
    file by file (``git show``; no archive to unpack)."""
    names = _git(repo, "ls-tree", "-r", "--name-only", sha, "operator/workspace_broker").decode().split("\n")
    for name in filter(None, names):
        if "/tests/" in name or "/__pycache__/" in name:
            continue
        out = dest / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_git(repo, "show", f"{sha}:{name}"))
    return dest / "operator"


def _copy(src: Path, dest: Path) -> None:
    """A private copy of ``src``, read through a read-only connection."""
    ro = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    rw = sqlite3.connect(dest)
    try:
        ro.backup(rw)
    finally:
        rw.close()
        ro.close()
    dest.chmod(0o600)


def _run_child(tree: Path, db: Path) -> dict[str, Any]:
    proc = subprocess.run(  # noqa: S603 - this script, run on a tree and a private copy
        [sys.executable, "-I", str(Path(__file__).resolve()), "--child", str(tree), str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"a reader failed ({proc.stdout.strip() or 'no message'})")
    return json.loads(proc.stdout)


def compare(old: dict[str, Any], new: dict[str, Any]) -> tuple[list[str], bool]:
    lines: list[str] = []
    ok = True
    for t in sorted(set(old["tables"]) | set(new["tables"])):
        a, b = old["tables"].get(t), new["tables"].get(t)
        if a is None or b is None:
            lines.append(f"table {t}: FAIL (owned by one version only)")
            ok = False
            continue
        marks = []
        for key in ("raw", "read", "project"):
            same = a[key] == b[key]
            ok &= same
            marks.append(f"{key}={'PASS' if same else 'FAIL'}")
        count = f"rows={a['rows']}" if a["rows"] == b["rows"] else f"rows={a['rows']}/{b['rows']}"
        lines.append(f"table {t}: {count} {' '.join(marks)}")
    for c in sorted(set(old["classes"]) | set(new["classes"])):
        a, b = old["classes"].get(c), new["classes"].get(c)
        same = a is not None and b is not None and a == b
        ok &= same
        lines.append(f"class {c}: schema+reads={'PASS' if same else 'FAIL'}")
    return lines, ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--db", type=Path, help="a COPY of an audit.db (opened read-only)")
    ap.add_argument("--base", help="the commit whose classes are the OLD side (git archive)")
    ap.add_argument("--old-tree", type=Path, help="an operator/ dir whose classes are the OLD side")
    ap.add_argument("--new-tree", type=Path, default=OPERATOR, help="the NEW side's operator/ dir (default: this tree)")
    ap.add_argument("--child", nargs=2, metavar=("TREE", "DB"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.child:
        try:
            print(json.dumps(child(Path(args.child[0]), Path(args.child[1]))))
        except Exception as exc:  # noqa: BLE001 - only the type leaves this process: a message may quote the file
            print(type(exc).__name__)
            return 2
        return 0
    if args.db is None or not args.db.is_file() or (args.base is None) == (args.old_tree is None):
        print("usage: --db <copy of audit.db> and exactly one of --base <sha> / --old-tree <operator dir>")
        return 2
    work = Path(tempfile.mkdtemp(prefix="ledger-diff-"))
    try:
        old_tree = args.old_tree or old_tree_at(args.base, work / "old")
        old_db, new_db = work / "old.db", work / "new.db"
        _copy(args.db, old_db)
        _copy(args.db, new_db)
        old = _run_child(old_tree.resolve(), old_db)
        new = _run_child(args.new_tree.resolve(), new_db)
    except Exception as exc:  # noqa: BLE001 - only the type is printed: a message may quote the file
        print(f"could not run: {type(exc).__name__}")
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)
    lines, ok = compare(old, new)
    print("\n".join(lines))
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
