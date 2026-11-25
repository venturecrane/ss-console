"""Step 5c of the relay: code writes the reply to a request for work
(``shared.turn_facts``, 2026-11-24).

On a law-firm seat, an in-turn reply (not a bound one, not a records reply with
a facts block) whose turn included an act goes out in code's words: the turn's
required sentences, one line per file it filed, placed or created, and
:data:`turn_facts.LOOK_INTO` once when an act or read failed and no retry of the
same target succeeded. The model's draft is discarded. Each look-into writes one
owed row (``shared.smd_owed``, kind ``look_into``) so SMD finishes the work, and
a thread gets the line once until SMD has answered the earlier one (the owed
item is closed). An owed row that cannot be written is logged and the line
still goes: the attorney is never left with silence for our bookkeeping.

A turn with no act keeps the model's words, with two checks on them, each held
for a redraft and, past the cap, replaced by code's words:

* ``act_owed``: the email asks for the work if it is not done ("was this done,
  and if not, please do so") and the reply says it is not done. The work is
  owed now, not an answer. Past the cap: the look-into line, and SMD is told.
* ``status_contradicted``: the reply says a document is not on the matter that
  the matter's file list read this turn shows. Past the cap: code states it is
  filed.

Never raises out of the relay: a fault leaves the model's draft to the checks
after it, which is today's path.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import OrderedDict
from types import ModuleType
from typing import Any

from shared import inbound, smd_owed, turn_facts, turn_says

from . import held_notice, job_reply

_PACKAGE = __name__.rpartition(".")[0]
logger = logging.getLogger(_PACKAGE)


def _held(reason: str, origin: Any, **extra: Any) -> None:
    """The plugin's ``_held``, read off the module loaded now (a test loads the
    plugin per test, and the decision recorder wraps the name where it lives)."""
    plugin: ModuleType = sys.modules[_PACKAGE]
    plugin._held(reason, origin, **extra)


#: A thread already waits on SMD for an earlier look-into, and this turn has
#: nothing else to say: nothing is sent, and SMD is told of this email too.
LOOK_INTO_OWED_REASON = "look_into_owed"
LOOK_INTO_OWED_DETAIL = (
    "look_into_owed: nothing was sent. SMD is already following up in this thread, and this email "
    "is added to that. Do not draft a reply to it again; end the turn."
)
ACT_OWED_REASON = "act_owed"
ACT_OWED_DETAIL = (
    "act_owed: nothing was sent and the one reply is still yours. The email asks for the work to be "
    "done if it has not been, and your reply says it has not been done. Do the work now, with its "
    "tool, in this turn, then draft the reply again. Never stop at answering the question."
)
STATUS_REASON = "status_contradicted"
STATUS_DETAIL = (
    "status_contradicted: nothing was sent and the one reply is still yours. The matter's file list "
    'you read this turn shows "{name}" on the matter, and your reply says it is not there. Draft it '
    "again from the file list."
)
#: Redrafts each email gets for the two checks; past them code writes the reply.
MAX_REDRAFTS = 2
_BODY_KEYS = frozenset({"text", "html", "body_text", "body_html", "body"})
_NO_MESSAGE_ID = "<no message id>"


class LookIntos:
    """Per thread, the email whose reply carried the look-into line, its owed
    reference and whether SMD was told; and per email, what code wrote."""

    _MAX = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: OrderedDict[str, tuple[str, str, bool]] = OrderedDict()
        self._owed: OrderedDict[str, bool] = OrderedDict()
        self._composed: OrderedDict[tuple[str, str], turn_facts.Composed] = OrderedDict()
        #: Holds of the two model-word checks, per email (their redraft cap).
        self.holds = held_notice.HoldCounter()

    def thread(self, key: str) -> tuple[str, str, bool] | None:
        with self._lock:
            return self._threads.get(key)

    def claim(self, key: str, message_id: str, ref: str, told: bool) -> None:
        with self._lock:
            self._threads[key] = (message_id, ref, told)
            _trim(self._threads, self._MAX)

    def owed(self, message_id: str) -> bool | None:
        with self._lock:
            return self._owed.get(message_id)

    def note_owed(self, message_id: str, told: bool) -> None:
        with self._lock:
            self._owed[message_id] = told
            _trim(self._owed, self._MAX)

    def note_composed(
        self, session_id: str, message_id: str, composed: turn_facts.Composed
    ) -> None:
        with self._lock:
            self._composed[(session_id, message_id)] = composed
            _trim(self._composed, self._MAX)

    def composed(self, session_id: str, message_id: str) -> turn_facts.Composed | None:
        with self._lock:
            return self._composed.get((session_id, message_id))

    def forget_composed(self, session_id: str, message_id: str) -> None:
        """Each draft is decided afresh: what code wrote for an earlier draft of
        this email never exempts a later one from the checks."""
        with self._lock:
            self._composed.pop((session_id, message_id), None)

    def clear(self) -> None:
        with self._lock:
            self._threads.clear()
            self._owed.clear()
            self._composed.clear()
            self.holds = held_notice.HoldCounter()


def _trim(table: OrderedDict, limit: int) -> None:
    while len(table) > limit:
        table.popitem(last=False)


LOOK_INTOS = LookIntos()


def _smd(address: Any) -> bool:
    from shared.heartbeat import _is_smd_address

    return _is_smd_address(address)


def with_body(args: dict[str, Any], text: str) -> dict[str, Any]:
    """The draft's arguments with code's words as its whole body."""
    out = {k: v for k, v in args.items() if k not in _BODY_KEYS}
    out["text"] = text
    out["html"] = job_reply.plain_html(text)
    return out


def _owe(origin: Any, reply_to: Any, failed: tuple[str, ...]) -> bool:
    """Write this email's owed row once; True when SMD was told."""
    have = LOOK_INTOS.owed(origin.message_id)
    if have is not None:
        return have
    if _smd(reply_to):
        return True  # SMD's own test mail: nothing is owed to anyone
    ref = getattr(origin, "internet_message_id", "") or origin.message_id
    told = smd_owed.record(smd_owed.KIND_LOOK_INTO, ref, "; ".join(failed)[:300])
    if not told:
        logger.error(
            "hermes-smd-reply: the look-into line goes to %s but SMD's owed row was not written",
            origin.message_id,
        )
    LOOK_INTOS.note_owed(origin.message_id, told)
    return told


