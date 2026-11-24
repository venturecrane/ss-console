"""A1 (2026-11-23): a response job on a matter where an earlier Operator job already
delivered the draft of one of its served sets. That set is named with its draft
and that job, never drafted again; the other sets are drafted; the reply names
the draft in the endings table's words, and nothing is owed on it. Every name
and number is invented.

The finder's half (a delivered draft of the same set; another party's; a hand
draft; two drafts) is in ``test_drafting_served_find.py``; the broker's half (a
change in reply reaches the draft) in
``workspace_broker/tests/test_drafting_prior_draft_route.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs.drafting import endings, outcome, served
from jobs.drafting.outcome import DraftingFailed

PRIOR = "Discovery Responses - Responses to Special Interrogatories, Set One - 100001 - 11-01-26.docx"
JOB = "01PRIORJOB"


def _set(kind: str, *, prior: str = "", drafted_by: str = "operator") -> served.ServedSet:
    label = served.SET_TYPES[kind][1]
    text = "\n".join(f"{label} NO. {i}: Example request {i}." for i in (1, 2))
    return served.ServedSet(kind, 1, "Defendant Delta Example", "Plaintiff Gamma Example", 2, text, drafted_by,
                            prior_draft=prior, prior_job=JOB if prior else "")  # fmt: skip


def test_a_named_set_is_not_planned_and_its_row_is_filed_and_told_with_its_job():
    sets = [_set("special_interrogatories", prior=PRIOR), _set("requests_for_production")]
    plan = served.plan_units(sets)
    assert [p["variant"] for p in plan] == ["requests_for_production"]
    rows = served.outcomes(sets, plan, {"set-1": "RFP doc.docx"})
    assert rows[0] == {"set": sets[0].label, "drafted_by": "operator", "status": "filed", "document": PRIOR,
                       "prior_draft": True, "prior_job": JOB, "told": True}  # fmt: skip
    assert rows[1]["status"] == "filed" and "prior_draft" not in rows[1]


def test_the_reply_line_is_the_endings_tables_sentence_and_the_broker_says_the_same():
    from workspace_broker import delivered_matter

    row = served.outcomes([_set("special_interrogatories", prior=PRIOR)], [], {})[0]
    line = served.reply_lines([row])[0]
    want = endings.FIRM_MESSAGES[endings.ending("prior_draft_routed").message]
    assert line == want.format(item=f'Responses to Special Interrogatories, Set One, "{PRIOR}"') + "."
    assert "?" not in line and line in delivered_matter._served_sets({"served_sets": [row]})


def test_the_delivered_verdict_carries_the_prior_draft_routed_code_only_when_a_set_was_named():
    rows = served.outcomes([_set("special_interrogatories", prior=PRIOR), _set("requests_for_production")],
                           [{"key": "set-1", "served": 1}], {"set-1": "x.docx"})  # fmt: skip
    reason = served.prior_reason(rows)
    assert reason and outcome.REASON.match(reason) and reason.startswith("prior_draft_routed: ")
    assert served.prior_reason(rows[1:]) is None


def test_served_json_round_trips_the_prior_draft_and_omits_it_when_empty(tmp_path: Path):
    sets = [_set("special_interrogatories", prior=PRIOR), _set("requests_for_production")]
    raw = json.loads(served.to_file(sets))["sets"]
    assert raw[0]["prior_job"] == JOB and "prior_draft" not in raw[1] and "prior_job" not in raw[1]
    got = served.parse(served.to_file(sets).encode(), tmp_path)
    assert [(s.prior_draft, s.prior_job) for s in got] == [(PRIOR, JOB), ("", "")]


def test_a_hand_file_naming_every_operator_set_as_drafted_is_invalid(tmp_path: Path):
    with pytest.raises(DraftingFailed, match="^served_invalid: .*prior_draft"):
        served.parse(served.to_file([_set("special_interrogatories", prior=PRIOR)]).encode(), tmp_path)


def test_an_unasked_amended_reading_on_an_unproven_lane_lets_the_finder_go_on_as_on_main():
    """Review 11-23: no new stop. Every set answered and a letter on file, the request
    not asking for amended responses, the lane not proven: NOT (the finder ends served_none)."""
    from jobs.drafting import amended

    answered, letters = [(_set("special_interrogatories"), object())], [object()]
    req = "Please draft responses to the discovery on matter 100001."
    assert amended.decide(req, False, answered, letters, is_proven=lambda: False) == amended.NOT  # type: ignore[arg-type]
    assert amended.decide(req, False, answered, letters, is_proven=lambda: True) == amended.AMENDED  # type: ignore[arg-type]


def test_a_prior_draft_without_its_job_is_invalid(tmp_path: Path):
    raw = json.loads(served.to_file([_set("special_interrogatories"), _set("requests_for_production")]))
    raw["sets"][0]["prior_draft"] = PRIOR
    with pytest.raises(DraftingFailed, match="go together"):
        served.parse(json.dumps(raw).encode(), tmp_path)
