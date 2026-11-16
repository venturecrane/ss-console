"""A client champion on the guide seat, at $0, through the seat's own path.

Reads the one guide rehearsal of this pytest process
(``test_guide_rehearsal.guide_rehearsal``); the champion's turns are
``_champion_turns.py``. What these tests prove, end to end, with the model
scripted and the broker stubbed:

* a reply that cites a rep-only library document (``demo-meetings.md``) never
  reaches the champion: the relay's wall holds it, and the champion receives
  the one fixed sentence, by code. FALSIFIER: remove the wall step
  (``relay_checks._champion_walled``) and the held text is what reaches them,
  so ``test_a_rep_only_citation_is_held_and_the_sentence_sent`` fails;
* the hold is on the ledger, where the hourly work-health triage flags it;
* the read fence refuses pitch-practice during the champion's turn;
* an answer that cites nothing walled goes out as written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests_e2e._champion_turns import CHAMPION, HELD_TEXT, OPEN_TEXT
from tests_e2e.test_guide_rehearsal import guide_rehearsal

OVERLAY = Path(__file__).resolve().parents[1]
SENTENCE = (
    "That is not available on this guide; for anything else, ask SMD Services at team@smd.services."
)


@pytest.fixture(scope="module")
def rehearsal() -> dict[str, Any]:
    return guide_rehearsal()


def _turn(rehearsal: dict, label: str) -> dict:
    return next(t for t in rehearsal["evidence"]["turns"] if t["label"] == label)


def test_the_sentence_is_the_seats_own() -> None:
    sys.path.insert(0, str(OVERLAY))
    from shared import champion

    assert champion.SENTENCE == SENTENCE


def test_a_rep_only_citation_is_held_and_the_sentence_sent(rehearsal) -> None:
    turn = _turn(rehearsal, "c-asks-for-the-demo-playbook")
    assert turn["router_verdict"] != {"action": "skip"}, "the champion's email ran a turn"
    assert turn["reply"] == HELD_TEXT, "the model wrote the rep-only answer"
    assert turn["replies"] == [SENTENCE], turn["replies"]
    assert "demo-meetings" not in json.dumps(turn["replies"])


def test_the_hold_is_on_the_ledger_for_the_triage(rehearsal) -> None:
    rows = rehearsal["evidence"]["champion"]["ledger_rows"]
    held = [json.loads(r["metadata"]) for r in rows if r["action_type"] == "REPLY_HELD"]
    assert [h["reason"] for h in held] == ["champion_wall"], held
    assert held[0]["rule"] == "source_not_allowed" and held[0]["recipient"] == CHAMPION


def test_the_read_fence_refuses_pitch_practice_on_a_champions_turn(rehearsal) -> None:
    turn = _turn(rehearsal, "c-reads-the-skills")
    # The fence blocks before dispatch: the read never runs, and the model is
    # handed the refusal as the tool's result.
    assert [c for c in turn["calls"] if c["tool"] == "read_file"] == []
    lines = (Path(rehearsal["root"]) / "model-requests.jsonl").read_text(encoding="utf-8")
    bodies = [
        json.dumps(r["body"])
        for r in map(json.loads, lines.splitlines())
        if r["turn"] == turn["label"]
    ]
    assert any("the library documents open to this sender" in b for b in bodies), bodies[-1:]
    assert turn["replies"] == [OPEN_TEXT], "an answer that cites nothing walled goes out"


def test_the_champions_turn_was_told_the_role(rehearsal) -> None:
    lines = (Path(rehearsal["root"]) / "model-requests.jsonl").read_text(encoding="utf-8")
    # Only this slice's turns: in the whole tests_e2e run the other slices'
    # model requests carry the product-guide skill, whose text names the line.
    told = {
        r["turn"]
        for r in map(json.loads, lines.splitlines())
        if r["turn"].startswith("c-")
        and "CHAMPION (platform-decided for this email)" in json.dumps(r["body"])
    }
    assert told == {"c-asks-for-the-demo-playbook", "c-reads-the-skills"}, told
