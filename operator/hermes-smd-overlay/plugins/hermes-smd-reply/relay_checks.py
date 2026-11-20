"""The checks of the relay decision (steps 1 to 7), in the order ``_relay_draft``
takes them.

``_relay_draft`` (this plugin's ``__init__``) is the decision's spine: it calls
these steps one after another, and each one either lets the reply go on or
decides the call (a hold, a duplicate, a job reply, a send). The order of the
steps, every call they make and every record they write are the ones the single
function made before it was split (wave 4, PR B); the decision corpus under
``tests/decisions/`` holds them to it.

HOW A STEP REACHES THE PLUGIN. Every name the plugin defines (``_held``,
``_LIMITER``, ``_CURRENT_CALL``, ``CustomerConfig`` ...) is read off the plugin
module at call time, through ``_plugin``, never imported: tests rebind those
names on the module, and the decision recorder wraps them where they live, so a
copy taken at import would be a different object. The plugin's own and shared
modules (``relay``, ``matter_gate`` ...) are imported, because they are one
object everywhere.

THE RETURN SHAPES. A guard returns ``True`` to go on. A step that hands values
to the next returns them as a tuple, or ``None`` once it has decided the call.
Steps 8 to 10 (send-posture routing, transmit or hold, the catch-all) live in
the plugin itself, beside the transports they call; what step 9 does once the
reply is out (its record, and a fault after the send) is in ``relay_sent``.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType
from typing import Any

from shared import (
    advertising_gate,
    champion,
    champion_audience,
    inbound,
    matter_file_facts,
    matter_file_reply,
    matter_gate,
    provenance,
    report_render,
    result_unwrap,
    spec_gate,
    turn_says,
)

from . import binding, held_notice, job_reply, notice, relay, working_drafts

_PACKAGE = __name__.rpartition(".")[0]
#: The plugin module this copy of the steps serves. The plugin drops any earlier
#: copy of this module before importing it, so a plugin loaded twice (the tests
#: load it per test) gets steps bound to itself, never to the previous load.
_plugin: ModuleType = sys.modules[_PACKAGE]
logger = logging.getLogger(_PACKAGE)


# --------------------------------------------------------------------------
# 1. The call guard
# --------------------------------------------------------------------------


def call_guard(kwargs: dict[str, Any]) -> bool:
    """Step 1: this call is a draft tool's, its ids are bound, and the draft exists."""
    if (kwargs.get("tool_name") or "") not in _plugin._CREATE_DRAFT_TOOLS:
        return False

    # Bind this dispatch's id so any hold below is recorded against the call
    # whose result the agent is about to read (ss-console#2367).
    tool_call_id = kwargs.get("tool_call_id")
    _plugin._CURRENT_CALL.tool_call_id = tool_call_id if isinstance(tool_call_id, str) else ""
    # ss-console#2497: bind the session at the SAME point, before any hold can
    # fire. The early holds — roster, recipient-lock, empty body — return long
    # before the send path resolves anything, and they are the rows an auditor
    # asking "why did this person get no answer" reads first.
    raw_session_id = kwargs.get("session_id")
    _plugin._CURRENT_CALL.session_id = raw_session_id if isinstance(raw_session_id, str) else ""
    # The device-redirect target, resolved after the roster check below.
    # Empty until then, so an early hold is recorded against the sender.
    _plugin._CURRENT_CALL.reply_to = ""
    # Which email this call answers, and whether its bytes reached the
    # transport. The catch-all below reads both: an unexpected fault after
    # the origin resolved and before a transmit is a reply nobody got, and
    # the agent must be told so (ss-console#2367), never left reading "ok".
    _plugin._CURRENT_CALL.origin = None
    _plugin._CURRENT_CALL.transmitted = False

    # (0) The draft must actually exist. ``post_tool_call`` fires after every
    # dispatch, including one that returned an error, and the relay used to
    # act on the tool NAME alone — so a create_draft that failed still put a
    # real email in the sender's inbox, and the agent's retry sent the same
    # answer again (leg-1 turn 2, vfy_01KYTG0B88R3B5K0D7FKPACRZT). Reply
    # transmission is now strictly downstream of a draft the tool confirmed.
    if relay.draft_call_failed(
        result=kwargs.get("result"),
        status=kwargs.get("status"),
        error_type=kwargs.get("error_type"),
    ):
        logger.info(
            "hermes-smd-reply: draft tool call reported failure; not relaying "
            "(the agent's retry, if any, relays instead)"
        )
        return False
    return True


# --------------------------------------------------------------------------
# 2. Config and origin
# --------------------------------------------------------------------------


