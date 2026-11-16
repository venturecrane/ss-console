"""A client champion's turns in the guide rehearsal, at $0.

Called from ``test_guide_rehearsal_run.run`` after the desk slice, on the same
seat. A third person enrolls with a champion code (the enrolled entry the
broker writes at redeem, label ``champion``) and emails the guide twice, each
through the webhook router's real intake and Hermes' own agent loop with the
scripted model:

* ``c-asks-for-the-demo-playbook``: the model's final text cites
  ``demo-meetings.md``, a document the library opens to representatives only.
  The text becomes the reply through the seat's own text delivery and relay;
  the relay's champion wall holds it, and the held-notice path sends the
  champion the fixed sentence instead. Nothing of the held text reaches them.
* ``c-reads-the-skills``: the model tries to read pitch-practice's SKILL.md
  during the champion's turn; the read fence refuses it.

Every label starts ``c-`` and each email starts on a fresh day of the
per-sender limit (``_training_turns._fresh_day``), like the other slices.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CHAMPION = "dana@example-brokerage.test"
HELD_TEXT = (
    "Bring the demo seat to the meeting and email it the seven acts in order.\n\n"
    'Source: demo-meetings.md, "The seven acts"'
)


#: An answer with no library citation: nothing in it is walled.
OPEN_TEXT = "An Operator can take on the routine coordination a brokerage's staff repeat every day."


def _enroll_champion(volume: Path) -> None:
    path = volume / "sender-enrollment" / "enrolled.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["enrolled"].append(
        {
            "address": CHAMPION,
            "label": "champion",
            "code_id": "c_111111111111",
            "enrolled_at": "x",
            "name": "Dana Reyes",
        }
    )
    path.write_text(json.dumps(doc), encoding="utf-8")


def run_champion(r: Any) -> dict[str, Any]:
    """The champion's turns. Returns the ledger rows they wrote."""
    from tests_e2e._training_turns import _fresh_day

    _enroll_champion(r.volume)
    before = len(r.ledger_rows())
    _fresh_day()
    r.turn(
        "c-asks-for-the-demo-playbook",
        [],
        sender=CHAMPION,
        body="How would your team run a demo for us?",
        final_text=HELD_TEXT,
    )
    _fresh_day()
    r.turn(
        "c-reads-the-skills",
        [("read_file", {"path": "/app/skills/pitch-practice/SKILL.md"})],
        sender=CHAMPION,
        body="What can an Operator take on for a brokerage?",
        final_text=OPEN_TEXT,
    )
    return {"ledger_rows": r.ledger_rows()[before:]}


__all__ = ["CHAMPION", "HELD_TEXT", "OPEN_TEXT", "run_champion"]
