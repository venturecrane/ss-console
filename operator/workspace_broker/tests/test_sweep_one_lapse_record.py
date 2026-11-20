"""Two sweeps at once write ONE RULE_LAPSED record for a silently lapsed rule.

The sweep used to select the expired rows, then update them in a separate
transaction, so a second sweep running between the two selected the same row
and both wrote its record (review 2026-11-19). The select now runs inside the
UPDATE's write transaction. This test interleaves a second sweep exactly at the
first sweep's select: before the fix it records twice. Addresses are invented.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.pending_rule_store import PendingRuleStore


class _Ledger:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.lock = threading.Lock()

    def append(self, row: dict) -> None:
        with self.lock:
            self.rows.append(row)


class _Conn:
    """A connection whose first SELECT starts a second sweep and gives it time."""

    def __init__(self, conn, on_select) -> None:
        self._conn = conn
        self._on_select = on_select

    def execute(self, sql, *args):
        out = self._conn.execute(sql, *args)
        if sql.lstrip().upper().startswith("SELECT") and self._on_select:
            hook, self._on_select = self._on_select, None
            hook()
        return out

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_two_sweeps_at_once_write_one_lapse_record(tmp_path) -> None:
    db = tmp_path / "audit.db"
    first = PendingRuleStore(db)
    row = first.create(
        scope="person",
        subject={"person": "pat@firm.example"},
        text="Keep my memos short.",
        instructed_by="pat@firm.example",
        for_admin=False,
    )
    ledger = _Ledger()
    second = PendingRuleStore(db)
    other: list[threading.Thread] = []

    def start_second() -> None:
        thread = threading.Thread(target=lambda: second.sweep(now=row["expires_at"] + 1, ledger=ledger))
        thread.start()
        other.append(thread)
        time.sleep(0.5)  # long enough for an unblocked sweep to finish

    plain = first._connect
    first._connect = lambda: _Conn(plain(), start_second)  # type: ignore[method-assign]
    first.sweep(now=row["expires_at"] + 1, ledger=ledger)
    for thread in other:
        thread.join(10)
    assert len(ledger.rows) == 1
