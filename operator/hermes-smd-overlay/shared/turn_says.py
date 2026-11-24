"""The relay's register of a turn's fixed sentences, and the block rule over them.

A SAY IS A REQUIRED BLOCK (2026-11-19, the review's design). Every sentence a
tool or a hook noted for this email must appear in the reply verbatim (after
:func:`norm`), as its own lines, each exactly once, in any order. Other
paragraphs may sit before, between and after: a second question in the same
email is answered there. On a turn with a say, those other paragraphs must not
describe how the work is done (:data:`MECHANICS_PHRASES`).

The relay's step 5a (``plugins/hermes-smd-reply/relay_checks.turn_say``) holds
any reply that breaks that. Tools reach it through their results (the records
and drafting submits, the drafting add-to-request, the help note).
"""

from __future__ import annotations

import html as html_lib
import json
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
    (:meth:`get`). A ``slot`` (a hook's sentence rewritten as the turn goes:
    the establishment plugin's read-back of a person's own rules) is replaced
    in place rather than added again.

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


#: ``(session_id, message_id, say, slot="") -> bool``, set by the reply plugin
#: at load (``plugins/hermes-smd-reply/relay_checks.note_turn_say``). A HOOK's
#: sentence (the establishment plugin's read-back, its numbered list, its reply
#: to a confirmation) has no tool result to carry it, so the hook calls
#: :func:`note`, which writes the SAME register step 5a reads.
_NOTE: Callable[..., bool] | None = None


#: The longest hook paragraph taken: a read-back of several rules with their reach.
HOOK_SAY_MAX = 6000


def writer_for(register: TurnSays) -> Callable[..., bool]:
    """The writer the reply plugin publishes for its own ``register``: a hook's
    required paragraph for one email, ``slot`` replacing an earlier version in
    place. True when recorded. Never raises."""

    def note_turn_say(session_id: str, message_id: str, say: str, slot: str = "") -> bool:
        if not (
            isinstance(session_id, str)
            and session_id
            and isinstance(message_id, str)
            and message_id
        ):
            return False
        if not isinstance(say, str) or not say.strip() or len(say) > HOOK_SAY_MAX:
            return False
        try:
            register.note(session_id, message_id, say.strip(), slot=slot)
        except Exception:  # noqa: BLE001 - noting never breaks the caller
            logger.warning("turn_says: noting a hook's fixed sentence failed", exc_info=True)
            return False
        return True

    return note_turn_say


def publish(note_turn_say: Callable[..., bool]) -> None:
    """The reply plugin's own writer into its register. The latest load wins."""
    global _NOTE
    _NOTE = note_turn_say


def published() -> bool:
    """Has the reply plugin published its register (so :func:`note` can hold)?"""
    return _NOTE is not None


def note(session_id: str, message_id: str, say: str, slot: str = "") -> bool:
    """Register ``say`` as a required block of the reply to email ``message_id``.

    True when the relay will hold a body without it. With the reply plugin not
    loaded nothing is recorded and this returns False. Never raises."""
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
#: leaks were on turns with no fixed sentence). Word-bounded. No sentence the
#: code itself tells the model to send may trip it
#: (``tests/test_code_sentences_pass_tiers.py``). DROPPED as unsafe: "job id"
#: (an employee's job ID), "this machine" (a product-liability matter's
#: machine) and "the system refused" (the e-filing system refused it), which
#: stay in the say-turn tier; "hold class" (class certification hearings);
#: "this seat" (the escalation digest's own "on this seat"); "drafting job"
#: (the drafting lane's own status and tool text, which the model reads and may
#: relay; it stays in the say-turn tier); "this turn" and
#: "an email turn" (refusal reasons the records lane relays as they come back:
#: "nothing was queued on this turn", "submitted from an email turn"); and
#: "[rule", which the administrators' request email and its ask-notes still
#: carry (a rule waiting on an administrator, and an act, keep their tags). A
#: person's OWN rule is read back with no id since 2026-11-20, so "rule id"
#: is back in this tier.
TIER1_PHRASES: tuple[str, ...] = (
    "this system refused",
    "automated drafting",
    "drafting queue",
    "proof run",
    "my email tool",
    "install check",
    "submitted for installation",
    "rule id",
)
_TIER1 = re.compile(
    r"(?<![\w\[])(" + "|".join(re.escape(p) for p in TIER1_PHRASES) + r")(?!\w)",
    re.IGNORECASE,
)


def tier1_phrase(text: str, html: str) -> str:
    """The first tier-1 phrase in any part a send carries, or ``""``."""
    for part in (text, html_lines(html)):
        for line in _lines(part or ""):
            found = _TIER1.search(line)
            if found:
                return found.group(1)
    return ""


