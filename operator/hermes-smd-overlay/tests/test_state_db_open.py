"""bootstrap.state_db_open: the first v0.22.5 open of a v2026.9.14 state.db happens before the gateway.

ss-console#3564. The first v0.22.5 ``SessionDB`` open of an EMPTY v2026.9.14
state.db raises ``no such savepoint: fts_align_empty``; the second open
succeeds at schema 32. On a seat that first open was the job worker's, so the
worker thread died. ``tests/fixtures/hermes_state/v2026.9.14-empty-state.db``
is a real v2026.9.14 file: ``SessionDB(db_path=...)`` then ``close()`` with
Hermes 345cd2b057a452236de401d3534b8502a7465e8d on Python 3.13, nothing else.

The stubbed tests pin the control flow. The real-Hermes test reproduces the
race and proves the fix against the pinned Hermes (HERMES_AGENT_SRC; required
in CI, skipped locally without it).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from bootstrap import state_db_open

FIXTURE = Path(__file__).parent / "fixtures" / "hermes_state" / "v2026.9.14-empty-state.db"
OVERLAY = Path(__file__).resolve().parents[1]
_HERMES_SRC = os.environ.get("HERMES_AGENT_SRC", "")


def _db(tmp_path: Path, version: int = 30) -> Path:
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    con.execute("INSERT INTO schema_version VALUES (?)", (version,))
    con.commit()
    con.close()
    return db


class _Stub:
    """A SessionDB stand-in: raises the queued errors, then migrates to *to*."""

    def __init__(self, errors: list[BaseException], to: int = 32) -> None:
        self.errors, self.to, self.opens = errors, to, 0

    def __call__(self, db_path: Path) -> _Stub:
        self.opens += 1
        if self.errors:
            raise self.errors.pop(0)
        con = sqlite3.connect(db_path)
        con.execute("UPDATE schema_version SET version = ?", (self.to,))
        con.commit()
        con.close()
        return self

    def close(self) -> None:
        pass


def test_the_savepoint_race_is_retried_once_and_then_verified(tmp_path):
    db = _db(tmp_path)
    stub = _Stub([sqlite3.OperationalError("no such savepoint: fts_align_empty")])
    assert state_db_open.check(db, stub, 32) == []
    assert stub.opens == 2


def test_a_clean_open_is_one_open(tmp_path):
    stub = _Stub([])
    assert state_db_open.check(_db(tmp_path, 32), stub, 32) == []
    assert stub.opens == 1


def test_any_other_operational_error_fails_without_a_retry(tmp_path):
    stub = _Stub([sqlite3.OperationalError("database is locked")])
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        state_db_open.check(_db(tmp_path), stub, 32)
    assert stub.opens == 1


def test_a_second_savepoint_failure_fails(tmp_path):
    race = sqlite3.OperationalError("no such savepoint: fts_align_empty")
    stub = _Stub([race, sqlite3.OperationalError("no such savepoint: fts_align_empty")])
    with pytest.raises(sqlite3.OperationalError):
        state_db_open.check(_db(tmp_path), stub, 32)
    assert stub.opens == 2


def test_a_schema_short_of_the_image_fails(tmp_path):
    problems = state_db_open.check(_db(tmp_path), _Stub([], to=31), 32)
    assert problems and "schema 31" in problems[0]


def _real(tmp_path: Path, db: Path) -> subprocess.CompletedProcess:
    """Run main() in a fresh `python -I` process on the pinned Hermes, as bootstrap.sh does."""
    if not _HERMES_SRC:
        if os.environ.get("CI"):
            pytest.fail("CI must check out the pinned Hermes and set HERMES_AGENT_SRC")
        pytest.skip("HERMES_AGENT_SRC not set")
    code = (
        "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]];"
        "from bootstrap.state_db_open import main; raise SystemExit(main(['x', sys.argv[3]]))"
    )
    env = {"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(db.parent), "HOME": str(tmp_path)}
    return subprocess.run(  # noqa: S603 - the repo's own module against a tmp copy
        [sys.executable, "-I", "-c", code, _HERMES_SRC, str(OVERLAY), str(db)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _hermes_open(db: Path) -> str:
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from hermes_state import SessionDB\n"
        "try:\n    SessionDB(db_path=Path(sys.argv[2])).close(); print('ok')\n"
        "except Exception as e:\n    print(type(e).__name__, e)\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HERMES_HOME": str(db.parent),
        "HOME": str(db.parent),
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code, _HERMES_SRC, str(db)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    ).stdout.strip()


def test_real_v2026_9_14_empty_db_reproduces_the_race_and_the_fix_opens_it(tmp_path):
    raw = tmp_path / "raw" / "state.db"
    raw.parent.mkdir()
    shutil.copy(FIXTURE, raw)
    fixed = tmp_path / "fixed" / "state.db"
    fixed.parent.mkdir()
    shutil.copy(FIXTURE, fixed)
    assert state_db_open.schema_version(fixed) == 30

    done = _real(tmp_path, fixed)
    # FALSIFIER: the race is real on the pinned Hermes. If Hermes ever fixes it,
    # this assertion fails and the workaround's retry branch can be retired.
    assert _hermes_open(raw).startswith("OperationalError no such savepoint"), (
        "race no longer reproduces"
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert (
        "opening once more" in done.stdout and "30 -> 32 after a retry, integrity ok" in done.stdout
    )
    assert state_db_open.schema_version(fixed) == 32
    # The gateway's own open is now clean, which is what the job worker does.
    assert _hermes_open(fixed) == "ok"
    # A second boot does one open and no rescan.
    again = _real(tmp_path, fixed)
    assert again.returncode == 0 and "current, opened cleanly" in again.stdout
    # No package-manager state was created under the home.
    assert not (fixed.parent / "installs").exists() and not (fixed.parent / "tools").exists()
