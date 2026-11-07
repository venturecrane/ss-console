"""The four messages that close the rule-request loop (ss-console#2546).

WHAT WAS BROKEN. ss-console#2529 gave a firm a way to teach the Operator by
talking to it: state a rule, hear it read back, say yes, and it commits. That
works when the person speaking is an Operator admin. When they are not, the
Operator recorded the rule, said an admin could apply it by replying "apply
that", and then nothing happened. No admin was told there was anything to
answer. An admin's "no" did nothing at all. A rule nobody answered was deleted
by the broker's sweep, so the person who asked could not even be told it had
lapsed. Three silences, and every one of them looked from the outside like the
Operator had simply ignored somebody.

WHAT THIS MODULE IS. The four notifications that end those silences, composed
here as FIXED TEMPLATES and sent deterministically by the overlay rather than by
the model:

1. a paralegal's rule reaches the administrators the firm named for request
   traffic, with the tag and the two answers;
2. the person who asked is told when it goes into effect;
3. they are told when an administrator declines it;
4. they are told when nobody answered and it lapsed.

WHY TEMPLATES AND NOT PROSE. Every one of these is a message about the firm's
own governance, sent to a named person, with no human between the decision and
the send. A composed body would put the model in the position of describing an
authority decision it did not make, on a turn that has other work to do. The
bodies here interpolate exactly three kinds of value, all of them authored or
broker-minted: an address, the person's own sentence read back verbatim, and a
proposal tag. Nothing here states a timeline, a promise, or a commitment the
firm has not made.

THE OPERATIONS HALF (ss-console#2546, the second wave). A routine, a schedule, a
channel, a memory setting, an autonomy level or an on/off is SMD's to change
rather than the firm's, so it never reaches an administrator here at all — it
reaches SMD's desk, and the silence to end is the one AFTER that: the desk
answered and the person who asked never heard. :func:`notify_ops_outcome` is the
three sentences that close it, and they are deliberately NOT the four above.
"Your rule is in effect" says an administrator of the FIRM applied something,
which is a different fact about a different authority; sending it about a routine
change would tell somebody their own colleagues decided a thing SMD decided.

WHY IT IS INJECTED. ``send`` and ``emit`` are arguments, not imports. The send
has to run through the trust plugin's gate (see :mod:`shared.send_dispatch` for
the layout reason), and every branch here has to be provable in a test without a
broker, a mailbox, or a D1 binding. The rule that makes that safe is the same
one the sweeper follows: this module decides WHAT to say and to WHOM, and it
never decides whether a send is allowed.

WHAT A CALLER OWES. Every function returns a :class:`Notification` saying
whether the message went, and a caller that cannot say "yes" must tell the
person so. An Operator that says an administrator has been asked, when nothing
left the building, is worse than one that says nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from shared import wording_letter
from shared.layout_rules import clause_truth
from shared.output_classes import describe as describe_output_class
from shared.output_classes import has_live_rule_reader

logger = logging.getLogger(__name__)

#: The participant fence's lane for the administrators' request email; the
#: broker maps it to ``scope.rule_requests_to`` and nothing else.
LANE_RULE_DISPATCH = "rule_dispatch"

#: The audit type written when a rule request has actually been emailed to the
#: administrators the firm named. Its own row, rather than a field on somebody
#: else's: this is the moment a request reached a person, and it is the only
#: record that it did.
RULE_REQUEST_NOTIFIED = "RULE_REQUEST_NOTIFIED"

#: Cap on how many administrators one request pages. A firm authoring a long
#: routing list is authoring its own noise, but an unbounded fan-out from a
#: single inbound message is a send amplifier, so it is bounded here as well as
#: by what a person would plausibly author.
MAX_NOTIFIED = 8


@dataclass(frozen=True)
class Notification:
    """What one notification attempt did, and what the Operator may now say.

    ``note`` is written to be injected into the model's context verbatim. On the
    failure path it is an INSTRUCTION to be honest rather than a description of
    the failure, because the failure the loop has to survive is the model
    smoothing over a send that did not happen.
    """

    sent: bool
    note: str
    recipients: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    #: The send's answer was lost, so the message may be out
    #: (``DispatchResult.outcome_unknown``). Not sent as far as anyone can
    #: prove, and never to be sent again on that basis.
    outcome_unknown: bool = False


#: ``(to, subject, text, session_id, cc) -> object with .sent/.reason``
SendFn = Callable[..., Any]
#: ``(action_type, metadata) -> None``
EmitFn = Callable[..., None]


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _readback(proposal_id: str, text: str) -> str:
    """The canonical block, rendered the same way the broker renders it.

    Deliberately re-rendered rather than passed through from wherever the caller
    happened to have it: the tag and the sentence must arrive together or a
    reply cannot bind an answer to a rule.
    """
    return f"[rule {proposal_id}] {text}"


def _passes_scan(body: str) -> bool:
    """Would the seat's own outbound scan let this body out?

    WHY AN OUTCOME LETTER CHECKS ITSELF (2026-10-16). Every outcome letter
    quotes the request back verbatim, and a request is whatever the firm typed or
    the Operator built: on A&P act 7fbf43bb was a records order whose readback
    carried the vendor's "$100.00" custodian fee. The out-of-turn send runs with
    an empty session, so no figure is provenance-verified, the scan refused the
    letter, and the sweeper retried it every thirty seconds for 21 hours while
    the person it was for heard nothing. A letter that can never pass is not a
    transient failure. So the letter is composed to pass: the full quotation
    when it is clean, the bare tag when it is not. The tag is enough, because the
    letter is anchored to the email the request was made in.

    Most-restrictive scan (no cohort, no vertical, no provenance), the same
    posture the out-of-turn send gets. A scan that cannot run reads as a hit.
    """
    try:
        from shared.outbound_gate import evaluate

        return bool(evaluate(body, None, None).allowed)
    except Exception:  # noqa: BLE001 -- unknown means quote less, never more
        logger.warning("rule_dispatch: outcome letter scan could not run; sending the tag only")
        return False


def _compose(template: str, tagged: str, tag: str, **values: Any) -> str:
    """``template`` with the full quotation, or with the tag alone if the
    quotation would be refused by the outbound scan (see :func:`_passes_scan`)."""
    body = template.format(readback=tagged, **values)
    return body if _passes_scan(body) else template.format(readback=tag, **values)


_ADMIN_SUBJECT = "A rule for the firm to approve [rule {proposal_id}]"

_ADMIN_BODY = """{requester} asked for this to become how the firm's work reads:

