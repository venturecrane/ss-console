"""Whether a logic-in-code check refuses, or only says what it would have refused.

A merge reaches a client seat without a person, so a check that can refuse an act
ships switched off per seat (``logic_in_code``, ADR 0104). Every such check in this
connector asks here, on the call, with the sentence it would refuse with:

* ``enforce``: the sentence comes back and the caller refuses with it.
* ``observe`` (the default): an observation row records the sentence and the
  caller carries on as if the check were not there.

The caller never reads the switch itself, so a check cannot forget one half.
"""

from __future__ import annotations

from . import logic_in_code, outcome_ledger


def spends_on_a_read(switch: str) -> bool:
    """Whether a check may pay to read its source. Only when it can act on the
    answer: a check that only observes never costs the seat a model read."""
    return logic_in_code.load_switch(switch).enforcing


def refusal(
    switch: str,
    problem: str,
    *,
    tool: str,
    status: str = outcome_ledger.WOULD_REFUSE,
    sha256: str = "",
    message_id: str = "",
    first_page: int | None = None,
    last_page: int | None = None,
    matter_id: str = "",
) -> str | None:
    """``problem`` when the switch enforces, else None with the row written."""
    if logic_in_code.load_switch(switch).enforcing:
        return problem
    outcome_ledger.observe(
        switch,
        tool,
        status,
        problem,
        sha256=sha256,
        message_id=message_id,
        first_page=first_page,
        last_page=last_page,
        matter_id=matter_id,
    )
    return None


__all__ = ["refusal", "spends_on_a_read"]
