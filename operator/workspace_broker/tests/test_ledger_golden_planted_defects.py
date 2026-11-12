"""The ledger goldens catch a broken ledger: each test plants one defect in a
REAL broker ledger (an illegal transition allowed, a reply-once mark that
marks twice, a month read that ignores the excluded job) and proves the
golden comparison then FAILS.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger_golden_capture import CAPTURES
from ledger_golden_kit import GOLDENS, diffs


def _differs(tmp_path: Path, mp: Any, lane: str, golden: str) -> list[str]:
    got = json.loads(json.dumps(CAPTURES[golden](lane, tmp_path, mp), sort_keys=True, default=str))
    want = json.loads((GOLDENS / lane / f"{golden}.json").read_text(encoding="utf-8"))
    return diffs(want, got)


def test_the_unplanted_ledger_matches(tmp_path, monkeypatch):
    """The control: nothing planted, the golden holds."""
    assert _differs(tmp_path, monkeypatch, "demand", "transitions") == []


def test_an_illegal_transition_allowed_fails_the_golden(tmp_path, monkeypatch):
    from workspace_broker import demand_ledger

    loose = {k: set(v) | {"submitted"} for k, v in demand_ledger._ALLOWED_NEXT.items()}
    monkeypatch.setattr(demand_ledger, "_ALLOWED_NEXT", loose)
    found = _differs(tmp_path, monkeypatch, "demand", "transitions")
    assert any(d.startswith("matrix.") for d in found), found


def test_a_reply_once_mark_that_marks_twice_fails_the_golden(tmp_path, monkeypatch):
    from workspace_broker.litigation_ledger import LitigationLedger

    monkeypatch.setattr(LitigationLedger, "mark_replied", lambda self, job_id, key="reply": True)
    found = _differs(tmp_path, monkeypatch, "litigation", "reply_once")
    assert any(d.startswith("rows.") for d in found), found


def test_a_month_read_that_ignores_the_exclusion_fails_the_golden(tmp_path, monkeypatch):
    from workspace_broker.negotiation_ledger import NegotiationLedger

    real = NegotiationLedger.spend_between
    monkeypatch.setattr(NegotiationLedger, "spend_between", lambda self, start, end, exclude="": real(self, start, end))
    found = _differs(tmp_path, monkeypatch, "negotiation", "month")
    assert any(d.startswith("reads.") for d in found), found