def _thread_allows(thread: str, message_id: str) -> bool:
    """A thread gets the look-into line again only once SMD has answered the
    earlier one (its owed item closed) or SMD was never told of it."""
    earlier = LOOK_INTOS.thread(thread)
    if earlier is None or earlier[0] == message_id or not earlier[2]:
        return True
    page = smd_owed.open_page()
    if page.get("unavailable"):
        return False
    return not any(r.get("request_ref") == earlier[1] for r in page.get("entries") or [])


def _act_turn(
    session_id: str, origin: Any, reply_to: Any, args: dict[str, Any], entries: tuple, say: Any
) -> tuple[dict[str, Any], bool]:
    said = (say,) if isinstance(say, str) else tuple(say or ())
    blocks = [b for b in said if isinstance(b, str) and not b.startswith(turn_says.FORBID)]
    thread = getattr(origin, "conversation_id", "") or origin.message_id
    composed = turn_facts.compose(
        entries, blocks, allow_look_into=_thread_allows(thread, origin.message_id)
    )
    if composed is None:
        return args, False
    if composed.failed:
        told = _owe(origin, reply_to, composed.failed)
        if composed.look_into:
            ref = getattr(origin, "internet_message_id", "") or origin.message_id
            LOOK_INTOS.claim(thread, origin.message_id, ref, told)
    if not composed.text:
        if composed.failed:
            _held(LOOK_INTO_OWED_REASON, origin, _detail=LOOK_INTO_OWED_DETAIL)
            return args, True
        return args, False  # every lane said "none": the model answers the rest
    LOOK_INTOS.note_composed(session_id, origin.message_id, composed)
    return with_body(args, composed.text), False


