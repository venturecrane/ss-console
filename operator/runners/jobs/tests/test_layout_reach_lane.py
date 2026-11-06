"""The drafting lane lays a rule over the kinds it was compiled for (drafting/firm_rules.py).

Installed rules are not rewritten (overlay ``shared/layout_reach.py``): a rule
compiled before kinds were recorded may carry ``doc_classes == []``, and the lane
keeps applying it to every kind, exactly as before. A rule confirmed with its
kinds, switched on, is installed with exactly those kinds, never ``[]``, so it
reaches only them. Names are made up.
"""

from __future__ import annotations

from typing import Any

import pytest

from jobs.drafting import firm_rules

KINDS = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")


def _rule(doc_classes: list[str]) -> dict[str, Any]:
    clause = {
        "text": "Two blank lines before the court name.",
        "layout": {"front_court_gap_lines": 2},
        "relative": {},
        "base_owned": [],
        "carried": False,
    }
    return {"rule_id": "c0000001", "doc_classes": doc_classes, "compiled": [clause], "prose": []}


@pytest.mark.parametrize("cls", KINDS)
def test_an_installed_rule_with_no_kinds_still_reaches_every_kind(cls):
    layout, _rows, _notes = firm_rules.for_class([_rule([])], cls, on_base=False)
    assert layout == {"front_court_gap_lines": 2}


@pytest.mark.parametrize("cls", KINDS)
def test_a_rule_compiled_for_its_kinds_reaches_only_them(cls):
    layout, _rows, _notes = firm_rules.for_class([_rule(["memo", "discovery_set"])], cls, on_base=False)
    assert (layout == {"front_court_gap_lines": 2}) is (cls in ("memo", "discovery_set"))


@pytest.mark.parametrize("cls", KINDS)
def test_a_rule_for_no_laid_out_kind_reaches_none(cls):
    rule = {**_rule(["demand_letter"]), "compiled": []}
    layout, rows, _notes = firm_rules.for_class([rule], cls, on_base=False)
    assert layout == {} and rows == []
