"""A marketing seat's scheduled drafts reach the firm's administrators on a tainted turn.

WHY. A law-firm marketing seat (``seat.role: marketing``) drafts the firm's
blog posts, social posts, YouTube descriptions, Business Profile posts, the
monthly topic slate and the analytics note on a schedule, and emails them to
the firm's administrators for approval. Several of those runs must read outside
content to do the job (web search for the topic slate and the blog sources, the
firm's public YouTube feed for the episode drafts), and that read taints the
session. The taint gate then refuses every send class, the internal one
included (``enforce._TAINT_GATED_CLASSES``), so the drafts could never leave.

The precedent is the date-prep brief (Captain decision 2026-09-28,
``CodeFixedRecipients``): the attack the taint gate exists for is injected text
redirecting mail outward, and it cannot happen when CODE fixes the recipients
and the model cannot supply or change them. This module is that shape for the
marketing seat, scoped as tightly as the brief:

* only a seat whose ``seat.role`` is ``marketing`` (any other seat: refused,
  nothing sent);
* only a SCHEDULED run of one of :data:`MARKETING_CRON_SKILLS`, resolved from
  the cron store (``cron_attribution``, the scheduler's fact) under a managed
  ``op-managed:`` job name, for a skill the seat enables;
* recipients are ``scope.admins`` intersected with ``scope.inbound_allow_from``,
  computed here from the seat's own config. The tool takes no address at all;
* it lifts the taint gate only. The send is still classified, the persona's
  authored ``external_send_internal`` ceiling still decides, and every outbound
  scan (the advertising gate, the output checklist, the content floor) runs on
  the body exactly as on any other send.

One email per run: a second call after a send (or after a send whose answer was
lost) is refused, so a looping turn cannot flood the firm.

WIDENING A TAINT EXEMPTION IS THE CAPTAIN'S CALL. This module ships behind
that decision; see the PR that introduced it.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from shared import cron_attribution, digest_reply_ref, send_anchor, send_dispatch

TOOL_NAME = "marketing_draft_send"
MARKETING_ROLE = "marketing"
MANAGED_PREFIX = "op-managed:"

#: The marketing skills that run on a schedule and email their drafts. A slug
#: not listed here never mints the capability, whatever seat it runs on.
MARKETING_CRON_SKILLS: frozenset[str] = frozenset(
    {
        "marketing-topic-slate",
        "blog-post-draft",
        "social-post-draft",
        "youtube-episode-post",
        "business-profile-post",
        "marketing-analytics-note",
    }
)

MAX_SUBJECT = 200
MAX_TEXT = 60_000

DESCRIPTION = (
    "Send this scheduled marketing run's one email of drafts to the firm's administrators. "
    "Only on a scheduled run of a marketing skill on a marketing seat. You give the subject "
    "and the text; the recipients are fixed by code (the seat's administrators on its roster) "
    "and you never name an address. Every outbound check still runs on the text. One email "
    "per run."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "The email's subject line."},
        "text": {
            "type": "string",
            "description": (
                "The email body: a short cover, then each draft between a line "
                "'--- DRAFT <channel> ---' and a line '--- END DRAFT ---'."
            ),
        },
    },
    "required": ["subject", "text"],
    "additionalProperties": False,
}

_OUTCOME_UNKNOWN = "outcome_unknown"
#: session id -> "sent" | "outcome_unknown". Bounded: a scheduled run is one
#: session, and a long-lived Machine must not grow this without limit.
_SENT: OrderedDict[str, str] = OrderedDict()
_MAX_SESSIONS = 256
_LOCK = threading.Lock()


def _mark(session_id: str, state: str) -> None:
    with _LOCK:
        _SENT[session_id] = state
        _SENT.move_to_end(session_id)
        while len(_SENT) > _MAX_SESSIONS:
            _SENT.popitem(last=False)


def _refusal(reason: str) -> str:
    return json.dumps({"status": "refused", "reason": reason}, ensure_ascii=False)


def fixed_recipients(cfg: Any) -> tuple[str, ...]:
    """``scope.admins`` that are also exact ``scope.inbound_allow_from`` entries.

    An ``@domain`` roster grant admits nobody here: the recipient set is people
    the seat names twice, by address, never a domain."""
    roster = {entry for entry in cfg.inbound_roster if not entry.startswith("@")}
    return tuple(address for address in cfg.admins if address in roster)


def _seat_role(cfg: Any) -> str:
    seat = cfg.seat
    role = seat.get("role") if isinstance(seat, dict) else None
    return role.strip() if isinstance(role, str) else ""


def _skill_enabled(cfg: Any, persona: str, skill: str) -> bool:
    for entry in cfg.personas:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("slug") or entry.get("name") or "").strip() != persona:
            continue
        for row in entry.get("skills") or []:
            if isinstance(row, dict) and row.get("name") == skill and row.get("enabled") is True:
                return True
    return False


def resolve(session_id: str, cfg: Any, routine: Any) -> tuple[str, tuple[str, ...]] | str:
    """``(skill, recipients)`` this run may send to, or the refusal sentence."""
    if _seat_role(cfg) != MARKETING_ROLE:
        return "this tool runs only on a marketing seat; nothing was sent"
    if routine is None or not routine.skill or not routine.persona:
        return "this tool runs only on a scheduled marketing run; on any other turn, reply in the thread"
    if not str(routine.job_name or "").startswith(MANAGED_PREFIX):
        return "this run is not a scheduled routine the seat authored; nothing was sent"
    if routine.skill not in MARKETING_CRON_SKILLS:
        return f"{routine.skill} does not send marketing drafts; nothing was sent"
    if not _skill_enabled(cfg, routine.persona, routine.skill):
        return f"{routine.skill} is not enabled on this seat; nothing was sent"
    recipients = fixed_recipients(cfg)
    if not recipients:
        return "the seat names no administrator on its roster to send to; nothing was sent"
    return routine.skill, recipients


def marketing_draft_send(
    args: Any,
    *,
    session_id: str,
    load_config: Callable[[], Any],
    routine: Callable[[str], Any] = cron_attribution.resolve_routine,
    dispatch: Callable[..., Any] = send_dispatch.dispatch,
) -> str:
    """Send the run's one email. Never raises."""
    args = args if isinstance(args, dict) else {}
    subject, text = args.get("subject"), args.get("text")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT:
        return _refusal(f"give a subject of at most {MAX_SUBJECT} characters")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        return _refusal(f"give the email text, at most {MAX_TEXT} characters")
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001 - an unreadable config sends nothing
        return _refusal("the seat's configuration could not be read; nothing was sent")
    decided = resolve(session_id, cfg, routine(session_id))
    if isinstance(decided, str):
        return _refusal(decided)
    skill, recipients = decided
    with _LOCK:
        prior = _SENT.get(session_id)
        if prior == _OUTCOME_UNKNOWN:
            return _refusal(
                "this run's email may already have gone out; SMD is checking. Send nothing else"
            )
        if prior:
            return _refusal("this run's email has already gone out; send nothing else")
    to = list(recipients)
    result = dispatch(
        to=to,
        subject=subject.strip(),
        text=text,
        anchor=None,
        lane=send_anchor.skill_lane(skill),
        session_id=session_id,
        templated=False,
        audit_extra={
            "skill_name": skill,
            "dispatch_ref": digest_reply_ref.mint_dispatch_ref(),
            "body_variant": "full",
        },
        # The capability is minted here, from the seat's own config, and
        # nowhere a tool argument can reach. It lifts the taint gate only.
        code_fixed_recipients=send_dispatch.CodeFixedRecipients(
            to=tuple(to), source=f"{skill} marketing administrators"
        ),
    )
    if getattr(result, "outcome_unknown", False):
        _mark(session_id, _OUTCOME_UNKNOWN)
        return json.dumps(
            {
                "status": "outcome_unknown",
                "reason": getattr(result, "reason", ""),
                "note": "The email may or may not have gone out. Do not send it again; report that in one line.",
            },
            ensure_ascii=False,
        )
    if not getattr(result, "sent", False):
        return json.dumps(
            {
                "status": "not_sent",
                "reason": getattr(result, "reason", ""),
                "note": "Not sent. Fix only what the reason names and try once more, or report it in one line.",
            },
            ensure_ascii=False,
        )
    _mark(session_id, "sent")
    return json.dumps(
        {"status": "sent", "message_id": getattr(result, "message_id", ""), "recipients": len(to)},
        ensure_ascii=False,
    )


def _reset_for_tests() -> None:
    with _LOCK:
        _SENT.clear()


__all__ = [
    "DESCRIPTION",
    "MARKETING_CRON_SKILLS",
    "SCHEMA",
    "TOOL_NAME",
    "fixed_recipients",
    "marketing_draft_send",
    "resolve",
]
