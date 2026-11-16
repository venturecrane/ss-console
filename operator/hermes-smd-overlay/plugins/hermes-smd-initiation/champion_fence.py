"""The champion read fence: while a client champion's turn runs, the skills tree narrows.

DEFENSE IN DEPTH. The wall that decides what a champion receives is at the
reply relay (``hermes-smd-reply``, ``shared.champion.wall_reason``), keyed on
the recipient, where the recipient is certain. This fence narrows what such a
turn can READ, so a rep-only document is not in the model's context to begin
with. It cannot be keyed on the sender: Hermes hands ``pre_tool_call`` no
session id, and the thread-local resolver behind it is ambiguous with two
people in flight (``shared.provenance``). So it keys on the in-flight marker
(``shared.champion.IN_FLIGHT``, set by :func:`on_turn_start` at ``pre_llm_call``
with the real session id) and on the session this thread is working under:

* no champion turn in flight: nothing changes, for anyone;
* this thread is working under a session that is not a champion's (it noted
  its own id): nothing changes, so a representative's turn running beside a
  champion's still reads pitch-practice;
* otherwise (this thread is the champion's, or cannot say whose it is):
  ``read_file`` under a skills root may reach only ``product-guide/SKILL.md``, the
  library index (``references/INDEX.md``)
  and the allowlisted ``references/`` documents; ``skill_view`` may name only
  product-guide, with no file or one of those files; ``search_files`` under a
  skills root, and ``terminal`` / ``execute_code`` that mention one, are
  refused.

The refusal is :data:`shared.champion.SENTENCE`'s posture in model terms: the
document is not available on this turn. A fence that cannot decide refuses.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from shared import champion, provenance

from . import skill_fence

logger = logging.getLogger(__name__)

REFUSAL = (
    "could not read that: on this turn only the product-guide skill's own instructions and "
    "the library documents open to this sender can be read. Answer from those, or reply "
    "with the product-guide skill's fixed sentence for what is not available."
)
_PATH_KEYS = ("path", "file_path", "filename", "directory", "dir", "target")


def _champion_thread() -> bool:
    """True unless this thread is plainly working under a non-champion session."""
    session, mode = provenance.resolve_session_with_mode(None)
    if mode in (provenance.MODE_THREAD, provenance.MODE_KEYED) and session:
        return champion.IN_FLIGHT.has(session)
    return True


def _allowed_file(resolved: str, allowed: frozenset[str]) -> bool:
    """Whether a resolved path is product-guide's SKILL.md or an allowed document."""
    for root in skill_fence.SKILL_ROOTS:
        for base in {root, os.path.realpath(root)}:
            guide = os.path.join(base, champion.SKILL)
            if resolved == os.path.join(guide, "SKILL.md"):
                return True
            refs = os.path.join(guide, "references")
            if os.path.dirname(resolved) == refs and os.path.basename(resolved) in allowed:
                return True
    parts = resolved.split(os.sep)
    profiles = skill_fence.PROFILES_DIR.split(os.sep)
    tail = parts[len(profiles) :]
    if (
        parts[: len(profiles)] == profiles
        and len(tail) >= 4
        and tail[1:3] == ["skills", "product-guide"]
    ):
        rest = tail[3:]
        return rest == ["SKILL.md"] or (
            len(rest) == 2 and rest[0] == "references" and rest[1] in allowed
        )
    return False


def _skill_view_allowed(args: dict[str, Any], allowed: frozenset[str]) -> bool:
    names = [args.get(k) for k in ("name", "skill", "slug") if isinstance(args.get(k), str)]
    if [n.strip() for n in names] != [champion.SKILL]:
        return False
    sub = args.get("file_path")
    if not isinstance(sub, str) or not sub.strip() or sub.strip() == "SKILL.md":
        return True
    parts = sub.strip().split("/")
    return len(parts) == 2 and parts[0] == "references" and parts[1] in allowed


def _read_allowed(args: dict[str, Any], allowed: frozenset[str]) -> bool:
    for key in _PATH_KEYS:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        resolved = os.path.realpath(os.path.expanduser(value.strip()))
        if skill_fence._slug_under_root(value.strip()) is not None and not _allowed_file(
            resolved, allowed
        ):
            return False
    return True


