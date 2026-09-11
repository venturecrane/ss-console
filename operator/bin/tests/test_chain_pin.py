"""The falsifier for ss#2500: tail truncation must stop passing.

READ THE FIRST TEST FIRST. ``test_truncated_tail_passes_without_a_pin`` asserts
the OLD behaviour on purpose -- an export with its last rows deleted still walks
as a valid chain and ``verify_chain`` still says ok. That test is the negative
control for every other test in this file. Without it, a bug that made the
truncated fixture fail for some unrelated reason would make the pin tests pass
while proving nothing about the pin (Law 12: build the falsifier into the
instrument, and test the probe on a known case).

The fixture is a REAL chain built through ``compute_row_hash`` from
``operator/workspace_broker/chain.py`` -- the same function the broker writes
with -- not hand-written hashes. A hand-written fixture would be pinned to this
test's idea of the canonicalization rather than to the shipped one, and would
keep passing after a canonicalization change that broke every real ledger.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_OPERATOR = _HERE.parents[2]
sys.path.insert(0, str(_OPERATOR))
sys.path.insert(0, str(_OPERATOR / "workspace_broker"))

from bin.lib.chain_pin import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    PIN_ABSENT,
    PIN_DESCENDS,
    PIN_MALFORMED,
    PIN_NOT_SUPPLIED,
    PIN_UNCHANGED,
    check_pinned_head,
)
from chain import CHAIN_COLUMNS, GENESIS, compute_row_hash, verify_chain  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

_VERIFIER = _OPERATOR / "bin" / "verify-audit-chain.py"


def _build_chain(n: int) -> list[dict]:
    """A real n-row chain, linked exactly as the broker links one."""
    rows: list[dict] = []
    prev = GENESIS
    for i in range(n):
        row = {
            "id": f"01J0000000000000000000{i:04d}",
            "ts": f"2026-08-20T00:{i // 60:02d}:{i % 60:02d}Z",
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


@pytest.fixture()
def full_chain() -> list[dict]:
    return _build_chain(40)


# ---------------------------------------------------------------------------
# The negative control: what the chain walk alone cannot see
# ---------------------------------------------------------------------------


def test_truncated_tail_passes_without_a_pin(full_chain):
    """The defect, reproduced. This is what the live verifier did on 2026-08-20.

    Both shapes the live run exercised: the last 1 row removed, and a 50-row
    block removed. Both leave a valid chain, and the walk cannot tell.
    """
    for keep in (len(full_chain) - 1, len(full_chain) - 20):
        truncated = full_chain[:keep]
        report = verify_chain(truncated)
        assert report["ok"] is True, (
            "the chain walk alone should still pass; if it does not, this file's other tests prove nothing"
        )
        assert report["breaks"] == []
        # And the head it reports is simply the new last row -- nothing about
        # the report says rows are missing.
        assert report["head"] == truncated[-1]["row_hash"]


def test_rehash_after_mutation_passes_without_a_pin(full_chain):
    """The fourth live tamper: mutate a row, re-hash everything after it.

    The result is internally perfect. Only an external head disagrees.
    """
    rows = [dict(r) for r in full_chain]
    rows[10]["skill_name"] = "tampered"
    prev = rows[9]["row_hash"]
    for row in rows[10:]:
        row["prev_hash"] = prev
        row["row_hash"] = compute_row_hash(prev, [row[c] for c in CHAIN_COLUMNS])
        prev = row["row_hash"]
    assert verify_chain(rows)["ok"] is True
    # ...and the pin catches it, because the post-mutation re-hash necessarily
    # produced a different tip than the one that was pinned.
    pin = check_pinned_head(rows, pinned_head=full_chain[-1]["row_hash"], current_head=verify_chain(rows)["head"])
    assert pin["ok"] is False
    assert pin["verdict"] == PIN_ABSENT


# ---------------------------------------------------------------------------
# check_pinned_head
# ---------------------------------------------------------------------------


def test_truncated_tail_with_the_pre_truncation_head_is_a_break(full_chain):
    """THE falsifier named in ss#2500. Fails before chain_pin.py exists."""
    pinned = full_chain[-1]["row_hash"]
    truncated = full_chain[:-1]
    pin = check_pinned_head(truncated, pinned_head=pinned, current_head=verify_chain(truncated)["head"])
    assert pin["ok"] is False
    assert pin["verdict"] == PIN_ABSENT
    assert "truncated" in pin["reason"]