def config_and_origin(kwargs: dict[str, Any]) -> tuple[Any, ...] | None:
    """Step 2: the live config and the email this call answers.

    ``(cfg, vertical, session_id, args, origin, bound)``, or None when the
    config cannot be read or no email is there to answer."""
    # Config is read LIVE (ADR 0044): the roster can be authored without a
    # restart, so the relay re-reads it here. Fail closed if customer.yaml is
    # unreadable — a relay that cannot confirm the roster never sends.
    try:
        cfg = _plugin.CustomerConfig.from_volume(str(_plugin._YAML_PATH))
    except (_plugin.CustomerConfigError, OSError) as exc:
        logger.warning(
            "hermes-smd-reply: customer.yaml live-read failed (%s); not replying this call",
            exc,
        )
        return None
    vertical = cfg.vertical or None

    session_id = kwargs.get("session_id") or ""
    args = kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {}

    # THIS turn's own email only (ss-console participant fence, 2026-10-07):
    # the origin the inbound plugin bound from this turn's trusted prompt
    # prefix. Not the sticky session origin, and no longer the address-keyed
    # recovery (``find_for_recipient`` is deleted): a draft addressed to
    # someone who once wrote in found their most recent email and answered
    # it, which is a reply to a request nobody made in this turn.
    origin = inbound.SESSION_INBOUND_ORIGIN.bound_this_turn(session_id)
    # The verified reply binding (binding.py): a turn no email opened, bound
    # through the broker to ONE earlier email.
    bound = binding.SESSION_BINDINGS.get(session_id) if origin is None else None
    if bound is not None:
        origin = bound.origin()
    if origin is None:
        # Fail closed: no email opened this turn and no binding was taken,
        # so there is nobody to reply to. A create_draft that did NOT
        # originate from this turn's inbound email never relays.
        return None
    _plugin._CURRENT_CALL.origin = origin
    return cfg, vertical, session_id, args, origin, bound


# --------------------------------------------------------------------------
# 3. Duplicate and code-sent kinds
# --------------------------------------------------------------------------


def duplicate_and_code_sent(
    kwargs: dict[str, Any], cfg: Any, session_id: str, origin: Any, bound: Any
) -> tuple[str, str] | None:
    """Step 3: ``(reply_key, draft_id)``, or None when this email already has its
    reply or a drafting job's reply is the seat's to send."""
    # (0b) One inbound message, at most one reply out. The hook fires per
    # tool CALL, so an agent that drafts twice in a turn arrives here twice
    # for the SAME email. The (0) outcome check catches the retry-after-error
    # shape; this catches every other way a second draft could reach the
    # transport, including shapes no result envelope would reveal. Committed
    # only on a real send or a durable enqueue, so a gated reply stays
    # deliverable by the release path.
    #
    # A BOUND reply is keyed on its binding in THIS session, never on the
    # email's id (2026-10-12, A&P drafting job 01M4RXQV...): the request
    # email's earlier failure reply had committed its id, so the job's
    # delivery, bound correctly hours later, was held as a duplicate and
    # dropped until a gateway restart cleared the guard. The broker's
    # durable claim is the once-only rule across turns for a bound reply
    # (one per job, attempt and outcome); this guard only collapses two
    # drafts in one bound turn.
    reply_key = _plugin._reply_guard_key(session_id, bound, origin)
    # The Graph draft this call made: the agent's working copy of the reply
    # (working_drafts.py). Only a Graph seat's draft is ever discarded.
    draft_id = (
        working_drafts.draft_id_of(kwargs.get("result"))
        if _plugin._email_adapter(cfg) == _plugin._ADAPTER_MSGRAPH
        else ""
    )
    if _plugin._REPLIED.committed(reply_key):
        _plugin._held("duplicate_reply", origin)
        # The reply already went out (or is queued): this draft is a copy of
        # an answer the person has, not something left for review.
        _plugin._discard_drafts([draft_id])
        return None
    if bound is not None and bound.as_request().get("kind") in job_reply.CODE_SENT_KINDS:
        # (0c) A drafting job's reply is the broker's words, never this
        # draft's (job_reply, A&P 2026-11-05): the seat sends them now.
        _plugin._WORKING_DRAFTS.track(session_id, reply_key, draft_id)
        _plugin._deliver_job_reply(session_id)
        return None
    return reply_key, draft_id


# --------------------------------------------------------------------------
# 4. Roster, recipient and redirect
# --------------------------------------------------------------------------


def roster_and_recipient(
    cfg: Any,
    session_id: str,
    args: dict[str, Any],
    origin: Any,
    bound: Any,
    reply_key: str,
    draft_id: str,
) -> tuple[Any, bool, Any] | None:
    """Step 4: ``(reply_to, redirected, shared_people)``, or None once held."""
    if not _roster(cfg, session_id, origin, reply_key, draft_id):
        return None
    reply_to, redirected = _redirect(cfg, origin)

    # (b0) A SHARED reply (ADR 0091): on a space-mode seat, a turn the space
    # resolver puts in ``shared`` from a joint email thread is answered to
    # every authored person, not just the one who wrote last. The set is the
    # seat's ``people[].email``, never anything the draft said, and only an
    # AgentMail inbound reply can carry it (the broker copies the other
    # people from the thread itself and re-checks every condition).
    shared_people = _plugin._shared_reply_people(cfg, session_id, origin, bound, redirected)
    if shared_people == _plugin._SHARED_UNSUPPORTED:
        _plugin._held("shared_reply_unsupported", origin)
        return None
    if not _recipient_lock(cfg, args, origin, bound, reply_to, redirected, shared_people):
        return None
    return reply_to, redirected, shared_people