def _model_turn(
    session_id: str, origin: Any, reply_to: Any, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    if _smd(reply_to):
        return args, False
    words = turn_facts.draft_words(args)
    owed = turn_facts.unfinished_ask(getattr(origin, "reply_text", ""), words)
    name = (
        ""
        if owed
        else turn_facts.listing_contradiction(
            turn_facts.TURN_FACTS.listed(session_id, origin.message_id), words
        )
    )
    if not owed and not name:
        return args, False
    if LOOK_INTOS.holds.note(origin.message_id or _NO_MESSAGE_ID) > MAX_REDRAFTS:
        if owed:
            failed = ("act_owed: the email's conditional instruction was not done",)
            _owe(origin, reply_to, failed)
            composed = turn_facts.Composed(turn_facts.LOOK_INTO, (), True, failed)
        else:
            composed = turn_facts.Composed(f"{name} is filed in the matter.", (name,))
        LOOK_INTOS.note_composed(session_id, origin.message_id, composed)
        return with_body(args, composed.text), False
    if owed:
        _held(ACT_OWED_REASON, origin, _detail=ACT_OWED_DETAIL)
    else:
        _held(STATUS_REASON, origin, _detail=STATUS_DETAIL.format(name=name))
    return args, True


def _decide(
    cfg: Any,
    session_id: str,
    origin: Any,
    bound: Any,
    reply_to: Any,
    args: dict,
    facts: Any,
    say: Any,
) -> tuple[dict[str, Any], bool]:
    if (
        bound is not None
        or facts is not None
        or getattr(cfg, "vertical", "") not in turn_facts.FIRM_VERTICALS
    ):
        return args, False
    try:
        entries = turn_facts.TURN_FACTS.entries(session_id, origin.message_id)
        if turn_facts.has_act(entries):
            return _act_turn(session_id, origin, reply_to, args, entries, say)
        return _model_turn(session_id, origin, reply_to, args)
    except Exception:  # noqa: BLE001 - a fault leaves today's path
        logger.error(
            "hermes-smd-reply: writing the reply from the turn's facts failed", exc_info=True
        )
        return args, False


def code_reply(
    cfg: Any,
    session_id: str,
    origin: Any,
    bound: Any,
    reply_to: Any,
    args: dict[str, Any],
    facts: Any,
    say: Any,
) -> tuple[dict[str, Any] | None, bool]:
    """Step 5c: ``(args, coded)``. ``args`` carry code's words when it wrote
    the reply (``coded``), and are None once the reply was held."""
    message_id = getattr(origin, "message_id", "")
    LOOK_INTOS.forget_composed(session_id, message_id)
    out, decided = _decide(cfg, session_id, origin, bound, reply_to, args, facts, say)
    if decided:
        return None, False
    return out, LOOK_INTOS.composed(session_id, message_id) is not None


def model_words_only(session_id: str, origin: Any, checklist_hold: Any) -> Any:
    """The output checklist's finding, or None on a reply code wrote: the
    checklist judges the model's prose, and that reply has none (the turn's
    required sentences and the matter's own file names; ``job_reply`` sends a
    job's words unjudged for the same reason). A redraft could not change it."""
    if LOOK_INTOS.composed(session_id, getattr(origin, "message_id", "")) is not None:
        return None
    return checklist_hold


def note(kwargs: dict[str, Any]) -> None:
    """Record what an act or read tool did for this turn's email
    (``shared.turn_facts``), from the reply plugin's ``post_tool_call``.
    Never raises."""
    try:
        session_id = kwargs.get("session_id") or ""
        tool = kwargs.get("tool_name") or ""
        if not isinstance(session_id, str) or not session_id:
            return
        if tool not in turn_facts.ACT_TOOLS and tool not in turn_facts.READ_TOOLS:
            return
        origin = inbound.SESSION_INBOUND_ORIGIN.bound_this_turn(session_id)
        if origin is not None and origin.message_id:
            turn_facts.note_call(session_id, origin.message_id, kwargs)
    except Exception:  # noqa: BLE001 - noting never breaks the hook
        logger.warning("hermes-smd-reply: noting the turn's facts failed", exc_info=True)


__all__ = [
    "ACT_OWED_REASON",
    "LOOK_INTOS",
    "LOOK_INTO_OWED_REASON",
    "STATUS_REASON",
    "code_reply",
    "model_words_only",
    "note",
    "with_body",
]