def tier1_problem(text: str, html: str) -> str:
    """:func:`tier1_phrase` worded as a block-rule problem, or ``""``."""
    phrase = tier1_phrase(text, html)
    return f'must not describe how the work is done ("{phrase}")' if phrase else ""


#: A tier-1 hold's own redraft instruction (review 2026-11-19): it names the
#: phrase to take out. The fixed-wording text was wrong on a turn with no
#: fixed sentence.
MECHANICS_REDRAFT = (
    'mechanics_named: nothing was sent and the one reply is still yours. Rewrite it without "{phrase}": '
    "say what the person needs, not how the work is done."
)


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

    phrase = "" if _is_smd_address(reply_to) else tier1_phrase(text, html)
    if not phrase:
        return None
    if count() > TIER1_MAX_REDRAFTS:
        return "mechanics_cap", stop_detail
    return "mechanics", MECHANICS_REDRAFT.format(phrase=phrase)


#: A limit hold's redraft instruction (2026-11-24, A&P): the words that stated
#: the limit, and the owner's sentence to say instead.
LIMIT_REDRAFT = (
    'limit_named: nothing was sent and the one reply is still yours. It says "{words}": never '
    "tell the person what you cannot do or why. Where you cannot do what they asked, write "
    'exactly "{sentence}" as its own paragraph instead, and answer the rest of the email as '
    "usual. Do not list files, say what you tried, or name a tool, a format or a limit. A plain "
    "fact the person asked about (there is no hearing set) is an answer, not a limit: state it "
    "as a fact."
)


def limit_hold(
    reply_to: object, text: str, html: str, count: Callable[[], int], stop_detail: str
) -> tuple[str, str] | None:
    """The limit rule's decision for one reply (``shared.reply_could_not``):
    ``(rule, detail)`` to hold it, or None. A reply to SMD's own address is
    never held. ``count`` notes this email's hold and returns how many it has
    had; past :data:`TIER1_MAX_REDRAFTS` the detail is ``stop_detail``."""
    from shared import reply_could_not
    from shared.heartbeat import _is_smd_address

    if _is_smd_address(reply_to):
        return None
    words = reply_could_not.stated_limit(text) or reply_could_not.stated_limit(html_lines(html))
    if not words:
        return None
    if count() > TIER1_MAX_REDRAFTS:
        return "limit_cap", stop_detail
    return "limit", LIMIT_REDRAFT.format(words=words, sentence=reply_could_not.LOOK_INTO)


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
    r"(?<![\w\[])(" + "|".join(re.escape(p) for p in MECHANICS_PHRASES) + r")(?!\w)",
    re.IGNORECASE,
)
_BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|p|/div|div|/li|li|/tr|/h[1-6])\b[^>]*>", re.IGNORECASE)


def html_lines(markup: str) -> str:
    """An HTML part as text with its block breaks kept as lines."""
    return from_html(_BLOCK_TAGS.sub("\n", markup))


def _lines(text: str) -> list[str]:
    return [line for line in (norm(raw) for raw in text.splitlines()) if line]


def forbid_entry(matter_number: str, words: list[str]) -> str:
    """The register entry for a "none" point: its matter number and the words
    its document goes by ("mediation brief", "brief")."""
    return FORBID + json.dumps({"matter": matter_number, "words": [w.lower() for w in words if w]})


#: Words that claim a document is in hand: received, started, being prepared.
_IN_HAND = re.compile(
    r"\b(?:received|with us|start(?:ed|ing)?|prepar\w*|drafting|working on|in hand|underway|queued|on it)\b"
)


#: A paragraph that says the document is NOT in hand is not a claim.
_NEGATED = re.compile(
    r"\b(?:not|haven't|hasn't|have not|has not|could not|couldn't|cannot|can't|unable|no)\b"
)


def _claims(paragraph: str, entry: str) -> bool:
    """Does ``paragraph`` speak of that matter's document as received, started
    or being prepared? The matter number and one of the document's words in the
    same paragraph, with an in-hand word: an answer about the same matter that
    does not claim the document (its deadline, its judge) is not refused."""
    try:
        spec = json.loads(entry[len(FORBID) :])
    except ValueError:
        return False
    number = str(spec.get("matter") or "")
    words = [w for w in spec.get("words") or [] if isinstance(w, str) and w]
    if not number or not words:
        return False
    if _NEGATED.search(paragraph):
        return False  # "I have not started the brief for 4410" is the truth, not a claim
    on_matter = re.search(rf"(?<![\w-]){re.escape(number.lower())}(?![\w-])", paragraph)
    named = any(re.search(rf"\b{re.escape(w)}\b", paragraph) for w in words)
    return bool(on_matter and named and _IN_HAND.search(paragraph))


