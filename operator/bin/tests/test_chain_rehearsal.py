"""The audit-integrity alarm drill (ss#2500, ``--rehearse-mismatch``).

The DETECTOR is covered by ``test_audit_chain_watch.py``. What was never
exercised before this is the path from a finding to the console's alert sink,
and the three things a drill must not do: overwrite a real alert, write to the
object-locked archive, or leave its own fake alarm standing.

Everything here drives the real ``rehearse_mismatch`` and the real
``evaluate_export``; only the seam pull and the sink are stubs. Nothing touches a
seat, D1 or R2 — a drill whose own tests needed the live system would be skipped
in CI, which is how a control ships with detection nobody has ever seen fire.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_OPERATOR = _HERE.parents[2]
sys.path.insert(0, str(_OPERATOR))
sys.path.insert(0, str(_OPERATOR / "workspace_broker"))

from bin.lib import chain_rehearsal  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from bin.lib.console_d1 import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ALERT_DRIVER_PREFIX,
    REHEARSAL_DRIVER_PREFIX,
    ConsoleD1,
)
from chain import CHAIN_COLUMNS, GENESIS, compute_row_hash  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

# The script has a dash in its name, so it is loaded by path. The drill borrows
# its real `evaluate_export`: a stub there would test the stub.
_spec = importlib.util.spec_from_file_location(
    "audit_chain_watch", _OPERATOR / "bin" / "audit-chain-watch.py"
)
watch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["audit_chain_watch"] = watch
_spec.loader.exec_module(watch)


def _chain(n: int) -> list[dict]:
    rows: list[dict] = []
    prev = GENESIS
    for i in range(n):
        row = {
            "id": f"01J0000000000000000000{i:04d}",
            "ts": f"2026-08-20T00:00:{i % 60:02d}Z",
            "action_type": "TOOL_CALL_COMPLETED",
            "actor": "agent",
            "actor_role": "agent",
            "skill_name": "unit-test",
            "matter_ref": None,
            "input_digest": None,
            "output_digest": None,
            "diff_digest": None,
            "trust_ceiling": "draft",
            "metadata": None,
        }
        row["prev_hash"] = prev
        row["row_hash"] = compute_row_hash(prev, [row[c] for c in CHAIN_COLUMNS])
        prev = row["row_hash"]
        rows.append(row)
    return rows


def _pin(rows: list[dict]) -> dict:
    return {
        "audit_head": rows[-1]["row_hash"],
        "audit_rows": len(rows),
        "first_seen_heartbeat_ts": "2026-08-20T00:00:00Z",
        "last_seen_heartbeat_ts": "2026-08-20T00:05:00Z",
    }


class _FakeConsole:
    """Records what the drill would do to D1 without doing any of it."""

    def __init__(self, *, pin=None, fail_clear=False):
        self._pin = pin
        self._fail_clear = fail_clear
        self.writes: list[dict] = []
        self.cleared: list[str] = []

    def newest_pin(self, slug):
        return self._pin

    def clear_rehearsal_alerts(self, *, slug):
        if self._fail_clear:
            raise RuntimeError("delete failed")
        self.cleared.append(slug)


class _Drill:
    """One drill run, with the three collaborators the split made explicit."""

    def __init__(self, rows, *, console=None, evaluate=None, seam_missing=False):
        self.console = console or _FakeConsole(pin=_pin(rows))
        self._rows = rows
        self._seam_missing = seam_missing
        self._evaluate = evaluate or watch.evaluate_export
        self.archived = False

    def _seam(self, _slug):
        if self._seam_missing:
            return None
        rows = self._rows

        class _Client:
            def read_all(self, _table):
                return rows

        return _Client()

    def _emit(self, console, outcome, driver_prefix):
        console.writes.append(
            {"summary": outcome.headline, "details": outcome.details,
             "driver_prefix": driver_prefix}
        )
        return None

    def run(self, slug="smd-staging"):
        return chain_rehearsal.rehearse_mismatch(
            slug,
            self.console,
            seam_client_from_env=self._seam,
            evaluate_export=self._evaluate,
            emit_alert=self._emit,
        )


def test_the_drill_fires_the_alarm_and_then_clears_it():
    d = _Drill(_chain(20))
    code, lines = d.run()
    assert code == chain_rehearsal.EXIT_REHEARSAL_OK
    assert len(d.console.writes) == 1, "the alarm did not fire"
    assert d.console.cleared == ["smd-staging"], "the drill left its own alarm standing"
    assert any("the alarm fired" in ln for ln in lines)


def test_the_drill_writes_under_the_rehearsal_driver_not_the_real_one():
    """The load-bearing one. The alert PK is (entity_id, alert_date, driver) and
    the insert UPSERTS, so a drill sharing the real driver would erase a genuine
    finding written for the same seat earlier the same day."""
    d = _Drill(_chain(12))
    d.run()
    prefix = d.console.writes[0]["driver_prefix"]
    assert prefix == REHEARSAL_DRIVER_PREFIX
    assert prefix != ALERT_DRIVER_PREFIX


def test_the_drill_row_says_it_is_synthetic_in_the_summary_and_the_details():
    d = _Drill(_chain(12))
    d.run()
    written = d.console.writes[0]
    assert written["summary"].startswith("[REHEARSAL")
    assert written["details"]["rehearsal"] is True
    assert "SYNTHETIC" in written["details"]["rehearsal_note"]


def test_the_drill_never_archives():
    """The audit/ prefix is object-locked for seven years; a key a drill burned
    could never be rewritten. The module has no archive call at all."""
    source = Path(chain_rehearsal.__file__).read_text()
    assert "archive_export" not in source
    assert _Drill(_chain(12)).run()[0] == chain_rehearsal.EXIT_REHEARSAL_OK


def test_the_drill_leaves_the_real_pin_alone():
    """The synthetic head lives in memory for one call. Nothing writes
    audit_head_history, so tomorrow's real run compares against the real pin."""
    rows = _chain(12)
    real = _pin(rows)
    d = _Drill(rows, console=_FakeConsole(pin=real))
    d.run()
    assert real["audit_head"] == rows[-1]["row_hash"]
    assert d.console.writes[0]["details"]["pinned_head"] == chain_rehearsal.REHEARSAL_HEAD


