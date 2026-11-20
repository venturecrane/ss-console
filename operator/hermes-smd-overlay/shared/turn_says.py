"""The relay's register of a turn's fixed sentences, and the block rule over them.

A SAY IS A REQUIRED BLOCK (2026-11-19, the review's design). Every sentence a
tool or a hook noted for this email must appear in the reply verbatim (after
:func:`norm`), as its own lines, each exactly once, in the order noted. Other
paragraphs may sit before, between and after: a second question in the same
email is answered there. On a turn with a say, those other paragraphs must not
describe how the work is done (:data:`MECHANICS_PHRASES`).

The relay's step 5a (``plugins/hermes-smd-reply/relay_checks.turn_say``) holds
any reply that breaks that. Tools reach it through
their results; a sentence written by a HOOK (the establishment plugin's reply to
a confirmation, its numbered list, its read-back) has no tool result to carry it.
So the reply plugin publishes its own ``note_turn_say`` here at load, and a hook
calls :func:`note`, which writes the SAME register that step reads. No second
register, no second comparison: with the reply plugin not loaded, :func:`note`
records nothing and returns False, and the reply goes out as it always did.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: A ``say`` that means: no fixed sentence for THIS point (review, 2026-11-19).
#: It adds no required block and holds nothing by itself: the reply goes out
#: with whatever else it answers, and a turn that drafts nothing sends nothing.
SAY_NONE = "none"
#: The prefix of a noted entry that is not a block but a phrase the reply must
#: not carry: a "none" point's own subject (the drafting lane's "mediation brief
#: for matter 4410"), so the model cannot claim the point anyway.
FORBID = "\x00not:"


class TurnSays:
    """Every whole-reply ``say`` noted this turn, per (session, email), in call order.

    Keyed on the email the turn's own trusted prompt bound, never only the
    session, so a later email in the same session is never held to an earlier
    email's sentence. Bounded; a restart forgets it, and then the reply is
    unchecked, as every reply was before this.

    SEVERAL IN ONE TURN (review, 2026-11-19). An email asking for five documents
    submits five jobs, each with its own sentence; a draft and a records request,
    or one class accepted and one refused, are two. Keeping only the last made a
    reply carrying all of them a refusal whose retry quoted one, so the firm
    heard about one document. Now every sentence is kept, de-duplicated, and the
    expected reply is all of them in call order, one paragraph each
    (:meth:`get`). A ``slot`` (a hook's sentence that is rewritten as the turn
    goes, the establishment plugin's read-back) is replaced in place rather than
    added again.

    ``"none"`` adds nothing: no block, no hold (``SAY_NONE``). A ``FORBID``
    entry is a phrase the reply must not carry outside a block."""

    _MAX = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._says: OrderedDict[tuple[str, str], list[tuple[str, str]]] = OrderedDict()

    def note(self, session_id: str, message_id: str, say: str, slot: str = "") -> None:
        with self._lock:
            key = (session_id, message_id)
            entries = self._says.pop(key, [])
            if slot and any(s == slot for s, _ in entries):
                entries = [(s, say if s == slot else said) for s, said in entries]
            elif not any(said == say for _, said in entries):
                entries.append((slot, say))
            self._says[key] = entries
            while len(self._says) > self._MAX:
                self._says.popitem(last=False)

    def get(self, session_id: str, message_id: str) -> tuple[str, ...] | None:
        """The required blocks and ``FORBID`` entries in the order noted, ``()``
        when only "none" was noted (a say turn with no block), ``None`` when
        nothing was noted."""
        with self._lock:
            entries = self._says.get((session_id, message_id))
        if not entries:
            return None
        return tuple(said for _, said in entries if said != SAY_NONE)


#: ``(session_id, message_id, say, slot="") -> bool``, set by the reply plugin at load.
_NOTE: Callable[..., bool] | None = None


def publish(note_turn_say: Callable[..., bool]) -> None:
    """The reply plugin's own writer into its register. The latest load wins."""
    global _NOTE
    _NOTE = note_turn_say


def note(session_id: str, message_id: str, say: str, slot: str = "") -> bool:
    """Register ``say`` as a required block of the reply to email ``message_id``.

    True when the relay will hold any other body. Never raises."""
    writer = _NOTE
    if writer is None:
        return False
    try:
        return bool(writer(session_id, message_id, say, slot=slot))
    except Exception:  # noqa: BLE001 - an unregistered sentence costs the check, never the turn
        logger.warning("turn_says: the relay did not take the sentence", exc_info=True)
        return False


def norm(text: str) -> str:
    """The broker's comparison, exactly (``workspace_broker/fixed_wording._norm``):
    curly quotes folded, every whitespace run one space. A copy, because the
    overlay cannot import the broker; ``test_relay_steps`` pins the two."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.replace(" ", " ").split())


def from_html(markup: str) -> str:
    """``workspace_broker/fixed_wording._from_html``, for an HTML-only draft."""
    return html_lib.unescape(re.sub(r"<[^>]*>", " ", markup))


#: TIER 1 (review 2026-11-19, a replay of 88 real sent replies): phrases that
#: name the Operator's machinery and nothing in ordinary legal email, refused in
#: EVERY reply to a firm address on every seat, say or no say (13 of 32 real
#: leaks were on turns with no fixed sentence). Each is word-bounded, and two
#: carry a guard: "this turn" not before "of" (this turn of events), "this seat"
#: not before "on" (this seat on the board). DROPPED from this tier as unsafe:
#: "job id" (an employee's job ID), "this machine" (a product-liability
#: matter's machine), "the system refused" (the e-filing system refused it);
#: they stay in the say-turn tier below.
TIER1_PHRASES: tuple[str, ...] = (
    "drafting job",
    "[rule",
    "rule id",
    "this system refused",
    "automated drafting",
    "drafting queue",
    "proof run",
    "this turn",
    "an email turn",
    "this seat",
    "my email tool",
    "hold class",
    "install check",
    "submitted for installation",
)
_TIER1 = re.compile(
    r"(?<![\w\[])("
    + "|".join(re.escape(p) for p in TIER1_PHRASES)
    + r")(?!\w)(?<!this turn(?= of\b))(?<!this seat(?= on\b))",
    re.IGNORECASE,
)


def tier1_problem(text: str, html: str) -> str:
    """The first tier-1 phrase in any part a send carries, as the refusal's
    problem, or ``""``."""
    for part in (text, html_lines(html)):
        for line in _lines(part or ""):
            found = _TIER1.search(line)
            if found:
                return f'must not describe how the work is done ("{found.group(1)}")'
    return ""


#: Redrafts a tier-1 hold quotes the phrase for; after them the model is told
#: to stop (the relay's 5a cap, ``SAY_MAX_REDRAFTS``).
TIER1_MAX_REDRAFTS = 2


def mechanics_hold(
    reply_to: object, text: str, html: str, count: Callable[[], int], stop_detail: str
) -> tuple[str, str] | None:
    """Tier 1's decision for one reply: ``(rule, detail)`` to hold it, or None.
    A reply to SMD's own address is never held here. ``count`` notes this
    email's hold and returns how many it has had."""
    from shared.heartbeat import _is_smd_address

    problem = "" if _is_smd_address(reply_to) else tier1_problem(text, html)
    if not problem:
        return None
    if count() > TIER1_MAX_REDRAFTS:
        return "mechanics_cap", stop_detail
    return "mechanics", refusal(problem)


#: Phrases that describe how the work is done, refused on a turn with a say in
#: the reply's OTHER paragraphs (TIER 2; tier 1 is included). Each came from a real leak to a firm on
#: 2026-11-19 ("job 01M7... is queued on this Machine", "was completed outside
#: the automated drafting", "[rule b45394a0]"). Matched case-insensitively and
#: word-bounded. DROPPED as unsafe on ordinary legal email: "lane" (Lane v.
#: Smith, Maple Lane), "gate" (a gate code), "broker" (an insurance broker).
MECHANICS_PHRASES: tuple[str, ...] = (
    "queue",
    "queued",
    "drafting job",
    "job id",
    "this machine",
    "automated drafting",
    "drafting queue",
    "rule id",
    "[rule",
    "proof run",
    "the system refused",
    *TIER1_PHRASES,
    # The owner's rule (2026-11-19): the Operator never says it passed anything
    # to SMD, a team or a person, nor that the problem was on our side. Kept
    # only where ordinary legal email does not use them. Left out on purpose:
    # "handed" (handed down), "forwarded" (the court forwarded), "a person" (a
    # person with knowledge), "passed to" (title passed to the buyer) and "on
    # our side" (the law is on our side).
    "smd",
    "passed your",
    "our team",
    "problem on our",
    "on our end",
)
_MECHANICS = re.compile(
    r"(?<![\w\[])(" + "|".join(re.escape(p) for p in MECHANICS_PHRASES) + r")(?!\w)", re.IGNORECASE
)
_BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|p|/div|div|/li|li|/tr|/h[1-6])\b[^>]*>", re.IGNORECASE)


