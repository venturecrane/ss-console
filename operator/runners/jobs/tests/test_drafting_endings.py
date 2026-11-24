"""Every drafting reason code has an ending (owner, how it ends, resumable, what
the firm is told) and a row in the hourly triage, and the two agree.

FALSIFIER: add a code to ``outcome.REASON_CODES`` without a row in
``drafting/endings.py`` or ``bin/lib/work_health_drafting.py`` and this fails.
"""

from __future__ import annotations

import pytest

from bin.lib import work_health_drafting as triage
from jobs.drafting import endings, outcome

#: The triage classes an ending allows.
_ALLOWED = {
    ("firm", ("held",)): {"firm_told"},
    ("smd", ("held", "failed")): {"smd_check"},
    ("smd", ("held",)): {"smd_request"},
    ("smd", ("delivered",)): {"smd_noted"},
    ("firm", ("delivered",)): {"firm_told"},
}


def test_the_endings_table_covers_every_reason_code_and_nothing_else():
    assert endings.problems() == []


@pytest.mark.parametrize("code", outcome.REASON_CODES)
def test_every_reason_code_has_a_triage_row_that_agrees_with_its_ending(code):
    e = endings.ending(code)
    cls = triage.TRIAGE.get(code)
    assert cls in triage.STEPS, f"{code}: no triage row"
    if e.ends == ("failed",):
        allowed = {"smd_resume", "smd_hand"} if e.resumable else {"smd_fix"}
    else:
        allowed = _ALLOWED[(e.owner, e.ends)]
    assert cls in allowed, (code, e, cls)


def test_the_triage_table_names_no_code_the_runner_does_not_write():
    assert set(triage.TRIAGE) == set(outcome.REASON_CODES)


def test_the_new_discovery_codes_are_reason_codes_with_their_owners():
    owners = {
        c: endings.ending(c).owner for c in ("rule_unchecked", "rule_unmet", "index_unpaired", "challenge_unread")
    }
    assert owners == dict.fromkeys(owners, "smd")
    assert endings.ending("prior_draft_routed").owner == "firm"
    for code in ("rule_unmet", "phrase_missing", "prior_draft_routed"):
        assert outcome.REASON.match(f"{code}: an invented sentence")


def test_a_delivered_ending_says_its_line_with_the_item_filled():
    line = endings.FIRM_MESSAGES[endings.ending("phrase_missing").message].format(item="Response No. 7")
    assert line.startswith("Response No. 7: left as drafted")


def test_triage_falls_back_for_another_lane_or_an_unknown_code():
    assert triage.next_step("chronology", "rule_unmet") is None
    assert triage.next_step("drafting", "not_a_code") is None
    assert triage.next_step("drafting", None) is None
    assert triage.next_step("drafting", "served_amended") == triage.STEPS["smd_hand"]
