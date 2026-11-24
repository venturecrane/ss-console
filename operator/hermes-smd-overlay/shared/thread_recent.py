"""The turn's context: what this mailbox already said in the thread it answers.

WHY (A&P, 2026-11-23). SMD trimmed an attorney's special interrogatories and
SMD's note told him so in his thread. His next email opened a turn that saw
only that email, and its reply said nothing had been changed and asked him to
choose again: a contradiction of a delivery already in his thread, and a
question he should never have been asked.

Before an email-opened turn on a Graph seat, the reply plugin's ``pre_llm_call``
asks the broker (``thread_recent``, gateway only; ss-console
``operator/workspace_broker/thread_recent.py``) for the messages this mailbox
sent in that email's conversation (in-turn replies, SMD notes, job deliveries,
SMD's own replies), newest :data:`MAX_SHOWN`, each its time, the start of its
text and its attachment names, and the drafting files delivered for a request in
that thread. :func:`render` turns them into a short block the model reads with
the rule: never contradict them; if the thing asked is already done, say so in
one line.

Code builds the block; the model only reads it. A broker that cannot answer
adds no block: the turn goes on exactly as before, and the relay's question
check (``relay_checks.question_held``) still stands.
"""

from __future__ import annotations

import logging
from typing import Any

from shared import inbound
from shared.jobs_client import JobsBrokerClient

logger = logging.getLogger(__name__)

MAX_SHOWN = 5
_TEXT_MAX = 240

HEADER = (
    "[thread-so-far] What this mailbox already sent in the thread of the email you are answering, "
    "oldest first, read from the mailbox by code. Never contradict it. If what the email asks is already "
    "done, say so in one line; never ask the person to choose or confirm it again."
)


def _when(at: str) -> str:
    text = str(at or "")
    return f"{text[:10]} {text[11:16]} UTC" if len(text) >= 16 else text


def render(answer: dict[str, Any] | None) -> str:
    """The block for one broker answer, or ``""`` when there is nothing to show."""
    if not isinstance(answer, dict) or answer.get("accepted") is not True:
        return ""
    lines: list[str] = []
    for sent in list(answer.get("sent") or [])[-MAX_SHOWN:]:
        if not isinstance(sent, dict):
            continue
        text = " ".join(str(sent.get("text") or "").split())[:_TEXT_MAX].replace('"', "'")
        files = [str(f) for f in sent.get("files") or [] if f]
        to = ", ".join(str(a) for a in sent.get("to") or [] if a)
        line = f'- {_when(sent.get("at", ""))}{f" to {to}" if to else ""}: "{text}"'
        if files:
            line += " Attached: " + ", ".join(files) + "."
        lines.append(line)
    filed = [f for f in answer.get("filed") or [] if isinstance(f, dict)]
    if filed:
        lines.append("Filed on the matter for a request in this thread:")
        for f in filed[-MAX_SHOWN:]:
            names = ", ".join(str(n) for n in f.get("files") or [] if n) or "no file names recorded"
            doc = str(f.get("document") or "document").replace("_", " ")
            lines.append(
                f"- {_when(f.get('at', ''))}: {doc}, matter {f.get('matter') or ''}: {names}"
            )
    if not lines:
        return ""
    return HEADER + "\n" + "\n".join(lines)


def context_for(session_id: str, user_message: Any, client: Any = None) -> str:
    """The block for this turn, or ``""``: only a turn an email on a Graph seat
    opened has a thread to read. Never raises."""
    try:
        # Only an email turn's prompt carries the delimiter. The bind is the
        # inbound plugin's own idempotent one, in case that hook runs after this
        # one; it is skipped for any other prompt, so this never re-marks a turn.
        if not session_id or not isinstance(user_message, str):
            return ""
        if inbound.UNTRUSTED_EMAIL_DELIMITER not in user_message:
            return ""
        inbound.bind_origin_from_prompt(session_id, user_message)
        origin = inbound.SESSION_INBOUND_ORIGIN.bound_this_turn(session_id)
        if origin is None or not origin.conversation_id or not origin.message_id:
            return ""
        broker = client if client is not None else JobsBrokerClient()
        return render(broker.thread_recent(origin.message_id))
    except Exception as exc:  # noqa: BLE001 - no block is the old behaviour, never a failed turn
        logger.warning("thread_recent: no thread context this turn (%s)", type(exc).__name__)
        return ""


__all__ = ["HEADER", "MAX_SHOWN", "context_for", "render"]