def _readable() -> frozenset[str]:
    """The documents under references/ a champion's turn may read: the
    allowlist, and the index itself, which the skill reads to find them."""
    return champion.allowlist() | {champion.INDEX_FILE}


def check(tool_name: str, args: Any) -> str | None:
    """The refusal, or None when this call may proceed on a champion's turn."""
    if tool_name not in skill_fence.FENCED_TOOLS or not isinstance(args, dict):
        return None
    if tool_name == "skill_view":
        return None if _skill_view_allowed(args, _readable()) else REFUSAL
    if not skill_fence.touched_skills(tool_name, args):
        return None
    if tool_name == "read_file":
        return None if _read_allowed(args, _readable()) else REFUSAL
    return REFUSAL


def on_pre_tool_call(**kwargs: Any) -> dict[str, Any] | None:
    tool_name = str(kwargs.get("tool_name") or "")
    if tool_name not in skill_fence.FENCED_TOOLS:
        return None
    try:
        if not champion.IN_FLIGHT.any() or not _champion_thread():
            return None
        message = check(tool_name, kwargs.get("args"))
    except Exception:  # noqa: BLE001 - a fence that cannot decide refuses
        logger.exception("hermes-smd-initiation: champion fence could not decide; refusing")
        return {"action": "block", "message": REFUSAL}
    if message is None:
        return None
    logger.info("hermes-smd-initiation: champion fence refused %s", tool_name)
    return {"action": "block", "message": message}


def on_turn_start(**kwargs: Any) -> dict[str, str] | None:
    """``pre_llm_call``: a client champion's turn is marked in flight, by the
    real session id this hook is handed, and the model is told the sender's
    role. The sender is the session's verified email origin (bound from the
    prompt by ``hermes-smd-inbound``, which registers earlier), never a channel
    identity, so it holds whatever ``sender_id`` the gateway passed. Never raises."""
    try:
        from shared.inbound import SESSION_INBOUND_ORIGIN

        session_id = kwargs.get("session_id")
        sid = session_id if isinstance(session_id, str) else ""
        origin = SESSION_INBOUND_ORIGIN.get(sid) if sid else None
        if origin is None or not champion.is_champion(origin.sender_address):
            return None
        champion.IN_FLIGHT.mark(sid)
        logger.info("hermes-smd-initiation: a client champion's turn is in flight")
        return {"context": f"CHAMPION (platform-decided for this email)\n- {champion.ROLE_LINE}"}
    except Exception:  # noqa: BLE001 - the relay's wall still holds what reaches them
        logger.warning("hermes-smd-initiation: champion turn start failed", exc_info=True)
        return None


def on_turn_end(**kwargs: Any) -> None:
    """``post_llm_call`` / ``on_session_end``: the champion turn is over."""
    session_id = kwargs.get("session_id")
    if isinstance(session_id, str) and session_id:
        champion.IN_FLIGHT.clear(session_id)


def register(ctx: Any) -> None:
    """The fence and the turn-end clear, on a guide seat that switches
    enrollment on (the only place a champion can enroll; the demo seat issues
    representative codes only); nothing on any other seat. An unreadable
    config registers nothing: the relay's wall still decides what reaches a
    champion, and the mark's deadline still runs."""
    try:
        from shared import sender_enrollment
        from shared.customer_config import CustomerConfig

        raw = CustomerConfig.from_volume().raw
        seat = raw.get("seat") if isinstance(raw, dict) else None
        role = seat.get("role") if isinstance(seat, dict) else None
        if role != sender_enrollment.ROLE or not sender_enrollment.enabled_in(raw):
            return
    except Exception:  # noqa: BLE001 - no config, no champion
        logger.info("hermes-smd-initiation: config unreadable; no champion fence")
        return
    ctx.register_hook("pre_llm_call", on_turn_start)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_llm_call", on_turn_end)
    # An interrupted turn fires on_session_end (interrupted=True) and no
    # post_llm_call; without this its mark would outlive the turn.
    ctx.register_hook("on_session_end", on_turn_end)


__all__ = ["REFUSAL", "check", "on_pre_tool_call", "on_turn_end", "on_turn_start", "register"]