def _roster(cfg: Any, session_id: str, origin: Any, reply_key: str, draft_id: str) -> bool:
    """The sender is on the roster, and under the redraft cap."""
    # (a) Roster authorization — the Operator replies autonomously only to a
    # colleague on the organization roster (ADR 0055). A verified inbound
    # sender NOT on the roster gets a drafted (not sent) reply: reaching
    # outside the roster needs explicit authorization. Held, not an error.
    # That draft is the one left for staff, so it is never tracked.
    if not cfg.sender_on_roster(origin.sender_address):
        _plugin._held("sender_not_on_roster", origin)
        return False
    # From here a held draft is one the agent redrafts; it is discarded with
    # the others once this reply is actually transmitted, and kept otherwise.
    _plugin._WORKING_DRAFTS.track(session_id, reply_key, draft_id)

    # (a1) The redraft cap (held_notice; send_policy.reply.max_redrafts).
    # Only on a seat that authors held_notice: past the cap, the draft is
    # refused without relaying and the end of the turn tells the sender.
    if held_notice.over_cap(
        held_notice.policy(cfg.raw), _plugin._HOLD_COUNTS.count(origin.message_id)
    ):
        _plugin._held(
            held_notice.REDRAFT_LIMIT_REASON,
            origin,
            _detail=held_notice.REDRAFT_LIMIT_DETAIL,
        )
        return False
    return True


def _redirect(cfg: Any, origin: Any) -> tuple[Any, bool]:
    """``(reply_to, redirected)``: who receives the reply."""
    # (a2) Device redirect. A sender authored under ``scope.device_senders``
    # is a machine (an office scanner emailing its scans), and a reply sent
    # back to it reaches nobody. The reply goes to the person the config
    # names instead, and EVERYTHING below keys on that person: the lock,
    # the content floor's classification, the matter gate, the rate limit,
    # the held-reply row and the audit. The roster check above stays on the
    # device, because the device is who wrote in. The target is authored
    # config (validator: on scope.admins), never anything the draft said.
    # Unmapped, ``reply_to`` IS ``origin.sender_address``, byte for byte, so
    # every ordinary reply keys its limiter, queue and rows exactly as before.
    device_target = cfg.device_reply_target(origin.sender_address)
    redirected = bool(device_target) and (device_target != origin.sender_address.strip().lower())
    reply_to = device_target if redirected and device_target else origin.sender_address
    _plugin._CURRENT_CALL.reply_to = reply_to if redirected else ""
    if redirected:
        logger.info(
            "hermes-smd-reply: sender is an authored device; the reply is "
            "redirected to its authored person (message=%s)",
            origin.message_id,
        )
    return reply_to, redirected


def _recipient_lock(
    cfg: Any,
    args: dict[str, Any],
    origin: Any,
    bound: Any,
    reply_to: Any,
    redirected: bool,
    shared_people: Any,
) -> bool:
    """The draft is addressed to whom the reply may go, on a transport that can."""
    # (b) Recipient-lock — the reply can go ONLY to the address that emailed
    # in (or, for a device, to the person answering for it; or, for a shared
    # reply, to the seat's people). An injected extra/substituted recipient
    # fails the lock here.
    locked = (
        relay.shared_recipients_locked(args, origin.sender_address, shared_people)
        if shared_people
        else relay.recipient_locked(args, origin.sender_address, reply_to)
    )
    if not locked:
        _plugin._held("recipient_mismatch", origin, draft_to=sorted(relay.draft_recipients(args)))
        return False
    if not origin.inbox_id:
        # No inbox to thread the reply into — fail closed.
        _plugin._held("no_inbox_id", origin)
        return False
    if bound is not None and (
        redirected or _plugin._email_adapter(cfg) != _plugin._ADAPTER_MSGRAPH
    ):
        # The binding is a Graph-broker verb and answers the email's own
        # sender only; a device redirect or another transport has no bound
        # path, so the reply is held rather than sent some other way.
        _plugin._held("bound_reply_unsupported", origin)
        return False
    if redirected and _plugin._email_adapter(cfg) != _plugin._ADAPTER_MSGRAPH:
        # Only the Graph broker can aim a reply at someone other than the
        # source message's sender (and re-checks the pairing itself). On any
        # other transport the reply would go back to the device, which is
        # the silence this redirect exists to end, so it is held instead.
        _plugin._held("device_redirect_unsupported", origin)
        return False
    return True


# --------------------------------------------------------------------------
# 5. Body and facts checks
# --------------------------------------------------------------------------