def html_lines(markup: str) -> str:
    """An HTML part as text with its block breaks kept as lines."""
    return from_html(_BLOCK_TAGS.sub("\n", markup))


def _lines(text: str) -> list[str]:
    return [line for line in (norm(raw) for raw in text.splitlines()) if line]


def block_problem(blocks: tuple[str, ...] | list[str], text: str) -> str:
    """Why ``text`` breaks the block rule over ``blocks``, or ``""`` when it holds.

    Each block's lines must appear as a contiguous run of the text's lines (a
    blank line or a single break between them is the same), each block exactly
    once and after the one before it. In the lines no block covers, a phrase
    from :data:`MECHANICS_PHRASES` is refused."""
    body = _lines(text)
    forbids = [norm(b[len(FORBID) :]).lower() for b in blocks if b.startswith(FORBID)]
    blocks = [b for b in blocks if not b.startswith(FORBID)]
    covered: set[int] = set()
    at = 0
    for block in blocks:
        want = _lines(block)
        if not want:
            continue
        starts = [i for i in range(len(body) - len(want) + 1) if body[i : i + len(want)] == want]
        if len(starts) != 1 or starts[0] < at:
            return f'must contain this sentence exactly, once, as its own paragraph: "{block}"'
        at = starts[0] + len(want)
        covered.update(range(starts[0], at))
    for i, line in enumerate(body):
        if i in covered:
            continue
        found = _MECHANICS.search(line)
        if found:
            return f'must not describe how the work is done ("{found.group(1)}")'
        said = next((f for f in forbids if f and f in line.lower()), "")
        if said:
            return f"must say nothing about the {said}, which got no fixed sentence this turn"
    return ""


def send_problem(blocks: tuple[str, ...], text: str, html: str) -> str:
    """:func:`block_problem` over every part a send carries (its text, and its
    HTML read as lines): the first part's problem, or ''."""
    parts = [p for p in (text, html_lines(html)) if p.strip()] or [""]
    for part in parts:
        if problem := block_problem(blocks, part):
            return problem
    return ""


def refusal(problem: str) -> str:
    """What the model reads: the exact sentence, or the phrase to take out."""
    return (
        "fixed_wording: nothing was sent and the one reply is still yours. The reply "
        f"{problem}. Answer anything else the email asked in other paragraphs; never "
        "describe how the work is done."
    )


__all__ = [
    "FORBID",
    "MECHANICS_PHRASES",
    "TIER1_PHRASES",
    "SAY_NONE",
    "TurnSays",
    "block_problem",
    "from_html",
    "html_lines",
    "norm",
    "note",
    "publish",
    "refusal",
    "send_problem",
    "mechanics_hold",
    "tier1_problem",
]