def test_a_descending_head_passes(full_chain):
    """The healthy shape: the ledger grew past the pin."""
    pinned = full_chain[20]["row_hash"]
    pin = check_pinned_head(full_chain, pinned_head=pinned, current_head=verify_chain(full_chain)["head"])
    assert pin["ok"] is True
    assert pin["verdict"] == PIN_DESCENDS


def test_an_unchanged_head_passes(full_chain):
    """A quiet seat. No rows written since the pin is not a finding."""
    pin = check_pinned_head(
        full_chain,
        pinned_head=full_chain[-1]["row_hash"],
        current_head=verify_chain(full_chain)["head"],
    )
    assert pin["ok"] is True
    assert pin["verdict"] == PIN_UNCHANGED


def test_a_regressed_head_fails(full_chain):
    """Head regression: today's export ends BEFORE a head already pinned.

    Distinct from truncation in cause -- a restored snapshot, a volume rolled
    back, a seat rebuilt on an older copy -- and identical in what it means for
    the record: rows that were pinned are not there.
    """
    later_pin = full_chain[-1]["row_hash"]
    older_export = full_chain[:15]
    pin = check_pinned_head(older_export, pinned_head=later_pin, current_head=verify_chain(older_export)["head"])
    assert pin["ok"] is False
    assert pin["verdict"] == PIN_ABSENT


def test_no_pin_is_reported_not_assumed(full_chain):
    pin = check_pinned_head(full_chain, pinned_head=None, current_head=verify_chain(full_chain)["head"])
    assert pin["ok"] is True
    assert pin["verdict"] == PIN_NOT_SUPPLIED
    assert "not detectable" in pin["reason"]


def test_a_malformed_pin_is_an_instrument_failure_not_a_finding(full_chain):
    """A junk pin can never appear in any export.

    Reporting it as a break would accuse a healthy ledger every day until a
    human read the row. It gets its own verdict so the watcher can tell "the
    record was tampered with" apart from "we stored garbage".
    """
    pin = check_pinned_head(full_chain, pinned_head="not-a-hash", current_head=verify_chain(full_chain)["head"])
    assert pin["ok"] is False
    assert pin["verdict"] == PIN_MALFORMED
    assert "instrument" in pin["reason"]


# ---------------------------------------------------------------------------
# The CLI, end to end
# ---------------------------------------------------------------------------


def _run_verifier(tmp_path: Path, rows: list[dict], *args: str) -> subprocess.CompletedProcess:
    payload = tmp_path / "export.json"
    payload.write_text(json.dumps({"entries": rows}))
    return subprocess.run(
        [sys.executable, str(_VERIFIER), "--json", str(payload), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_truncated_export_passes_without_the_flag_and_fails_with_it(tmp_path, full_chain):
    """One test, both sides, so the flag is provably what changed the verdict."""
    pinned = full_chain[-1]["row_hash"]
    truncated = full_chain[:-1]

    without = _run_verifier(tmp_path, truncated)
    assert without.returncode == 0, without.stdout + without.stderr
    assert "INTACT" in without.stdout
    assert "TAIL UNCHECKED" in without.stdout

    with_pin = _run_verifier(tmp_path, truncated, "--pinned-head", pinned)
    assert with_pin.returncode == 1, with_pin.stdout + with_pin.stderr
    assert "BROKEN" in with_pin.stdout
    assert f"BREAK pin={pinned}" in with_pin.stdout


def test_cli_intact_export_with_a_descending_pin_exits_zero(tmp_path, full_chain):
    result = _run_verifier(tmp_path, full_chain, "--pinned-head", full_chain[20]["row_hash"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INTACT" in result.stdout
    assert PIN_DESCENDS in result.stdout