def body_and_facts(
    cfg: Any,
    vertical: Any,
    session_id: str,
    args: dict[str, Any],
    origin: Any,
    bound: Any,
    reply_to: Any,
) -> tuple[Any, ...] | None:
    """Step 5: the body that would go and the content floor over it.

    ``(facts, facts_hold, model_words, text, recipient_class, internal)``, with
    ``text`` the ``(scan_text, send_text, send_html)`` of ``relay.draft_body``, or
    None once the floor held the reply."""
    facts, facts_hold, model_words, args = _facts(session_id, origin, args)

    scan_text, send_text, send_html = relay.draft_body(args)
    # 5a. A tool wrote a fixed sentence this turn: the body must carry it, as a block.
    say = _TURN_SAYS.get(session_id, origin.message_id)
    if say is not None and say_held(say, origin, send_text, send_html, reply_to):
        return None
    if mechanics_held(origin, reply_to, send_text, send_html):
        return None
    if facts is not None and not facts_hold:
        # The invariant, checked on the body that would go: no marker, and
        # the facts block present whenever there is one.
        facts_hold = matter_file_facts.missing(facts, send_text, send_html)

    floor = _content_floor(cfg, vertical, session_id, origin, bound, reply_to, scan_text)
    if floor is None:
        return None
    if _champion_walled(cfg, session_id, origin, reply_to, scan_text):
        return None
    recipient_class, internal = floor
    text = (scan_text, send_text, send_html)
    return facts, facts_hold, model_words, text, recipient_class, internal


# --------------------------------------------------------------------------
# 5a. The turn's fixed sentence (2026-11-19)
# --------------------------------------------------------------------------
#
# A tool that answers with ``say`` has written a required block: the records
# lane's acknowledgment, the drafting lane's acknowledgment and refusals, the
# help guide's hand-off. Until this check the skill text asked the model to
# relay it "exactly", and nothing compared: a drafting acknowledgment could
# leave with "shortly" added, or with the job's mechanics. Now every sentence the
# turn noted must appear in the reply exactly, each as its own paragraph, once
# and in order; other paragraphs may answer the rest of the email, and on such a
# turn may not describe how the work is done (``shared.turn_says``). Otherwise
# the reply is held with the sentence quoted back, the broker's fixed-wording
# retry, so the redraft goes in one step. ``say: "none"`` alone means this email
# gets no reply.
#
# Only the tools listed here: other tools' ``say`` (training, calendar, the
# library-setup lane) is a sentence for the reply to include, not the reply.
# The send path appends nothing to a body (no signature: ``relay.draft_body``
# and the Graph ``/reply`` body are the model's words exactly), so the body is
# compared as drafted.

#: The tools whose ``say`` is a required block of the reply, by their own contract.
SAY_TOOLS = frozenset(
    {
        "records_job_submit",
        "drafting_job_submit",
        "drafting_job_add_to_request",
        "operator_help_note",
        # Only on a rules_by_hand seat does its result carry a say.
        "establish_propose",
    }
)
#: The hold reason a reply with other words is held under, and its notice.
SAY_HOLD_REASON = "fixed_wording"
#: A reply naming the Operator's machinery (tier 1), counted apart from it.
MECHANICS_HOLD_REASON = "mechanics_named"
_SAY_MAX = 1024


#: The broker's comparison and its HTML reading, kept where the register is.
_norm = turn_says.norm
_from_html = turn_says.from_html


#: The register (``shared.turn_says``), one per load of this module, which is
#: one per load of the plugin.
TurnSays = turn_says.TurnSays
_TURN_SAYS = TurnSays()
#: How many times each email's reply was held for other words (step 5a's own
#: redraft cap, on EVERY seat: ``held_notice``'s cap is only on a seat that
#: authors it, and a model that keeps adding a greeting loops until the turn
#: runs out). Past ``SAY_MAX_REDRAFTS`` redrafts the model is told to stop; the
#: hold stays ``fixed_wording``, which pages SMD as ``reply_held:fixed_wording``.
_SAY_HOLDS = held_notice.HoldCounter()
SAY_MAX_REDRAFTS = 2
SAY_STOP_DETAIL = notice.FIXED_WORDING_STOP


def note_turn(kwargs: dict[str, Any]) -> None:
    """A tool call ended: note it as a send attempt (``_note_turn_send``) and
    keep its whole-reply ``say``. The reply plugin's ``post_tool_call``, which
    fires for the say tool before the model drafts. Never raises."""
    _plugin._note_turn_send(kwargs.get("tool_name"), kwargs.get("session_id"))
    note_say(kwargs)


def note_say(kwargs: dict[str, Any]) -> None:
    """Keep a whole-reply tool's ``say`` for this turn's email. Never raises."""
    try:
        session_id = kwargs.get("session_id") or ""
        if (kwargs.get("tool_name") or "") not in SAY_TOOLS or not isinstance(session_id, str):
            return
        parsed = result_unwrap.parse_tool_result(kwargs.get("result"))
        say = parsed.get("say") if isinstance(parsed, dict) else None
        if not isinstance(say, str) or not say.strip() or len(say) > _SAY_MAX:
            return
        origin = inbound.SESSION_INBOUND_ORIGIN.bound_this_turn(session_id) if session_id else None
        if origin is None or not origin.message_id:
            return
        _TURN_SAYS.note(session_id, origin.message_id, say.strip())
        forbid = parsed.get("forbid")
        if isinstance(forbid, dict):
            entry = turn_says.forbid_entry(
                str(forbid.get("matter") or ""), list(forbid.get("words") or [])
            )
            _TURN_SAYS.note(session_id, origin.message_id, entry)
    except Exception:  # noqa: BLE001 - noting never breaks the hook
        logger.warning("hermes-smd-reply: noting the turn's fixed sentence failed", exc_info=True)


