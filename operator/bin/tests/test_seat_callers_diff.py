"""The caller table is compared without line numbers
(``bin/lib/seat_callers/diff.py``).

A site is its file, its enclosing function and its call text. The table keeps
each call's line as a hint for a reader, but a peer's merge that moves a site
down its file must not fail every PR in the queue (it did, 2026-11-13: smoke.py
moved 14 lines and #3998 left the merge queue twice). A changed call still fails.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from bin.lib.seat_callers.diff import differences

TABLE = Path(__file__).resolve().parents[2] / "contracts" / "seat-config-callers.json"
_LINE = re.compile(r"^(\S+?):(\d+) ")


def _table() -> dict[str, Any]:
    return json.loads(TABLE.read_text(encoding="utf-8"))


def _moved(doc: dict[str, Any], file: str, by: int) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    for body in out["concepts"].values():
        for row in body["rows"]:
            if row["file"] == file:
                row["calls"] = [_LINE.sub(lambda m: f"{m[1]}:{int(m[2]) + by} ", c) for c in row["calls"]]
        body["rows"].reverse()  # a move can reorder rows; order is not compared either
    return out


def test_a_site_moved_down_its_file_is_the_same_site():
    """A peer adds lines above a reader: every line in that file moves, nothing else does."""
    doc = _table()
    moved = _moved(doc, "operator/bin/lib/fleet/smoke.py", 14)
    assert moved != doc, "the move must actually change the stored lines"
    assert differences(doc, moved) == []
    assert differences(doc, _moved(doc, "operator/templates/msgraph-read-app-cannot-read-staff-probe.py", 157)) == []


def test_a_changed_call_text_still_fails():
    """FALSIFIER: the same function now parses a different expression."""
    doc = _table()
    changed = copy.deepcopy(doc)
    row = next(
        r for r in changed["concepts"]["customer-yaml"]["rows"] if r["file"] == "operator/bin/lib/fleet/smoke.py"
    )
    row["calls"] = [c.replace("(", "_planted(", 1) for c in row["calls"]]
    found = differences(doc, changed)
    assert any("smoke.py" in f and " calls:" in f for f in found), found


def test_a_changed_behaviour_or_a_gone_site_still_fails():
    doc = _table()
    rows = doc["concepts"]["customer-yaml"]["rows"]
    gone = copy.deepcopy(doc)
    gone["concepts"]["customer-yaml"]["rows"] = rows[1:]
    assert any(f.startswith("gone: customer-yaml") for f in differences(doc, gone))
    changed = copy.deepcopy(doc)
    first = changed["concepts"]["customer-yaml"]["rows"][0]
    key = next(iter(first["behaviour"]))
    first["behaviour"][key] = "return planted"
    assert any(".behaviour." in f or " behaviour." in f for f in differences(doc, changed))
