"""Every litigation pass is shown the closed case-status list it must answer in.

The read pass is held to it by its tool schema's enum; the verify and audit
passes are not (``corrected`` is free-form), so the prompt text is the only
place they learn the list. 2026-10-14, A&P litigation proof: the audit pass on
a minor's-compromise matter wrote "I cannot pick between them without the
allowed status list", recorded "Unclear", and the gates held the list.

A defendant's status is no longer any pass's to choose (``defstatus.derive``,
2026-11-05), so no pass is shown that list: what it names instead is the events.
"""

from __future__ import annotations

import datetime as dt

import pytest

from litigation_testkit import make_inputs
from jobs.litigation import read, vocab
from jobs.litigation.firm import load


@pytest.mark.parametrize("role", ["read", "verify", "audit"])
def test_each_pass_names_every_case_status_and_every_event(tmp_path, role):
    firm = load(make_inputs(tmp_path / "in"))
    text = read.system_for(role, firm, dt.date(2026, 10, 14))
    for s in vocab.CASE_STATUSES:
        assert f'"{s}"' in text, (role, s)
    for e in vocab.DEFENDANT_EVENTS:
        assert e in text, (role, e)
    assert f'"{vocab.DROPPED}"' not in text and f'"{vocab.SERVED_NO_ANSWER}"' not in text