{readback}

Reply "apply that" to put it in force, or "no" to decline it. Either answer is
passed back to {requester}.

Nothing is in effect unless an administrator answers. A request nobody answers
lapses on its own, and {requester} is told when it does.
"""

_INSTALLED_SUBJECT = "Your rule is in effect [rule {proposal_id}]"

_INSTALLED_BODY = """{applied_by} applied the rule you asked for:

{readback}

It is in effect from now, and work of that kind is written to it.
"""

#: The install letter for a rule whose class has NO live reader (2026-11-04).
#: Installing it is true; "in effect" is not, because the work it is about is
#: rendered by a lane that does not read the firm's rules
#: (:data:`shared.output_classes.LIVE_RULE_READER_CLASSES`). So the letter says
#: what is true: recorded, not yet shaping that work, handed to SMD. No date,
#: because none has been agreed. "Passed to SMD" is backed by the page
#: :func:`_page_unread_rule` raises, which is what puts it on SMD's desk. It
#: promises no second letter (2026-11-05): nothing would send one.
_RECORDED_SUBJECT = "Your rule is recorded [rule {proposal_id}]"

_RECORDED_BODY = """{applied_by} applied the rule you asked for:

{readback}

We have recorded the rule, but it is not yet in effect for {work}. That work is
produced by a part of the Operator that does not read the firm's rules yet, so
the rule has been passed to SMD to build into it.
"""

#: A layout rule, clause by clause (:func:`shared.layout_rules.clause_truth`):
#: what is in effect and what is recorded are said apart, from the installed file.
_PARTLY_SUBJECT = "Your rule is partly in effect [rule {proposal_id}]"

_CLAUSES_BODY = """{applied_by} applied the rule you asked for:

{readback}

In effect from the next document:

{effect}
{recorded}{after}"""

_RECORDED_CLAUSES = """
Recorded and passed to SMD, not yet in effect:

