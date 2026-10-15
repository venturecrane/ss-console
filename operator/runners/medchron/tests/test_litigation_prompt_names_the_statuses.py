"""Every litigation pass is shown the closed status lists it must answer in.

The read pass is held to them by its tool schema's enum; the verify and audit
passes are not (``corrected`` is free-form), so the prompt text is the only
place they learn the list. 2026-10-14, A&P litigation proof: the audit pass on
a minor's-compromise matter wrote "I cannot pick between them without the
allowed status list", recorded "Unclear", and the gates held the list.
"""

from __future__ import annotations

import datetime as dt

import pytest

from litigation_testkit import make_inputs
from medchron.litigation import read, vocab
from medchron.litigation.firm import load


@pytest.mark.parametrize("role", ["read", "verify", "audit"])
def test_each_pass_names_every_case_and_defendant_status(tmp_path, role):
    firm = load(make_inputs(tmp_path / "in"))
    text = read.system_for(role, firm, dt.date(2026, 10, 14))
    for s in (*vocab.CASE_STATUSES, *vocab.DEFENDANT_STATUSES):
        assert f'"{s}"' in text, (role, s)
