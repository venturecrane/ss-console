"""``bin/ledger-differential.py``: two versions of the broker job ledgers read
one audit.db and must agree, and nothing from the file is printed.

The audit.db here is built through the REAL broker verbs and ledger classes
(the ledger golden kit's seat), every lane driven through its endings, plus
the aged-schema fixtures (``workspace_broker/tests/goldens/ledgers/*/aged.json``).
The same tree on both sides passes; a tree with one read planted different
fails on that table; the input file is never written.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

OPERATOR = Path(__file__).resolve().parents[2]
KIT = OPERATOR / "workspace_broker" / "tests"
for p in (str(OPERATOR), str(KIT)):
    if p not in sys.path:
        sys.path.append(p)

_spec = importlib.util.spec_from_file_location("ledger_differential", OPERATOR / "bin" / "ledger-differential.py")
assert _spec is not None and _spec.loader is not None
diff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diff)

#: What a line of output may be: a table or class name and marks, or the result.
LINE = re.compile(
    r"^(table [a-z_]+: rows=\d+(/\d+)?( (raw|read|project)=(PASS|FAIL)){3}|class [A-Za-z]+: schema\+reads=(PASS|FAIL)|RESULT: (PASS|FAIL))$"
)


def build_db(root: Path, mp: Any) -> Path:
    """An audit.db written by the real verbs: every lane's jobs taken through
    several endings (spend, lines, notices, resumes), reply marks, a note."""
    from ledger_golden_capture import _note_job, ledger, request
    from ledger_golden_kit import LANES, Seat

    seat = Seat(root, mp)
    paths = (["running", "delivered"], ["running", "held"], ["running", "failed", "running", "delivered"], ["failed"])
    for lane in LANES.values():
        for n, path in enumerate(paths, start=1):
            try:
                jid = lane.submit(seat.broker, n)
            except Exception:  # noqa: BLE001 - a lane that takes one job at a time refuses the next; that is data too
                continue
            for step in path:
                seat.call(request(lane, jid, step))
            led = ledger(seat, lane)
            if hasattr(led, "mark_replied"):
                led.mark_replied(jid, f"{n}:done")
    _note_job(seat)
    return seat.db


def aged_dbs(root: Path) -> list[Path]:
    """Each lane's aged schemas, the k-th step of every lane in the k-th file
    (the lanes own disjoint tables, as on a seat's one file)."""
    steps: dict[int, list[list[str]]] = {}
    for doc in sorted((KIT / "goldens" / "ledgers").glob("*/aged.json")):
        for k, step in enumerate(json.loads(doc.read_text(encoding="utf-8"))["steps"]):
            steps.setdefault(k, []).append(step["schema"])
    out = []
    for k, schemas in sorted(steps.items()):
        db = root / f"aged-{k}.db"
        conn = sqlite3.connect(db)
        for schema in schemas:
            rows = [s.split(": ", 1) for s in schema]
            for _kind, sql in sorted(rows, key=lambda r: (not r[0].startswith("table"), r[0])):
                conn.execute(sql)
        conn.commit()
        conn.close()
        out.append(db)
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(capsys: Any, *argv: str) -> tuple[int, list[str]]:
    code = diff.main(list(argv))
    return code, capsys.readouterr().out.splitlines()


@pytest.fixture(scope="module")
def seat_db(tmp_path_factory: Any) -> Path:
    mp = pytest.MonkeyPatch()
    try:
        return build_db(tmp_path_factory.mktemp("seat"), mp)
    finally:
        mp.undo()


def test_the_same_classes_agree_on_every_row_and_print_no_row(seat_db, capsys):
    before = _sha(seat_db)
    code, out = _run(capsys, "--db", str(seat_db), "--old-tree", str(OPERATOR))
    assert code == 0, out
    assert out[-1] == "RESULT: PASS"
    assert all(LINE.match(line) for line in out), out
    tables = {line.split(":")[0].split()[1]: line for line in out if line.startswith("table ")}
    # Every lane's jobs were read, with rows in them.
    for t in ("medchron_jobs", "demand_jobs", "drafting_jobs", "library_setup_jobs", "litigation_jobs",
              "casefile_jobs", "negotiation_jobs", "records_jobs", "note_jobs"):  # fmt: skip
        assert t in tables and "rows=0 " not in tables[t], (t, tables.get(t))
    assert _sha(seat_db) == before, "the input copy was written"


def test_a_planted_read_difference_fails_on_its_table(seat_db, tmp_path, capsys):
    """MUTATION: one version's demand projection drops a key; that table's
    project read FAILS, every other table still passes, and the exit is 1."""
    tree = tmp_path / "planted" / "operator"
    shutil.copytree(
        OPERATOR / "workspace_broker", tree / "workspace_broker", ignore=shutil.ignore_patterns("tests", "__pycache__")
    )
    led = tree / "workspace_broker" / "demand_ledger.py"
    text = led.read_text(encoding="utf-8")
    assert '    "reply_sent_at",\n)' in text
    led.write_text(text.replace('    "reply_sent_at",\n)', ")", 1), encoding="utf-8")
    code, out = _run(capsys, "--db", str(seat_db), "--old-tree", str(OPERATOR), "--new-tree", str(tree))
    assert code == 1
    assert out[-1] == "RESULT: FAIL"
    demand = next(line for line in out if line.startswith("table demand_jobs:"))
    assert "project=FAIL" in demand and "raw=PASS" in demand, demand
    assert all(
        "FAIL" not in line for line in out if line.startswith("table ") and not line.startswith("table demand_jobs:")
    )


def test_every_aged_schema_reads_the_same(tmp_path, capsys):
    for db in aged_dbs(tmp_path):
        code, out = _run(capsys, "--db", str(db), "--old-tree", str(OPERATOR))
        assert code == 0, (db.name, out)


def test_it_refuses_without_exactly_one_old_side(seat_db, capsys):
    assert _run(capsys, "--db", str(seat_db))[0] == 2
    assert _run(capsys, "--db", str(seat_db), "--base", "HEAD", "--old-tree", str(OPERATOR))[0] == 2