{clauses}

We have written these down and the document does not follow them yet.
"""

_RELATIVE_AFTER = """
Where you asked for more of something without a number, we add one step, and the
note with the document states the value we used. If you want more, say so.
"""

_BASE_OWNED_AFTER = """
A mediation brief takes the charts from your own Word file, so for a brief we have
recorded that part and passed it to SMD.
"""

_UNLISTED_CLAUSE = "- A part of the rule quoted by its number above"

#: What :data:`_RECORDED_BODY` calls the work when the class has no plain words.
_UNNAMED_WORK = "that kind of work"

#: The Sentry message that puts an unread rule on SMD's desk.
UNREAD_RULE_PAGE = "rule_installed_without_reader"

_DECLINED_SUBJECT = "Your rule was declined [rule {proposal_id}]"

_DECLINED_BODY = """{declined_by} declined the rule you asked for:

{readback}

Nothing changed. If you think it was misread, say it again and it will be put to
an administrator again.
"""

_LAPSED_SUBJECT = "Your rule lapsed unanswered [rule {proposal_id}]"

_LAPSED_BODY = """No administrator answered the rule you asked for, so it has
lapsed:

{readback}

Nothing changed. Say it again if you still want it, and it will be put to an
administrator again.
"""

#: An ACT (a ``tool_call`` row: an order, a filing, a send the Operator built
#: and held for a yes) is not a rule, and "your rule lapsed" about a records
#: order tells the person something they never asked for. Same news, its own
#: noun, and no claim about who was meant to answer: the readback itself said.
_ACT_LAPSED_SUBJECT = "Your request lapsed unconfirmed [act {proposal_id}]"

_ACT_LAPSED_BODY = """Nobody confirmed this request in time, so it lapsed and nothing was done:

{readback}

Say it again if you still want it.
"""

_ACT_DECLINED_SUBJECT = "Your request was declined [act {proposal_id}]"

_ACT_DECLINED_BODY = """{declined_by} declined this request, so nothing was done:

{readback}

If you think it was misread, say it again.
"""

#: SMD's operations desk AS A PUBLIC CONTACT, quoted in the lapsed notice so a
#: person whose request nobody answered has somewhere to go that is not this
#: seat. Deliberately NOT ``shared.operations_request.SMD_OPERATIONS_DESK``
#: since 2026-10-20: that one is where the seat addresses a card it needs an
#: answer to, and must be a mailbox rather than a Group (a Group rewrites the
#: seat's From and the answer never comes back). A person writing to SMD by
#: hand needs no answer routed to a seat, so the published team address is the
#: right one here.
OPS_DESK = "team@smd.services"


def _ops_readback(proposal_id: str, text: str) -> str:
    """The operations readback, rendered the way the broker renders it.

    Its own function beside :func:`_readback` rather than a parameter on it,
    because the tag WORD is the whole difference between "a sentence the firm
    agreed to" and "a change SMD made", and a caller passing the wrong one would
    tell somebody their rule was set up.
    """
    return f"[ops {proposal_id}] {text}"


_OPS_DONE_SUBJECT = "SMD set this up [ops {proposal_id}]"

_OPS_DONE_BODY = """SMD has made the change you asked for:

{readback}
{note}
Nothing else is needed from you. If it is not behaving the way you expected, say
so and the Operator will pass that on the same way.
"""

_OPS_DECLINED_SUBJECT = "SMD declined this request [ops {proposal_id}]"

_OPS_DECLINED_BODY = """SMD is not making the change you asked for:

{readback}
{note}
Nothing changed. If the request was misread, say it again with what you meant
and it will be put to SMD again.
"""

_OPS_LAPSED_SUBJECT = "Your request to SMD lapsed unanswered [ops {proposal_id}]"

_OPS_LAPSED_BODY = (
    """Nobody at SMD answered the change you asked for within a
week, so the request has lapsed:

{readback}

Nothing changed. Ask for it again and it will be put to SMD again, or write to
"""
    + OPS_DESK
    + """ directly.
