"""Whether a job's thread already holds a reply sent after the job finished.

WHY (A&P, 2026-11-06). A drafting job's completion reply went out as an SMD
hand release (``shared.matter_file_review``), a plain reply outside the bound
reply binding (``reply_binding.py``), so the job's claim was never taken and its
``reply_sent_at`` stayed empty. A ``jobs rewake`` would then have sent the
code-written completion a SECOND time. The claim cannot see a reply it never
took; the mailbox can. Anything this mailbox sent in the job's thread after the
job's last state change (its finish) is an answer the binding did not make, so
the job's reply is refused and a person decides.

Its own module so the binding (which refuses at bind and at send) and the
runner's status read (``hand_reply.thread_status``) share one rule.
"""

from __future__ import annotations

from typing import Any

from . import msgraph_lookup
from .msgraph_ops import MsGraphOps


def after_finish(
    ops: MsGraphOps, noun: str, kind: str, ident: str, row: dict[str, Any] | None, conversation_id: str
) -> str:
    """The refusal sentence when job ``ident``'s thread holds a reply this
    mailbox sent after the job finished, else "". An SMD note is SMD's own
    words, recorded once, and never checked here."""
    finished = str((row or {}).get("updated_at") or "")
    if kind == "note_job" or not finished:
        return ""
    if not msgraph_lookup.sent_in_conversation_since(ops, conversation_id, finished):
        return ""
    return (
        f"{noun} job {ident}'s thread already holds a reply sent from this mailbox after the job finished "
        "(an SMD hand reply counts); a second completion reply is refused and nothing was sent."
    )


__all__ = ["after_finish"]