def _paragraphs(text: str) -> list[str]:
    return [p for p in (" ".join(_lines(raw)).lower() for raw in re.split(r"\n\s*\n", text)) if p]


def block_problem(blocks: tuple[str, ...] | list[str], text: str, mechanics: bool = True) -> str:
    """Why ``text`` breaks the block rule over ``blocks``, or ``""`` when it holds.

    Each block's lines must appear as a contiguous run of the text's lines (a
    blank line or a single break between them is the same), each block exactly
    once, in ANY order (2026-11-20: a firm reply carrying both of its turn's
    sentences, in the other order to the tool calls, was held three times and
    never sent). Only the blocks actually missing, altered or repeated are
    named. In the lines no block covers, a phrase
    from :data:`MECHANICS_PHRASES` is refused, but only when a real fixed
    sentence is in the reply and ``mechanics`` (the relay passes False for a
    reply to SMD's own address). A ``FORBID`` entry refuses a paragraph that
    claims that "none" point's document anyway (:func:`_claims`)."""
    body = _lines(text)
    forbids = [b for b in blocks if b.startswith(FORBID)]
    blocks = [b for b in blocks if not b.startswith(FORBID)]
    mechanics = mechanics and bool(blocks)
    covered: set[int] = set()
    missing: list[str] = []
    for block in blocks:
        want = _lines(block)
        if not want:
            continue
        starts = [i for i in range(len(body) - len(want) + 1) if body[i : i + len(want)] == want]
        if len(starts) != 1:
            missing.append(block)
            continue
        covered.update(range(starts[0], starts[0] + len(want)))
    if len(missing) == 1:
        return f'must contain this sentence exactly, once, as its own paragraph: "{missing[0]}"'
    if missing:
        quoted = " ".join(f'"{b}"' for b in missing)
        return f"must contain each of these sentences exactly, once, as its own paragraph: {quoted}"
    for i, line in enumerate(body):
        found = _MECHANICS.search(line) if mechanics and i not in covered else None
        if found:
            return f'must not describe how the work is done ("{found.group(1)}")'
    fixed = {norm(" ".join(_lines(b))).lower() for b in blocks}
    for paragraph in _paragraphs(text):
        if paragraph not in fixed and any(_claims(paragraph, f) for f in forbids):
            return "must say nothing about the document this email asked for, which got no fixed sentence"
    return ""


def send_problem(blocks: tuple[str, ...], text: str, html: str, reply_to: object = None) -> str:
    """:func:`block_problem` over every part a send carries (its text, and its
    HTML read as lines): the first part's problem, or ''. No tier-2 phrase is
    refused in a reply to SMD's own address (an internal guide seat)."""
    from shared.heartbeat import _is_smd_address

    mechanics = not _is_smd_address(reply_to)
    parts = [p for p in (text, html_lines(html)) if p.strip()] or [""]
    for part in parts:
        if problem := block_problem(blocks, part, mechanics):
            return problem
    return ""


#: How every refusal below begins; the hold notice reads it to tell a
#: required-paragraph hold from the broker's whole-body one
#: (``plugins/hermes-smd-reply/notice.SAY_REFUSAL_LEAD``, pinned equal by test).
REFUSAL_LEAD = "fixed_wording: nothing was sent and the one reply is still yours. The reply "


def refusal(problem: str, blocks: tuple[str, ...] | list[str] = ()) -> str:
    """What the model reads: the sentence missing or altered, or the phrase to
    take out. On a turn with more than one required sentence it then lists every
    one, so a redraft cannot drop a sentence it was never shown."""
    real = [b for b in blocks if not b.startswith(FORBID)]
    every = ""
    if len(real) > 1:
        quoted = " ".join(f'"{b}"' for b in real)
        every = (
            f" All {len(real)} of these sentences must be in the reply, each exactly once as "
            f"its own paragraph, in any order: {quoted}."
        )
    return (
        f"{REFUSAL_LEAD}{problem}.{every} Answer anything else the email asked in other "
        "paragraphs; never describe how the work is done."
    )


__all__ = [
    "FORBID",
    "MECHANICS_PHRASES",
    "TIER1_PHRASES",
    "SAY_NONE",
    "TurnSays",
    "block_problem",
    "forbid_entry",
    "from_html",
    "html_lines",
    "norm",
    "note",
    "publish",
    "published",
    "REFUSAL_LEAD",
    "refusal",
    "send_problem",
    "MECHANICS_REDRAFT",
    "mechanics_hold",
    "tier1_phrase",
    "tier1_problem",
]