def say_held(say: Any, origin: Any, send_text: str, send_html: str, reply_to: Any = None) -> bool:
    """Step 5a: hold a body that breaks the block rule (``shared.turn_says``).

    ``say`` is the turn's required blocks in order (a lone string is one block).
    False to go on. Otherwise the reply is held (``_held``), with the missing
    or altered sentence, or the mechanics phrase, quoted for the redraft."""
    blocks = (say,) if isinstance(say, str) else tuple(say)
    # Every part the send carries must hold the blocks (the broker's rule,
    # ``workspace_broker/fixed_wording``): a draft with the sentence in its text
    # and other words in its HTML would send the other words.
    problem = turn_says.send_problem(blocks, send_text, send_html, reply_to)
    if not problem:
        return False
    # The first draft and SAY_MAX_REDRAFTS redrafts are told the exact text;
    # every draft after them is told to stop.
    capped = _SAY_HOLDS.note(origin.message_id) > SAY_MAX_REDRAFTS
    _plugin._held(
        SAY_HOLD_REASON,
        origin,
        rule="turn_say_cap" if capped else "turn_say",
        _detail=SAY_STOP_DETAIL if capped else turn_says.refusal(problem),
    )
    return True


def mechanics_held(origin: Any, reply_to: Any, send_text: str, send_html: str) -> bool:
    """Tier 1 (review 2026-11-19): a reply to a firm address that names the
    Operator's machinery is held on every turn, with the redraft cap of 5a."""
    hold = turn_says.mechanics_hold(
        reply_to,
        send_text,
        send_html,
        lambda: _SAY_HOLDS.note(origin.message_id),
        notice.MECHANICS_STOP,
    )
    if hold is not None:
        _plugin._held(MECHANICS_HOLD_REASON, origin, rule=hold[0], _detail=hold[1])
    return hold is not None


def turn_say(session_id: str, origin: Any, send_text: str, send_html: str) -> bool:
    """Step 5a as one predicate (the tests' entry): True to go on."""
    say = _TURN_SAYS.get(session_id, origin.message_id)
    return say is None or not say_held(say, origin, send_text, send_html)


def _champion_walled(cfg: Any, session_id: str, origin: Any, reply_to: Any, scan_text: str) -> bool:
    """(c0) The champion wall (``shared.champion``), keyed on who RECEIVES the
    reply: to a client champion, a reply that carries a rep marker or cites a
    document or heading outside the champion allowlist is held, and the held-
    notice path sends them the fixed sentence instead. On a seat whose
    product-guide entry authors ``audience: champion``, every reply's library
    citations are checked (``shared.champion_audience``). Anyone else's reply
    is untouched: ``walls`` answers ``""``."""
    # ``reply_to`` is who receives it: the sender, or a device's authored person.
    mode = champion.walls(reply_to, cfg.raw)
    if not mode:
        return False
    why = champion.reply_wall(
        reply_to, scan_text, origin.message_id, mode=mode, session_id=session_id
    )
    if not why:
        return False
    _plugin._held(champion.HOLD_REASON, origin, rule=why, _detail=_WALL_DETAIL[mode])
    return True


#: What the model is told when each kind of wall holds a reply.
_WALL_DETAIL = {
    champion.MODE_CHAMPION: champion.MODEL_DETAIL,
    champion.MODE_AUDIENCE: champion_audience.MODEL_DETAIL,
}


def _facts(session_id: str, origin: Any, args: dict[str, Any]) -> tuple[Any, str, Any, Any]:
    """``(facts, facts_hold, model_words, args)``: a records reply's facts, written in."""
    # (b1) The facts of a records reply (ADR 0102, shared.matter_file_facts):
    # what was attached, found and not found, written by code from the page
    # finder and the staged files, put into the body BEFORE the scan text and
    # html are built, so every gate below and the review digest cover it.
    facts = _plugin._file_facts(session_id, origin)
    facts_hold = ""
    model_words = matter_file_facts.model_words(args)
    if facts is not None:
        facts_hold = facts.hold
        # On a hold no block is written, but the marker still comes out: left
        # in, its capitals bounce the reply to a redraft that cannot fix it.
        args = matter_file_facts.insert(args, matter_file_facts.EMPTY if facts_hold else facts)
        if not facts_hold:
            extra = matter_file_facts.unstated(facts, model_words)
            if extra:
                facts_hold = (
                    f"the reply's own words state pages or counts the facts do not: {extra}"
                )
    return facts, facts_hold, model_words, args


