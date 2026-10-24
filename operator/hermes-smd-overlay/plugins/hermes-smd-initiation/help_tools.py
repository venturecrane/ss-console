"""The self-improving guide's tools and their gates (ss-console#3423, increment 3).

Three entry points the initiation plugin registers or dispatches to:

* ``operator_help_signals`` with ``scope: "review"`` (:func:`review_handler`):
  the week's struggle signals, counts only (``help_review.review``);
* ``operator_help_guide_update`` (:func:`update_handler`): promote, demote or
  reorder one SMD-authored tip id in the emphasis list, nothing else;
* ``operator_help_guide_undo`` (:func:`undo_handler`): restore the list before
  the newest change.

WHO MAY DRIVE THEM, decided here from platform facts and never from an argument:

* a turn the scheduler fired for the ``operator-help`` routine (its session id
  resolves through ``shared.cron_attribution`` to that skill) may review and
  update. That is the daily review;
* an email turn whose verified sender is an SMD person on this seat's
  ``scope.admins`` (address at one of ``OPS_REPLY_DOMAINS``) may review with
  ``force: true`` (the on-demand proof), update, and undo. Undo is SMD's only:
  the weekly note goes to SMD, and the "undo" reply comes from SMD;
* everyone else, a firm person included, is refused. The list only reorders
  SMD's own words, so the harm a client could do is small, and that is still
  not a reason to let a client steer it.

Every handler returns JSON and never raises: a fault is a refusal the model can
read, never an opaque tool error it might paraphrase into a claim.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shared import cron_attribution, request_index
from shared.customer_config import OPS_REPLY_DOMAINS

from . import help_guide, help_review, seat_facts

logger = logging.getLogger(__name__)

TOOL_HELP_GUIDE_UPDATE = "operator_help_guide_update"
TOOL_HELP_GUIDE_UNDO = "operator_help_guide_undo"

#: The routine whose scheduled turn runs the review.
ROUTINE_SKILL = help_guide.SKILL

GUIDE_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(help_review.ACTIONS),
            "description": (
                "promote = put this tip first in the help answer (only when its kind is "
                "actionable in this turn's review); demote = stop emphasizing it; "
                "reorder = move an emphasized tip to position."
            ),
        },
        "tip_id": {
            "type": "string",
            "maxLength": 64,
            "description": "The id of one tip from the review's kinds[].tips. Nothing else.",
        },
        "position": {
            "type": "integer",
            "minimum": 0,
            "description": "reorder only: the new zero-based position.",
        },
    },
    "required": ["action", "tip_id"],
    "additionalProperties": False,
}

GUIDE_UPDATE_DESCRIPTION = (
    "Change which SMD-written tips lead the help answer: promote, demote or reorder one "
    "tip by its id. You cannot write or change any tip's words. A promotion is accepted "
    "only for a tip whose kind this week's review marks actionable, at most "
    f"{help_review.MAX_CHANGES} changes a week, and every change is recorded so SMD can "
    "undo it. Used only in the help review."
)

GUIDE_UNDO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

GUIDE_UNDO_DESCRIPTION = (
    "Restore the help tips' emphasis to what it was before the most recent change. "
    "Only for an SMD person's undo reply to the weekly help note."
)


def _cfg() -> Any | None:
    from . import _load_config  # the plugin's own config loader (tests patch it)

    return _load_config()


def _scheduled_review(session_id: Any) -> bool:
    if not isinstance(session_id, str) or not session_id:
        return False
    routine = cron_attribution.resolve_routine(session_id)
    return routine is not None and routine.skill == ROUTINE_SKILL


def _smd_sender(session_id: Any) -> str | None:
    """The turn's verified sender when it is an SMD admin of this seat, else None."""
    from . import _help_origin

    origin = _help_origin(session_id)
    if origin is None:
        return None
    sender = request_index.bare_sender(origin.sender_address)
    cfg = _cfg()
    if sender is None or cfg is None:
        return None
    domain = sender.rpartition("@")[2]
    if domain not in OPS_REPLY_DOMAINS or sender not in cfg.admins:
        return None
    return sender


def _tips() -> list[dict[str, str]] | None:
    try:
        return help_guide.read_tips(seat_facts.skill_roots())
    except Exception:  # noqa: BLE001 - the review reports "tips" unreadable
        logger.warning("help_tools: tips unreadable", exc_info=True)
        return None


def _job_reader(days: int) -> dict[str, Any]:
    from shared.medchron_client import MedchronBrokerClient

    return MedchronBrokerClient().help_job_signals(days)


def run_review() -> dict[str, Any]:
    """The review itself, ungated. Tests and the update tool's evidence."""
    return help_review.review(tips=_tips(), job_reader=_job_reader)


def review_handler(args: dict[str, Any], session_id: Any) -> str:
    """``scope: "review"``. Refused outside the review routine unless an SMD
    admin asked with ``force: true``."""
    force = args.get("force") is True
    if not _scheduled_review(session_id) and not (force and _smd_sender(session_id)):
        return json.dumps(
            {
                "read": False,
                "refused": (
                    "the review runs on its schedule, or for an SMD administrator with force: true"
                ),
            }
        )
    return json.dumps(run_review())


def update_handler(args: dict[str, Any] | None = None, session_id: Any = "", **_: Any) -> str:
    """One emphasis change, with a fresh review as its evidence. Never raises."""
    args = args if isinstance(args, dict) else {}
    try:
        if not _scheduled_review(session_id) and not _smd_sender(session_id):
            return json.dumps(
                {"changed": False, "refused": "only the help review or SMD may change the tips"}
            )
        tips = _tips()
        if tips is None:
            return json.dumps({"changed": False, "refused": "the tips could not be read"})
        evidence = None
        if args.get("action") == help_review.ACTION_PROMOTE:
            evidence = help_review.review(tips=tips, job_reader=_job_reader, store=False)
        out = help_review.update(
            args.get("action"),
            args.get("tip_id"),
            tips=tips,
            review=evidence,
            position=args.get("position"),
        )
        return json.dumps(out)
    except Exception:  # noqa: BLE001 - a tool handler must not raise into the loop
        logger.warning("%s: update failed", TOOL_HELP_GUIDE_UPDATE, exc_info=True)
        return json.dumps({"changed": False, "refused": "the change could not be made"})


def undo_handler(args: dict[str, Any] | None = None, session_id: Any = "", **_: Any) -> str:
    """Restore the last snapshot, for an SMD admin's email turn only. Never raises."""
    try:
        if not _smd_sender(session_id):
            return json.dumps({"restored": False, "refused": "only SMD may undo a tip change"})
        return json.dumps(help_review.undo())
    except Exception:  # noqa: BLE001
        logger.warning("%s: undo failed", TOOL_HELP_GUIDE_UNDO, exc_info=True)
        return json.dumps({"restored": False, "reason": "the undo could not be made"})


__all__ = [
    "GUIDE_UNDO_DESCRIPTION",
    "GUIDE_UNDO_SCHEMA",
    "GUIDE_UPDATE_DESCRIPTION",
    "GUIDE_UPDATE_SCHEMA",
    "ROUTINE_SKILL",
    "TOOL_HELP_GUIDE_UNDO",
    "TOOL_HELP_GUIDE_UPDATE",
    "review_handler",
    "run_review",
    "undo_handler",
    "update_handler",
]
