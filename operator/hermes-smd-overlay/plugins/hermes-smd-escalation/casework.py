"""The casework reply tool: a person's plain-word answer to an Operator list.

The spec is ss-console ``docs/specs/operator/case-manager-deadline-work.md``.
One tool is left of the case-manager set, and it takes no identifier from the
model:

* ``reply_verdicts``. It resolves a verified reply's thread to the raise rows the
  broker stamped, parses the words (``reply_items.parse_reply_verdicts``),
  writes ``approved`` / ``held`` rows and queues the approved writes. The
  number-to-task map is code; the model never supplies an id. A thread that
  holds no casework rows falls through to ``escalation_reply_ack``, so one tool
  serves every reply: the deadline digest's "done with 1", and a Case File
  Manager list's approvals.

The task review (``casework_finish``), the date-prep brief (``casework_brief``)
and its step record (``casework_step_done``) were removed 2026-11-10 with the
two routines they served, which no seat ever switched on.

What this tool refuses, and why each refusal writes nothing:
* a reply naming a number not on the list;
* a number both approved and held;
* a thread that carries two lists;
* a bare "yes" to a list of several lines.
Each case asks instead. A wrong approval closes a real task, and a question
costs one more email.
"""

from __future__ import annotations

from typing import Any

from .casework_reply import reply_verdicts
from .casework_rules import ledger_path

EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

#: Byte-identical to the description live seats registered before the 2026-11-10
#: removal, on purpose: the model-facing text of the one tool that stays does
#: not change in a removal PR.
REPLY_DESCRIPTION = (
    "Record a person's plain-word reply to an Operator email (a task review, a "
    "date-prep brief, or a deadline digest), for example 'yes to all', 'all except "
    "3', 'leave 2', 'yes on 1', 'got it on 1'. Takes NO arguments: the reply, its "
    "sender, the email it answers and the numbered lines are read from the verified "
    "inbound message and the ledger. If it returns writes_queued, make that many "
    "mcp_smokeball_update_task calls (the arguments are filled in for you) and call "
    "reply_verdicts again. If it returns steps_to_run, run each step's skill on that "
    "matter with those params. Send confirmation_text back verbatim, and nothing "
    "when it is empty."
)


__all__ = [
    "EMPTY_SCHEMA",
    "REPLY_DESCRIPTION",
    "ledger_path",
    "reply_verdicts",
]