def _content_floor(
    cfg: Any,
    vertical: Any,
    session_id: str,
    origin: Any,
    bound: Any,
    reply_to: Any,
    scan_text: str,
) -> tuple[Any, bool] | None:
    """``(recipient_class, internal)``, or None once the outbound floors held it."""
    # (c) Re-apply the outbound floors to the draft body. ADR 0072 carve-out
    # (ss #1932): the send path deliberately does NOT content-floor a send
    # whose recipients all classify INTERNAL (enforce.py evaluate_tool_call —
    # firm-internal coordination legitimately names deadlines, signatures,
    # attorneys; flooring it held ack confirmations in drafts). Mirror it
    # with the SAME classifier and the SAME rosters the send path resolves.
    #
    # INTERNAL is an AUTHORED fact as of ss#2263, not one inferred from the
    # reply list. Before that split this line exempted every relayed reply by
    # construction — including a reply to the firm's own client, whom the firm
    # had put on `inbound_allow_from` for the sole purpose of enabling replies.
    # The floor is the larger of the two exposures the split moves (the matter
    # gate is the other); they move together, in one release, because they
    # read this one `recipient_class`.
    # ``from_tainted`` stays False deliberately: taint guards MODEL-CHOSEN
    # recipients, but this recipient is structurally pinned to the
    # Svix-verified inbound sender by the recipient-lock above — an injected
    # body cannot redirect the reply. The fabrication gate still applies to
    # every reply. Classification faults fail toward floored, never open.
    try:
        # ``reply_to``: the person who actually receives this reply, which is
        # the sender unless the sender is an authored device.
        recipient_class = _plugin.classify_recipients_typed(
            [reply_to], cfg.inbound_roster, cfg.outbound_roster
        )
    except Exception:  # noqa: BLE001 — unclassifiable recipient keeps the floor
        logger.exception(
            "hermes-smd-reply: recipient classification raised; keeping the content floor"
        )
        recipient_class = None
    internal = recipient_class is _plugin.RecipientClass.INTERNAL
    if bound is not None and internal and _plugin._session_tainted(session_id):
        # A bound turn that ingested outside content (a wake carrying a
        # document's words, a cron that read the mailbox) gets the content
        # floor even for a colleague: the recipient is pinned, but the
        # body may carry what the turn read, and no inbound fence wrapped it.
        internal = False
    allowed_captions, allowed_money = _provenance(session_id)
    gate = relay.gate_body(
        scan_text,
        vertical=vertical,
        cohort=_plugin._CUSTOMER_SLUG,
        internal_recipient=internal,
        allowed_case_names=allowed_captions,
        allowed_money=allowed_money,
    )
    if not gate.allowed:
        _plugin._held(gate.reason, origin, categories=list(gate.categories))
        return None
    return recipient_class, internal


def _provenance(session_id: str) -> tuple[Any, Any]:
    """``(allowed_captions, allowed_money)``: what this session read off its records."""
    # Provenance exemption — the SAME one the drafting path applies
    # (hermes-smd-trust/outbound.py). Case captions the agent READ from a
    # system of record this session are quotable; without them the two
    # channels disagreed, and the trust gate's approval of a draft naming
    # matters read from Smokeball was overturned here as
    # fabrication:tier2_citation, leaving the sender with silence. A fault
    # degrades to the empty register — no exemption, today's behaviour.
    #
    # ss-console#2367 adds the MONEY half of the same register. ss#2258 gave
    # ``specific-dollar-amount`` a provenance-scoped exemption on the
    # drafting path only, so this path still refused what the skill permits:
    # a demand letter was filed on 2026-PI-104 and the reply naming it was
    # held on the Kaiser lien and the MedFin payoff, both read this session
    # off the firm's own records and both cited to their source in the
    # sentence that carried them. The firm asked for a letter and got
    # silence. One register, read once, feeding both exemptions.
    try:
        _register = provenance.register_for(provenance.resolve_session(session_id))
        allowed_captions = _register.captions()
        allowed_money = _register.money()
    except Exception:  # noqa: BLE001 — an unreadable register grants nothing
        logger.debug(
            "hermes-smd-reply: provenance register unavailable; no caption exemption",
            exc_info=True,
        )
        allowed_captions = frozenset()
        allowed_money = frozenset()
    return allowed_captions, allowed_money


# --------------------------------------------------------------------------
# 6. The spec checklist
# --------------------------------------------------------------------------


