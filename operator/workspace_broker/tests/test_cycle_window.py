"""The allowance window, checked three ways (ss#2618).

1. THE READER, against the shared fixture. This is where the three surfaces
   actually diverge: `anchor_day: "15"` (quoted in YAML) passes the open scalar
   validator, projects to D1 as a string, and then reads as 15 on a surface that
   coerces and as nothing on one that type-checks. A window table alone cannot
   see that, because it takes the anchor already parsed.

2. THE ARITHMETIC, against the same fixture -- whose expectations are
   HAND-AUTHORED from Stripe's anchor rule, not generated from this module. A
   table generated from the code it checks proves only that the code equals
   itself.

3. THE TILING PROPERTY, exhaustively. A fixture can be wrong in the same way on
   every surface; a property cannot. For every anchor and every day across six
   years: each instant falls in exactly one window, and each window's end is the
   next window's start. Note what this DOESN'T catch -- a chained implementation
   (Feb 28 -> Mar 28 instead of returning to the 31st) tiles perfectly. That is
   what the return-to-anchor fixture vectors are for. Neither check subsumes the
   other.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.cycle_window import (  # noqa: E402
    AnchorInvalid,
    cycle_window,
    resolve_anchor,
    resolve_effective_from,
)

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "contracts" / "cycle_window_fixture.json"

# The cross-repo contract. The laptop pipeline (private engagements repo) vendors
# a byte-identical copy and pins this same digest; the console reads the file
# directly. If you change the fixture you change it in BOTH repos in the SAME
# change and update both pins -- that is what makes it a contract rather than a
# table two implementations happen to share.
# Precedent: PINNED_CONTENT_SHA256 in tests/customer-yaml-parity-contract.test.ts.
PINNED_CONTENT_SHA256 = "f7c10e5a2d0481b7720f8efbbdd1b17b4a9baa61898631ed2718a1d52f8ff2dd"

def _materialise(value):
    """`{"__float__": N}` means the native float N. JSON has one number type and
    YAML has two, and that distinction is exactly what one reader accepts and
    another refuses -- so it is carried as a tag rather than silently collapsed.
    Without it this vector tested the integer case and proved nothing."""
    if isinstance(value, dict) and set(value) == {"__float__"}:
        return float(value["__float__"])
    return value


def _settings(case: dict) -> dict:
    return {k: _materialise(v) for k, v in (case.get("settings") or {}).items()}


FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_the_fixture_is_the_pinned_one() -> None:
    actual = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert actual == PINNED_CONTENT_SHA256, (
        "cycle_window_fixture.json changed. Copy it to the engagements pipeline "
        "and update BOTH pins in the same change, or the three surfaces silently "
        f"stop agreeing. New digest: {actual}"
    )


@pytest.mark.parametrize("case", FIXTURE["reader"], ids=lambda c: c["why"][:48])
def test_the_reader_resolves_or_refuses(case: dict) -> None:
    asym = case.get("asymmetric")
    if asym:
        # A case the two languages genuinely cannot answer alike. Declared in the
        # fixture rather than omitted, so the table never reads as agreement that
        # does not exist. Python is authoritative here: the seat refuses, so
        # nothing runs, which is the fail-safe direction.
        if asym["python"] == "invalid":
            with pytest.raises(AnchorInvalid):
                resolve_anchor(_settings(case))
        else:
            resolve_anchor(_settings(case))
        return
    if case.get("invalid"):
        with pytest.raises(AnchorInvalid) as exc:
            resolve_anchor(_settings(case))
        # The refusal names the key the firm has to fix; a refusal that does not
        # is a dead end for whoever reads it.
        assert "chronology_package_cycle_anchor_day" in str(exc.value)
    else:
        assert resolve_anchor(_settings(case)) == case["anchor"]


@pytest.mark.parametrize("case", FIXTURE["effective_from"], ids=lambda c: c["why"][:48])
def test_the_effective_from_reader_resolves_or_refuses(case: dict) -> None:
    if case.get("invalid"):
        with pytest.raises(AnchorInvalid) as exc:
            resolve_effective_from(_settings(case))
        assert "chronology_package_cycle_effective_from" in str(exc.value)
    else:
        assert resolve_effective_from(_settings(case)) == case["value"]


def test_absent_is_not_invalid() -> None:
    """The distinction the whole design rests on. Absent means "no cycle
    authored, meter by calendar month" -- a known, safe state every seat was in
    before 2026-09-11. Invalid means a human authored a control the seat cannot
    honour, and quietly metering on a different window than the firm believes is
    the harm. Collapsing the two (which cloning the allowance reader would have
    done) lets `anchor_day: 32` silently revert a firm to calendar months."""
    assert resolve_anchor({}) is None
    assert resolve_anchor(None) is None
    with pytest.raises(AnchorInvalid):
        resolve_anchor({"chronology_package_cycle_anchor_day": 32})


@pytest.mark.parametrize("case", FIXTURE["window"], ids=lambda c: c["why"][:48])
def test_the_window_matches_the_hand_authored_fixture(case: dict) -> None:
    w = cycle_window(case["now"], case.get("anchor"), case.get("effective_from"))
    assert (w.start, w.end, w.label) == (case["start"], case["end"], case["label"])


def test_bounds_are_full_timestamps_never_bare_dates() -> None:
    """`created_at` is `YYYY-MM-DDTHH:MM:SS.mmmZ`. Bounds in the same full form
    mean no comparison ever depends on a shorter string sorting before a longer
    one."""
    w = cycle_window("2026-09-20T12:00:00.000Z", 15)
    assert w.start.endswith("T00:00:00.000Z") and w.end.endswith("T00:00:00.000Z")
    assert len(w.start) == len("2026-09-15T00:00:00.000Z")


def test_windows_tile_with_no_gap_or_overlap_for_every_anchor() -> None:
    """Exhaustive: 32 anchors (31 days plus unanchored) x ~2,190 days."""
    for anchor in [*range(1, 32), None]:
        day, stop = date(2024, 1, 1), date(2029, 12, 31)
        while day <= stop:
            now = day.isoformat() + "T12:00:00.000Z"
            w = cycle_window(now, anchor)
            assert w.start <= now < w.end, f"anchor={anchor} now={now} window={w}"
            nxt = cycle_window(w.end, anchor)
            assert nxt.start == w.end, f"gap/overlap at anchor={anchor} boundary={w.end}"
            day += timedelta(days=1)


def test_effective_from_never_reaches_back_past_itself() -> None:
    """Authoring an anchor must not re-partition rows already in the ledger: the
    debit rule keys on `created_at`, so without this the firm silently gets a
    second allowance inside one period, or is refused for pages it already
    consumed in a period it already paid for."""
    w = cycle_window("2026-09-20T12:00:00.000Z", 15, "2026-09-18")
    assert w.start == "2026-09-18T00:00:00.000Z"
    assert w.end == "2026-10-15T00:00:00.000Z"  # the END is untouched; only the floor moves
    # Once the first cycle has passed it is inert.
    later = cycle_window("2026-11-20T12:00:00.000Z", 15, "2026-09-18")
    assert later.start == "2026-11-15T00:00:00.000Z"


def test_the_label_is_prose_and_is_never_a_parseable_date() -> None:
    """`month` keeps its wire key (the overlay's pinned tool whitelists it by
    name) but carries a human phrase, because the agent relays refusal sentences
    verbatim. A range there reads "remain in 2026-09-15..2026-10-15's allowance"."""
    assert cycle_window("2026-09-20T12:00:00.000Z", 15).label == "the cycle ending Oct 14"
    # Unanchored keeps the old YYYY-MM shape, which is what `MONTH_RE` and every
    # existing ledger row already expect.
    assert cycle_window("2026-09-20T12:00:00.000Z").label == "2026-09"
