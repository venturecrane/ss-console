"""An email about drafting documents ALREADY FILED on the matter (2026-11-24).

THE DAY IT WENT WRONG. An attorney replied in the thread where five propounded
special interrogatory sets had been filed the day before ("the interrogatories
need follow-up questions": if trained, describe it; if he saw it deleted, who
and what). Those sets had been filed by SMD's hand after the Operator's own job
failed. The reply's words went only into a DIFFERENT draft still running on the
matter ("we added what you wrote to the instructions"), and nothing revised the
sets he was reading. Earlier that day another reply in the thread had started a
fresh redraft that pulled 502 files.

What this module does, all by code:

* A submit the broker answers with ``underway_job_id`` (the email revises the
  filed documents, or SMD holds it for them, and a draft of the same class is
  still running on the matter): the email's words are carried to that running
  draft too, with ``about_filed`` (``reach_underway``). The reply says what the
  submit's own sentence says; the carry is logged.
* An addition the broker answers with ``filed_in_thread`` (the model added the
  email to a running draft, but the thread also holds filed documents of that
  class): the email is submitted as well, so the filed documents are revised
  or held for SMD (``also_submit``). The reply then says the submit's sentence.
* The broker holding an email for SMD's hand (``HAND_CODES``): the one sentence
  SMD's own follow-up gets, "Let me look into that. I'll reply in this thread.",
  only when the broker recorded it as work SMD owes; else nothing about it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: ss-console ``drafting_route.FILED_BY_SMD`` / ``SEVERAL_DRAFTS``: SMD revises
#: the filed documents by hand.
HAND_CODES = frozenset({"prior_filed_by_smd", "prior_several_drafts"})
#: What the Operator says when the follow-up is SMD's: it owns it, names no one.
LOOK_INTO_SAY = "Let me look into that. I'll reply in this thread."
#: An addition's results after which the email was about the running draft too.
_CARRIED = frozenset({"applied", "compose_started"})


def reach_underway(resp: dict[str, Any], add: Callable[..., str]) -> None:
    """Carry the email to the draft still running on the matter, when the broker
    named one. ``add(job_id, about_filed=True)`` is the drafting tool's own
    add-to-request; its answer is logged, never said (the submit's sentence is)."""
    job_id = str(resp.get("underway_job_id") or "").strip()
    if not job_id:
        return
    try:
        result = json.loads(add(job_id, about_filed=True)).get("result")
    except (ValueError, AttributeError):
        result = "unreadable"
    logger.info("drafting: the email about filed documents reached the running draft: %s", result)


def also_submit(
    added: dict[str, Any], resp: dict[str, Any], submit: Callable[[dict[str, Any]], str]
) -> dict[str, Any]:
    """An addition whose thread also holds filed documents of its class: the
    email is submitted too (``submit(args)``, the drafting submit with the
    broker's matter), and its sentence is the reply's. ``added`` is returned
    unchanged when nothing more was due or the submit gave nothing to say."""
    if resp.get("filed_in_thread") is not True or str(resp.get("result")) not in _CARRIED:
        return added
    args = {
        "matter_id": str(resp.get("matter_id") or ""),
        "matter_number": str(resp.get("matter_number") or ""),
        "document_class": str(resp.get("document_class") or ""),
    }
    try:
        sub = json.loads(submit(args))
    except ValueError:
        return added
    say = sub.get("say") if isinstance(sub, dict) else None
    if not isinstance(say, str) or not say or sub.get("code") == "underway":
        return added  # nothing of its own to say, or it met the running draft again
    out = {k: v for k, v in added.items() if k not in ("instruction", "forbid")}
    out["say"] = say
    for key in ("instruction", "forbid"):
        if key in sub:
            out[key] = sub[key]
    return out


def hand_say(code: str, smd_told: bool, none: str) -> str | None:
    """The sentence for an SMD-hand hold, ``none`` when it was not recorded as
    owed, or None for any other code."""
    if code not in HAND_CODES:
        return None
    return LOOK_INTO_SAY if smd_told else none


__all__ = ["HAND_CODES", "LOOK_INTO_SAY", "also_submit", "hand_say", "reach_underway"]