def test_a_drill_that_produces_no_finding_fails():
    """The drill's own falsifier. If a head that is not in the export stops being
    a finding, the detector is broken and every clean run since is unproven — so
    the drill goes red rather than quietly succeeding at nothing."""
    clean = watch.SeatOutcome("smd-staging", watch.CLEAN, "smd-staging: fine", {"head": "x"})
    d = _Drill(_chain(12), evaluate=lambda *a, **k: clean)
    code, lines = d.run()
    assert code == chain_rehearsal.EXIT_REHEARSAL_FAILED
    assert d.console.writes == [], "a non-finding must not write an alert row"
    assert any("did not produce a finding" in ln for ln in lines)


def test_the_rehearsal_head_is_shaped_like_a_real_one():
    """It has to survive the malformed-pin guard to reach the absent-head branch.
    A 'not-a-hash' sentinel would be reported as a broken INSTRUMENT (a hold),
    which is not the alarm this drill is proving."""
    rows = _chain(12)
    assert len(chain_rehearsal.REHEARSAL_HEAD) == 64
    assert all(c in "0123456789abcdef" for c in chain_rehearsal.REHEARSAL_HEAD)
    assert chain_rehearsal.REHEARSAL_HEAD not in {r["row_hash"] for r in rows}
    d = _Drill(rows)
    d.run()
    assert d.console.writes[0]["details"]["pin_verdict"] != "pin_malformed"


def test_a_failed_clear_is_reported_as_a_failure_with_the_row_to_delete():
    """If the row cannot be cleared, the run must go red and say exactly what is
    still on the dashboard. Silence leaves a fake integrity alarm up."""
    rows = _chain(12)
    d = _Drill(rows, console=_FakeConsole(pin=_pin(rows), fail_clear=True))
    code, lines = d.run()
    assert code == chain_rehearsal.EXIT_REHEARSAL_FAILED
    assert any(REHEARSAL_DRIVER_PREFIX in ln for ln in lines)


def test_a_seam_that_is_not_configured_fails_rather_than_passing():
    d = _Drill(_chain(12), seam_missing=True)
    code, _lines = d.run()
    assert code == chain_rehearsal.EXIT_REHEARSAL_FAILED
    assert d.console.writes == []


def test_the_drill_exit_codes_cannot_be_confused_with_a_real_verdict():
    """3 and 4 sit outside the clean/finding/hold vocabulary on purpose, so a
    drill can never open the 'a seat's audit record does not hold up' issue."""
    real = {watch.EXIT_CLEAN, watch.EXIT_FINDING, watch.EXIT_HOLD}
    assert chain_rehearsal.EXIT_REHEARSAL_OK not in real
    assert chain_rehearsal.EXIT_REHEARSAL_FAILED not in real


def test_clear_rehearsal_alerts_can_only_delete_rehearsal_rows():
    """The DELETE is pinned to the rehearsal driver by equality, so no slug and
    no pattern character can widen it onto a real finding."""
    seen: list[list[str]] = []

    def runner(cmd):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, json.dumps([{"results": []}]), "")

    ConsoleD1(db="test-db", runner=runner).clear_rehearsal_alerts(slug="seat%' OR '1'='1")
    sql = seen[-1][-1]
    assert "DELETE FROM cost_anomaly_alerts WHERE driver = " in sql
    assert "LIKE" not in sql
    assert "OR '1'='1" not in sql
    driver = bytes.fromhex(sql.split("CAST(x'")[1].split("'")[0]).decode("utf-8")
    assert driver.startswith(REHEARSAL_DRIVER_PREFIX)