"""
)

#: How SMD's own words are quoted, and they ARE quoted rather than paraphrased.
#: An Operator composing its own account of somebody else's business decision
#: would be inventing client-facing content; a quotation is a report.
_OPS_REASON_LINE = '\nSMD wrote: "{reason}"\n'
_OPS_DONE_NOTE_LINE = '\nSMD\'s note: "{reason}"\n'


_NOTIFIED_NOTE = (
    "The rule was recorded and this seat has ALREADY emailed it to {names} for "
    'them to answer with "apply that" or "no". Tell the person that, naming who '
    "was asked. Do not say it is in effect, do not offer to send anything "
    "yourself, and do not call a send tool: the message is already gone."
)

_NOT_NOTIFIED_NOTE = (
    "The rule was recorded, but this seat COULD NOT tell an administrator about "
    "it: {reason}. Say exactly that to the person, in your own words: their rule "
    "is written down, nobody has been asked yet, and they should forward it to "
    "an administrator themselves. Do not say an administrator was asked."
)

_UNKNOWN_NOTIFIED_NOTE = (
    "The rule was recorded, and this seat handed it to {names} by email, but the "
    "connection failed before the answer came back, so it is unknown whether "
    "they received it. SMD is checking. Tell the person that their rule is "
    "written down and that you are checking whether the administrator was told. "
    "Do not say an administrator was asked or that nobody was, do not ask them "
    "to forward it, and do not call a send tool."
)

_NO_ROUTING_NOTE = (
    "The rule was recorded, but this engagement names nobody to receive rule "
    "requests, so no administrator has been told. Say that plainly: the rule is "
    "written down and is waiting for an administrator to apply it. Do not claim "
    "anyone was asked."
)


def notify_admins(
    *,
    proposal_id: str,
    text: str,
    requester: str,
    rule_requests_to: list[str],
    send: SendFn,
    emit: EmitFn | None = None,
    session_id: str = "",
    anchor: dict[str, str] | None = None,
) -> Notification:
    """Put one paralegal's rule in front of the administrators the firm named.

    THE TAG GOES IN THE SUBJECT as well as the body. Reply chains vary wildly in
    what they quote, and the answer has to carry the tag or the seat cannot bind
    it to a rule; a "Re:" subject survives clients that strip the quoted body
    entirely.

    THE REQUESTER IS COPIED, on purpose. They asked for something and they can
    see that it was asked, by name, without waiting for an outcome. It also
    means the thread they answer on is the thread they started.

    Administrators the firm did NOT name receive nothing, here or anywhere: this
    sends to ``rule_requests_to`` and never to ``admins``. The two lists are
    separate precisely so a partner is not paged for every request.
    """
    recipients = [a for a in (_clean(x) for x in rule_requests_to or []) if a][:MAX_NOTIFIED]
    proposal_id = _clean(proposal_id)
    requester = _clean(requester)
    if not recipients:
        return Notification(sent=False, note=_NO_ROUTING_NOTE, reason="no routing authored")
    readback = _readback(proposal_id, _clean(text))
    result = send(
        to=recipients,
        cc=[requester] if requester else [],
        subject=_ADMIN_SUBJECT.format(proposal_id=proposal_id),
        text=_ADMIN_BODY.format(requester=requester, readback=readback),
        session_id=session_id,
        # The participant fence: the administrators are the firm's authored
        # routing (the lane), the requester is on the email that asked (the
        # anchor, this turn's own).
        anchor=anchor,
        lane=LANE_RULE_DISPATCH,
    )
    if getattr(result, "outcome_unknown", False):
        reason = _clean(getattr(result, "reason", ""))
        logger.error(
            "rule_dispatch: rule %s notification outcome unknown (%s)", proposal_id, reason
        )
        return Notification(
            sent=False,
            note=_UNKNOWN_NOTIFIED_NOTE.format(names=", ".join(recipients)),
            recipients=tuple(recipients),
            reason=reason,
            outcome_unknown=True,
        )
    if not getattr(result, "sent", False):
        reason = _clean(getattr(result, "reason", "")) or "the send was refused"
        logger.info("rule_dispatch: rule %s NOT notified (%s)", proposal_id, reason)
        return Notification(
            sent=False,
            note=_NOT_NOTIFIED_NOTE.format(reason=reason),
            recipients=tuple(recipients),
            reason=reason,
        )
    if emit is not None:
        _record(
            emit,
            proposal_id=proposal_id,
            requester=requester,
            recipients=recipients,
            message_id=_clean(getattr(result, "message_id", "")),
            session_id=session_id,
        )
    return Notification(
        sent=True,
        note=_NOTIFIED_NOTE.format(names=", ".join(recipients)),
        recipients=tuple(recipients),
    )


def _record(
    emit: EmitFn,
    *,
    proposal_id: str,
    requester: str,
    recipients: list[str],
    message_id: str,
    session_id: str,
) -> None:
    """Write the RULE_REQUEST_NOTIFIED row. Best-effort, never fatal.

    Ids and addresses only. The rule's TEXT is deliberately absent, for the same
    reason it is absent from RULE_PROPOSED: a proposal is a sentence somebody
    typed in an email, and the ledger keeps who and which, not what.

    It is a row of its own rather than a field on the send's own
    CONFIRM_SEND_DISPATCHED row, because the two answer different questions. The
    send row says a message left; this says a specific rule reached the people
    authorized to answer it, which is the fact anybody auditing this loop is
    actually looking for.
    """
    try:
        emit(
            action_type=RULE_REQUEST_NOTIFIED,
            metadata={
                "proposal_id": proposal_id,
                "instructed_by": requester,
                "notified_to": list(recipients),
                "notified_count": len(recipients),
                "sent_message_id": message_id,
            },
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001 — the send already happened
        logger.warning("rule_dispatch: RULE_REQUEST_NOTIFIED emission failed (%s)", exc)


def _installed_letter(
    proposal_id: str, readback: str, tag: str, by: str, output_class: str
) -> tuple[str, str]:
    """Subject and body for an install, honest about whether anything reads it.

    A ``work_product`` rule has a reader only for the clauses the seat compiled
    to a layout key the drafting lane applies (:mod:`shared.layout_rules`), and
    the proof is the INSTALLED ``layout.json``, read as the lane reads it, never
    the text.
    """
    if has_live_rule_reader(output_class):
        return (
            _INSTALLED_SUBJECT.format(proposal_id=proposal_id),
            _compose(_INSTALLED_BODY, readback, tag, applied_by=by),
        )
    # A wording rule a queued job now reads (shared/wording_letter.py), first:
    # it is the one answer that can call prose in effect for a lane document.
    worded = wording_letter.letter(
        proposal_id,
        readback,
        tag,
        by,
        output_class,
        compose=_compose,
        clause_truth=clause_truth,
        clause_letter=_clause_letter,
        page=_page_unread_rule,
    )
    if worded is not None:
        return worded
    truth = clause_truth(proposal_id, output_class)
    if truth and truth["compiled"]:
        return _clause_letter(proposal_id, readback, tag, by, output_class, truth)
    _page_unread_rule(proposal_id, output_class)
    work = describe_output_class(output_class) or _UNNAMED_WORK
    return (
        _RECORDED_SUBJECT.format(proposal_id=proposal_id),
        _compose(_RECORDED_BODY, readback, tag, applied_by=by, work=work),
    )


def _clause_letter(
    proposal_id: str,
    readback: str,
    tag: str,
    by: str,
    output_class: str,
    truth: dict[str, list[dict[str, Any]]],
) -> tuple[str, str]:
    """The letter for a rule the seat compiled, clause by clause. A clause the
    outbound scan would refuse is not quoted, the same fallback as the readback."""
    compiled, prose = truth["compiled"], truth["prose"]
    if prose:
        _page_unread_rule(proposal_id, output_class)
    after = _RELATIVE_AFTER if any(c.get("relative") for c in compiled) else ""
    after += _BASE_OWNED_AFTER if any(c.get("base_owned") for c in compiled) else ""
    subject = _PARTLY_SUBJECT if prose else _INSTALLED_SUBJECT

    def body(quoted: bool, quotation: str) -> str:
        def lines(rows: list[dict[str, Any]]) -> str:
            return "\n".join(f"- {r['text']}" if quoted else _UNLISTED_CLAUSE for r in rows)

        recorded = _RECORDED_CLAUSES.format(clauses=lines(prose)) if prose else ""
        return _CLAUSES_BODY.format(
            applied_by=by,
            readback=quotation,
            effect=lines(compiled),
            recorded=recorded,
            after=after,
        )

    full = body(True, readback)
    return (
        subject.format(proposal_id=proposal_id),
        full if _passes_scan(full) else body(False, tag),
    )


def _page_unread_rule(proposal_id: str, output_class: str) -> None:
    """A firm rule installed where nothing reads it: put it on SMD's desk.

    Ids and the class only, never the rule's text or an address, the same as
    every other seat page. Never raises: the letter is the firm's, the page is
    ours, and a monitoring fault must not cost the firm its letter.
    """
    logger.warning(
        "rule_dispatch: rule %s installed for class %r, which no live reader applies; "
        "the firm is told it is recorded, not in effect",
        proposal_id,
        output_class,
    )
    try:
        import os

        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("reason", UNREAD_RULE_PAGE)
            scope.set_tag("proposal_id", proposal_id)
            scope.set_tag("output_class", output_class or "(none)")
            scope.set_tag(
                "customer",
                os.environ.get("SMD_CUSTOMER_SLUG") or os.environ.get("CUSTOMER_SLUG") or "",
            )
            sentry_sdk.capture_message(UNREAD_RULE_PAGE, level="error")
    except Exception as exc:  # noqa: BLE001 - monitoring must never block the letter
        logger.debug("rule_dispatch: unread-rule page skipped (%s)", exc)


def notify_outcome(
    *,
    kind: str,
    proposal_id: str,
    text: str,
    requester: str,
    send: SendFn,
    by: str = "",
    session_id: str = "",
    anchor: dict[str, str] | None = None,
    row_kind: str = "rule",
    output_class: str = "",
) -> Notification:
    """Tell the person who asked how their rule ended.

    ``output_class`` is the class the broker's row binds the rule to. An
    ``installed`` letter says "in effect" only when that class has a live reader
    (:func:`shared.output_classes.has_live_rule_reader`); any other class,
    including an absent one, gets the recorded letter and SMD is paged.

    ``row_kind`` is the broker row's stored kind. A ``tool_call`` row is an act
    (an order or a send held for a yes), and its lapse and decline letters say
    "request", never "rule". An installed act does not exist: an act that ran
    is answered by the run itself.

    ``anchor`` is the rule row (``{"kind": "rule", ...}``): the broker resolves
    it to the email the rule was stated in, recorded when the row was made.

    ``kind`` is ``installed``, ``declined`` or ``lapsed``. Three sentences
    rather than one parameterised sentence, because the three are different news
    and a person reading them needs to know which happened without parsing a
    clause: one is a change to how their work is written, one is an answer from
    a named colleague, and one is nobody having answered at all.

    The caller marks the broker AFTER this returns sent, never before. A mark
    written first would trade a duplicate note for a silence, and silence is the
    failure this whole module exists to end.
    """
    proposal_id = _clean(proposal_id)
    requester = _clean(requester)
    if not requester:
        return Notification(sent=False, note="", reason="no requester to tell")
    is_act = _clean(row_kind) == "tool_call"
    tag = f"[{'act' if is_act else 'rule'} {proposal_id}]"
    readback = f"{tag} {_clean(text)}" if is_act else _readback(proposal_id, _clean(text))
    by = _clean(by) or "An administrator"
    if kind == "installed":
        subject, body = _installed_letter(proposal_id, readback, tag, by, output_class)
    elif kind == "declined":
        subject = (_ACT_DECLINED_SUBJECT if is_act else _DECLINED_SUBJECT).format(
            proposal_id=proposal_id
        )
        body = _compose(
            _ACT_DECLINED_BODY if is_act else _DECLINED_BODY, readback, tag, declined_by=by
        )
    elif kind == "lapsed":
        subject = (_ACT_LAPSED_SUBJECT if is_act else _LAPSED_SUBJECT).format(
            proposal_id=proposal_id
        )
        body = _compose(_ACT_LAPSED_BODY if is_act else _LAPSED_BODY, readback, tag)
    else:  # pragma: no cover — a caller bug, not a runtime state
        raise ValueError(f"unknown outcome kind: {kind!r}")
    result = send(
        to=[requester],
        cc=[],
        subject=subject,
        text=body,
        session_id=session_id,
        anchor=anchor,
        lane=None,
    )
    sent = bool(getattr(result, "sent", False))
    reason = _clean(getattr(result, "reason", ""))
    if not sent:
        logger.info(
            "rule_dispatch: %s outcome for rule %s NOT delivered (%s)", kind, proposal_id, reason
        )
    return Notification(
        sent=sent,
        note="",
        recipients=(requester,),
        reason=reason,
        outcome_unknown=bool(getattr(result, "outcome_unknown", False)),
    )


def notify_ops_outcome(
    *,
    kind: str,
    proposal_id: str,
    text: str,
    requester: str,
    send: SendFn,
    by: str = "",
    reason: str = "",
    session_id: str = "",
    anchor: dict[str, str] | None = None,
) -> Notification:
    """Tell the person who asked how SMD answered their operations request.

    THE HALF ss-console#2546 WAS MISSING. Before this the Operator told somebody
    their request had been passed on, which was true, and that was the last they
    heard of it: SMD's answer reached the desk's own mailbox and stopped there.
    From where the person sat, a request that was granted and a request that was
    ignored produced exactly the same silence.

    THREE SENTENCES, NOT ONE PARAMETERISED SENTENCE, for the reason
    :func:`notify_outcome` gives: the three are different news and the reader
    must know which happened without parsing a clause. They are also DELIBERATELY
    not that function's four — "your rule is in effect" says an administrator of
    the FIRM applied something, which is a different fact about a different
    authority, and sending it about a routine change would misattribute the
    decision to the reader's own colleagues.

    ``by`` IS NOT RENDERED, and its absence is the point rather than an omission.
    Who at SMD answered is on the broker row and in the ledger; putting a named
    individual in front of the firm would make the answer read as one person's
    opinion instead of the firm's, and it is a person's address, which the
    requester has no reason to hold.

    ``reason`` is SMD's OWN WORDS, quoted. It is optional on a ``done`` (where it
    reads as a note) and usual on a ``declined`` (where it is the answer). Its
    absence renders as nothing at all rather than as an empty quotation.
    """
    proposal_id = _clean(proposal_id)
    requester = _clean(requester)
    if not requester:
        return Notification(sent=False, note="", reason="no requester to tell")
    readback = _ops_readback(proposal_id, _clean(text))
    tag = f"[ops {proposal_id}]"
    quoted = _clean(reason)
    if kind == "done":
        subject = _OPS_DONE_SUBJECT.format(proposal_id=proposal_id)
        body = _compose(
            _OPS_DONE_BODY,
            readback,
            tag,
            note=_OPS_DONE_NOTE_LINE.format(reason=quoted) if quoted else "",
        )
    elif kind == "declined":
        subject = _OPS_DECLINED_SUBJECT.format(proposal_id=proposal_id)
        body = _compose(
            _OPS_DECLINED_BODY,
            readback,
            tag,
            note=_OPS_REASON_LINE.format(reason=quoted) if quoted else "",
        )
    elif kind == "lapsed":
        subject = _OPS_LAPSED_SUBJECT.format(proposal_id=proposal_id)
        body = _compose(_OPS_LAPSED_BODY, readback, tag)
    else:  # pragma: no cover - a caller bug, not a runtime state
        raise ValueError(f"unknown operations outcome kind: {kind!r}")
    result = send(
        to=[requester],
        cc=[],
        subject=subject,
        text=body,
        session_id=session_id,
        anchor=anchor,
        lane=None,
    )
    sent = bool(getattr(result, "sent", False))
    send_reason = _clean(getattr(result, "reason", ""))
    if not sent:
        logger.info(
            "rule_dispatch: %s outcome for operations request %s NOT delivered (%s)",
            kind,
            proposal_id,
            send_reason,
        )
    return Notification(
        sent=sent,
        note="",
        recipients=(requester,),
        reason=send_reason,
        outcome_unknown=bool(getattr(result, "outcome_unknown", False)),
    )


__all__ = [
    "MAX_NOTIFIED",
    "OPS_DESK",
    "RULE_REQUEST_NOTIFIED",
    "UNREAD_RULE_PAGE",
    "EmitFn",
    "Notification",
    "SendFn",
    "notify_admins",
    "notify_ops_outcome",
    "notify_outcome",
]
