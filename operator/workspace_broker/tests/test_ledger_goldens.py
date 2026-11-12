"""Every broker job ledger compared with its goldens (``ledger_golden_kit.py``):
the schema on a fresh file and on each aged schema, the full state x
transition matrix, reply-once, the month at Pacific month edges, and every
verb's reply.

A change to a ledger must leave each golden equal, except the dotted paths
``goldens/ledgers/allowed-changes.json`` names (key ``<lane>/<golden>``), a
reviewed change made in the PR that changes the behaviour.

``JOBS_WRITE_LEDGER_GOLDENS=1`` rewrites them (read the diff).
``JOBS_GOLDENS_OUT=<dir>`` writes them under ``<dir>/ledgers`` instead of
comparing (the integrity job runs this file on the merge-base tree that way).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# The kit beside this file (the broker suite runs in importlib mode).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger_golden_capture import ALL, CAPTURES
from ledger_golden_kit import GOLDENS, diffs, load_allowed, unexplained

CASES = [
    (lane, golden) for lane in ALL for golden in CAPTURES if not (lane == "note" and golden in ("transitions", "month"))
]


def test_every_lane_has_every_golden():
    """MUTATION: drop a lane's golden file and this fails."""
    for lane, golden in CASES:
        assert (GOLDENS / lane / f"{golden}.json").is_file(), (lane, golden)
        assert (GOLDENS / lane / "aged.json").is_file(), lane


@pytest.mark.parametrize("lane,golden", CASES, ids=[f"{a}/{b}" for a, b in CASES])
def test_a_ledger_matches_its_golden(tmp_path, monkeypatch, lane, golden):
    """MUTATION: change a ledger's schema, a transition, its reply-once rule,
    its month window or a verb's reply, and its golden fails."""
    got = json.loads(json.dumps(CAPTURES[golden](lane, tmp_path, monkeypatch), sort_keys=True, default=str))
    out = os.environ.get("JOBS_GOLDENS_OUT")
    if out or os.environ.get("JOBS_WRITE_LEDGER_GOLDENS"):
        dest = (Path(out) / "ledgers" if out else GOLDENS) / lane
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{golden}.json").write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        if out:
            return
    want = json.loads((GOLDENS / lane / f"{golden}.json").read_text(encoding="utf-8"))
    allowed = load_allowed(GOLDENS).get(f"{lane}/{golden}", [])
    assert unexplained(diffs(want, got), allowed) == []