def spec_checklist(
    kwargs: dict[str, Any],
    session_id: str,
    origin: Any,
    bound: Any,
    facts: Any,
    facts_hold: str,
    model_words: Any,
    text: tuple[str, str, str],
    internal: bool,
) -> str | None:
    """Step 6: the output checklist, the advertising gate and a body to send.

    ``facts_hold`` as the checklist leaves it, or None once the reply is held."""
    _scan_text, send_text, send_html = text
    checked = _checklist(
        kwargs,
        session_id,
        origin,
        bound,
        facts,
        facts_hold,
        model_words,
        send_text,
        send_html,
        internal,
    )
    if checked is None:
        return None

    # (c1b) The California advertising gate (ADR 0098), only on a seat whose
    # role turns it on (a law-firm marketing seat). A reply to the firm can
    # carry drafts of public content; HELD on a BLOCK like the checklist,
    # with each finding's rule and fragment in the notice. FLAGs write the
    # row and the reply goes. A check that cannot run holds.
    advertising_hold = _plugin._advertising_violations(
        send_text or report_render.html_text_content(send_html),
        internal=internal,
        tool_name=str(kwargs.get("tool_name") or ""),
        session_id=session_id,
    )
    if advertising_hold is not None:
        _plugin._held(
            advertising_gate.REASON,
            origin,
            rules=advertising_hold[0],
            _detail=advertising_hold[1],
        )
        return None

    if not (send_text or send_html):
        # Subject-only draft — nothing to relay (the gate scans the subject,
        # but a reply transmits only the body). Fail closed.
        _plugin._held("empty_body", origin)
        return None
    return checked


def _checklist(
    kwargs: dict[str, Any],
    session_id: str,
    origin: Any,
    bound: Any,
    facts: Any,
    facts_hold: str,
    model_words: Any,
    send_text: str,
    send_html: str,
    internal: bool,
) -> str | None:
    """``facts_hold`` after the output checklist, or None once it held the reply."""
    # (c1) The output checklist (ss-console Option B, 2026-09-29): would a
    # good paralegal have sent this reply as is? A colleague's reply meets
    # the staff set; a reply to anyone outside the firm meets ids and ISO/UTC
    # times only, so a client's own words quoted back cannot hold it. HELD,
    # not refused: the draft stands, and the notice tells the model exactly
    # what to correct.
    #
    # An SMD note's words were approved as written and the binding refuses any
    # other wording (fixed_wording), so it meets the outside set, ids and times
    # only: the style rules judge a model's prose (2026-11-17, an approved note
    # naming the firm's own capitalised folder names was held at the send). Read
    # from the binding's request without a call, so the recorded decision keeps
    # its shape.
    if bound is not None and ("kind", "note_job") in bound.request:
        internal = False
    checklist_hold = _plugin._checklist_violations(
        # A file name the facts print is the matter's own, not our prose: the
        # checklist never judges it (2026-11-02 replay: credentials in names).
        matter_file_facts.without_names(
            facts, send_text or report_render.html_text_content(send_html)
        ),
        internal=internal,
        tool_name=str(kwargs.get("tool_name") or ""),
        session_id=session_id,
        job_reply=_plugin._is_job_reply(bound),
    )
    if (
        checklist_hold is not None
        and facts is not None
        and (
            facts_hold
            or (
                facts.text
                and _plugin._checklist_violations(
                    matter_file_facts.without_names(facts, model_words),
                    internal=internal,
                    tool_name=str(kwargs.get("tool_name") or ""),
                    session_id=session_id,
                    job_reply=_plugin._is_job_reply(bound),
                )
                is None
            )
        )
    ):
        # The facts block is code's: when only it trips the checklist, or the
        # reply already waits for SMD over its facts, a redraft cannot fix
        # it, so the reply waits for SMD instead of bouncing to the model.
        facts_hold = facts_hold or f"the facts block tripped the checklist ({checklist_hold[0]})"
        checklist_hold = None
    if checklist_hold is not None:
        _plugin._held(
            spec_gate.REASON_OUTPUT_CHECKLIST,
            origin,
            rules=checklist_hold[0],
            _detail=checklist_hold[1],
        )
        return None
    return facts_hold


# --------------------------------------------------------------------------
# 7. The matter
# --------------------------------------------------------------------------


def matter(
    cfg: Any,
    session_id: str,
    origin: Any,
    bound: Any,
    text: tuple[str, str, str],
    route: tuple[Any, bool, Any],
    recipient_class: Any,
) -> tuple[Any, Any] | None:
    """Step 7: the matter gate and the matter's files.

    ``(cited_matter_ref, prepared)``, or None once the reply is held."""
    scan_text = text[0]
    reply_to, redirected, _shared_people = route
    held, cited_matter_ref = _matter_gate(session_id, origin, scan_text, reply_to, recipient_class)
    if held:
        return None

    # (c3) Matter files (ADR 0102). Staged files ride on this reply only
    # after every floor above, and only when their own fences hold here:
    # the same email, firm staff, one matter. A refusal clears the staging
    # and holds the reply, so the model redrafts without claiming a file.
    try:
        prepared = _plugin._prepare_matter_files(
            cfg,
            session_id,
            origin,
            reply_to=reply_to,
            redirected=redirected,
            bound=bound is not None,
            scan_text=scan_text,
        )
    except matter_file_reply.FileRefused as exc:
        matter_file_reply.discard(session_id)
        _plugin._held(
            matter_file_reply.REFUSAL_REASON,
            origin,
            _detail=str(exc),
            _matter_ref=cited_matter_ref,
        )
        return None
    return cited_matter_ref, prepared


