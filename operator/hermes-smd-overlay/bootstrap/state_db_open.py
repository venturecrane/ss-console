"""Open a Hermes ``state.db`` once, before the gateway does, and prove it migrated.

WHY (ss-console#3564, the v0.22.5 re-promotion; boot-harness volume run on a
restored smd-staging snapshot). The first v0.22.5 open of an EMPTY
v2026.9.14-layout ``state.db`` (schema 30, no messages) raises::

    sqlite3.OperationalError: no such savepoint: fts_align_empty

``_migrate_misaligned_fts_source`` (``hermes_state_schema.py:392-401`` @
v0.22.5) opens ``SAVEPOINT fts_align_empty`` and then calls
``_ensure_fts_schema``, whose ``executescript`` COMMITs the open transaction and
with it the savepoint, so both ``RELEASE`` and the ``ROLLBACK TO`` in the
handler raise. The COMMIT has already landed the realigned FTS schema, so the
SECOND open finds nothing to align and completes at schema 32 with an intact
database (reproduced offline on a v2026.9.14-created file:
``tests/fixtures/hermes_state/``). On a seat the first open is the gateway's,
inside the overlay's smd-job-worker thread (``shared/job_worker_runtime.py``),
so the first v0.22 boot of such a seat ran with no durable-job worker.

This module is that first open, done where a failure is visible. bootstrap.sh
runs it once per ``state.db`` the gateway can open, before the gateway exec,
as ``HERMES_HOME=<the db's home> python3 -I -m bootstrap.state_db_open <db>``:

1. Read the schema version, read-only.
2. Open ``SessionDB(db_path=<db>)`` and close it. Exactly the
   ``no such savepoint`` OperationalError is caught, logged and followed by ONE
   more open; any other error, or a second failure, fails the boot.
3. Assert the schema version now equals the image Hermes' ``SCHEMA_VERSION``,
   and, when this run migrated or retried, that ``PRAGMA integrity_check`` is
   ``ok``. A database that was already current and opened cleanly is not
   rescanned on every boot: on the largest seat that is a 630 MB read.
4. Assert ``hermes_bootstrap`` was never imported. The import path is
   ``hermes_state`` only: it imports a few ``pm`` path helpers through
   ``hermes_cli`` but calls no ``pm`` function and never runs
   ``activate_dependencies`` (profiled offline at v0.22.5).
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

SAVEPOINT_RACE = "no such savepoint"


def _log(msg: str) -> None:
    print(f"state.db open: {msg}", flush=True)


def schema_version(db: Path) -> int | None:
    """``schema_version.version`` read through a read-only connection, or None."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        if row is None:
            return None
        got = con.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return int(got[0]) if got else None
    finally:
        con.close()


def integrity(db: Path) -> str:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return "; ".join(str(r[0]) for r in con.execute("PRAGMA integrity_check").fetchall())
    finally:
        con.close()


def open_db(db: Path, session_db: Callable[..., Any]) -> bool:
    """Open and close *db* through Hermes; True when the savepoint race forced a retry."""
    try:
        session_db(db_path=db).close()
        return False
    except sqlite3.OperationalError as exc:
        if SAVEPOINT_RACE not in str(exc):
            raise
        _log(f"{db}: first open raised {exc!r} (Hermes v0.22.5 FTS-align savepoint race); opening once more")
    session_db(db_path=db).close()
    return True


def check(db: Path, session_db: Callable[..., Any], want: int) -> list[str]:
    """Open *db* once (twice on the savepoint race); return problems, empty is pass."""
    before = schema_version(db)
    retried = open_db(db, session_db)
    after = schema_version(db)
    problems = []
    if after != want:
        problems.append(f"{db}: schema {after!r} after open, image Hermes is {want}")
    migrated = before != after
    if migrated or retried:
        verdict = integrity(db)
        if verdict != "ok":
            problems.append(f"{db}: integrity_check {verdict[:300]!r}")
        else:
            _log(f"{db}: schema {before} -> {after}{' after a retry' if retried else ''}, integrity ok")
    else:
        _log(f"{db}: schema {after}, current, opened cleanly")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    from hermes_state import SessionDB  # the image Hermes; lazy so tests can stub it
    from hermes_state_common import SCHEMA_VERSION

    problems: list[str] = []
    for arg in argv[1:]:
        db = Path(arg)
        if not db.is_file():
            continue  # a fresh state.db is created at the current schema; no race there
        try:
            problems.extend(check(db, SessionDB, int(SCHEMA_VERSION)))
        except Exception as exc:  # noqa: BLE001 - any other failure fails the boot, named
            problems.append(f"{db}: {type(exc).__name__}: {exc}")
    if "hermes_bootstrap" in sys.modules:
        problems.append("hermes_bootstrap was imported: this open may have swapped dependency environments")
    for problem in problems:
        print(f"state.db open: {problem}", file=sys.stderr, flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
