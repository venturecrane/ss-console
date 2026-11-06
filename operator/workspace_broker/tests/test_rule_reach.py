"""A firm rule's reach is carried from the proposal row to the committed adjustment.

The seat decides which kinds and which attorney a rule covers (overlay
``shared/rule_reach.py``); the broker checks the shape and carries the two fields
unchanged, so what the person confirmed is what installs (rule_reach.py here).
Names are made up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_rule_proposals import _broker, _confirm, _propose, _submission

from workspace_broker.establishment import EstablishmentValidationError

KINDS = ["mediation_brief", "memo"]


def _subject(**reach):
    return {"output_class": "work_product", "property": "voice", **reach}


def test_the_reach_rides_from_the_row_to_the_adjustment(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker, subject=_subject(doc_kinds=KINDS, attorney="  Dana   Whitlock ", reader="mediator"))
    assert proposed["subject"]["doc_kinds"] == KINDS
    assert proposed["subject"]["attorney"] == "Dana Whitlock"
    result = _confirm(broker, proposed["proposal_id"])
    adjustment = _submission(broker, result["run_id"])["adjustment"]
    assert adjustment["doc_kinds"] == KINDS
    assert adjustment["attorney"] == "Dana Whitlock"
    assert adjustment["reader"] == "mediator"


def test_a_rule_with_no_reach_commits_as_it_always_did(tmp_path):
    broker = _broker(tmp_path)
    proposed = _propose(broker, subject=_subject())
    adjustment = _submission(broker, _confirm(broker, proposed["proposal_id"])["run_id"])["adjustment"]
    assert not {"doc_kinds", "attorney", "reader"} & set(adjustment)


@pytest.mark.parametrize(
    "reach",
    [
        {"doc_kinds": []},
        {"doc_kinds": "memo"},
        {"doc_kinds": ["memo", "memo"]},
        {"doc_kinds": ["Memo!"]},
        {"attorney": "Dana Whitlock"},
        {"doc_kinds": ["memo"], "attorney": "two\nlines"},
        {"reader": "mediator"},
        {"doc_kinds": ["memo"], "reader": "The Court!"},
    ],
)
def test_a_malformed_reach_is_refused(tmp_path, reach):
    with pytest.raises(EstablishmentValidationError, match="subject.(doc_kinds|attorney|reader)"):
        _propose(_broker(tmp_path), subject=_subject(**reach))