def _matter_gate(
    session_id: str, origin: Any, scan_text: str, reply_to: Any, recipient_class: Any
) -> tuple[bool, Any]:
    """``(held, cited_matter_ref)``: is this recipient a party to the matter?"""
    # (c2) Matter identity — is this recipient a party to the matter this
    # reply is about? (ss#2167)
    #
    # WHY IT HAS TO BE HERE AND NOT IN THE TRUST GATE. The matter check lives
    # in enforce.evaluate_tool_call, guarded by `is_send`, which is true only
    # for EXTERNAL_SEND* action classes. The tool this lane calls is
    # ``create_draft`` — INTERNAL_WRITE (shared/action_classes.py) — and
    # ``_reclassify_send`` returns a non-EXTERNAL_SEND base class unchanged.
    # So on the reply lane the matter gate never ran at all, while the relay
    # sent the same draft out as a real email a few steps later.
    # That was 86 of the pilot's replies, and ~74% of all sends, with no
    # matter-identity check of any kind (vfy_01KZRRW066Y70TFEYKGQX6ME76).
    # It was recorded on the issue as the gate being *blind* here; it was
    # absent, which is why widening membership capture alone would have
    # changed nothing and reported the lane closed.
    #
    # The recipient is structurally pinned to the verified inbound sender by
    # the recipient-lock at (b), so `origin.sender_address` IS the recipient
    # set — no model-chosen address participates.
    # The exemption used to be unable to say `recipient_class is INTERNAL` on
    # this lane, and getting that wrong would have shipped a control that can
    # never fire. `sender_on_roster` above IS `scope.inbound_allow_from`, and
    # `_classify_one_typed` USED TO return INTERNAL on an inbound-roster match
    # BEFORE consulting the typed roster ("a rostered internal recipient
    # outranks a typed class"). So every relayed reply classified INTERNAL by
    # construction, and an INTERNAL exemption would have skipped 100% of this
    # lane.
    #
    # That conflation is fixed at the source as of ss#2263, and this call site
    # got simpler because of it. `inbound_allow_from` answers "may I reply to
    # you"; `scope.outbound_roster` answers "what are you to this firm", and
    # `_classify_one_typed` now reads the typed roster FIRST. So the single
    # `recipient_class` computed for the content floor above is already the
    # right input here: a reply-authorized address the firm typed as a client
    # classifies CLIENT — floored AND gated — instead of INTERNAL.
    #
    # This block used to reclassify against an EMPTY internal roster to route
    # around the old precedence. That workaround is deleted rather than kept:
    # it could only ever have fired for a config the validators rejected
    # ("a recipient cannot be both internal and a typed outbound class"), so
    # it protected nothing, and leaving two classifications on one path is how
    # the floor and the gate drift apart again. One classifier, one verdict.
    #
    # A classification FAULT above leaves `recipient_class` None, which is in
    # neither exempt class, so the gate RUNS. A classification we could not
    # perform is not evidence that this recipient is firm staff.
    matter_verdict = matter_gate.evaluate(
        session_id=session_id,
        body=scan_text,
        # The person receiving it: a device redirect is gated on the person,
        # since the device is never a party to anything.
        recipients={reply_to},
        # Firm staff and records vendors are not expected to be parties
        # (ADR 0072, the same carve-out enforce.py applies).
        recipient_is_exempt=recipient_class
        in (_plugin.RecipientClass.INTERNAL, _plugin.RecipientClass.VENDOR),
    )
    # The verdict's own cited-and-resolved tokens are better evidence of what
    # this reply is ABOUT than the session's read set, so from here down the
    # rows carry the matter the body actually names (ss-console#2497). Still
    # ``None`` when the body cites two matters — see ``matter_ref_for``.
    cited_matter_ref = matter_gate.matter_ref_for(session_id, matter_verdict.matters)
    if matter_verdict.should_withhold and matter_gate.mode() == "block":
        _plugin._held(
            "matter_mismatch",
            origin,
            matters=list(matter_verdict.matters),
            detail=matter_verdict.reason,
            _matter_ref=cited_matter_ref,
        )
        return True, cited_matter_ref
    if matter_verdict.status == "unresolved" and matter_verdict.matters:
        # Recorded, NOT held (Captain call, 2026-08-11). A reply citing a
        # matter whose party list this turn never read is the common case
        # today — get_matter fires on 8 of 86 reply turns — so holding it
        # would withhold correct client replies at a rate nobody has
        # measured, and a control that blocks correct work gets removed
        # rather than fixed. This row is that measurement.
        _plugin._emit_reply_event(
            action_type="MATTER_UNRESOLVED",
            metadata={
                "recipient": reply_to,
                "message_id": origin.message_id,
                **_plugin._reply_recipient(origin)[1],
                "matters": list(matter_verdict.matters),
                "detail": matter_verdict.reason,
            },
            session_id=session_id,
            matter_ref=cited_matter_ref,
        )
    return False, cited_matter_ref
