"""The establishment plugin's standing turn notes (moved out of ``__init__``
2026-11-20 so the module-size ratchet holds), and the choice between them and
the rules-by-hand note (``shared.rules_by_hand``, a stopgap)."""

from __future__ import annotations

from collections.abc import Callable

from shared import rules_by_hand

#: The tool names, as ``__init__`` registers them (its TOOL_PROPOSE, TOOL_STATUS).
_PROPOSE = "establish_propose"
_STATUS = "establish_status"

#: The class map in plain words, for the places the model chooses one. Written
#: as "the kind of output -> the class" rather than "the class -> its
#: definition", because that is the direction the model reads it in: it has a
#: sentence from a person and needs a slug, and a glossary ordered the other way
#: is one it has to search (ss-console#2546 follow-up).
_CLASS_CHOICE_MAP = (
    "internal email or a note to firm staff -> staff; a document drafted for the "
    "firm and filed to a matter -> work_product; an internal record such as a "
    "chronology row or a ledger line -> record; letters and emails to the firm's "
    "own clients -> outbound_client; to records vendors, providers or "
    "lienholders -> outbound_vendor; to opposing counsel, carriers, courts or "
    "any other outside party -> outbound_external. WHO READS IT decides, never "
    "what the document is called: 'demand letter', 'letter' and 'email' are not "
    "classes and a rule attached to one binds to nothing."
)

#: THE ONE NUDGE (ss-console#2529). It replaced two — an admin-only line about
#: documents and a person-only line about preferences — because the split was
#: not the one the firm experiences. What a person says is either about their
#: own work or about a kind of the firm's work, and neither of those is "do you
#: have documents for me". The old pair had no route at all for the ordinary
#: case: a partner writing one sentence about how letters should read.
#:
#: WHY THE READBACK SHAPE IS SPELLED OUT HERE. It is the whole control. The
#: person is agreeing to a specific sentence, so they have to be shown that
#: sentence, told what it attaches to, and given something unambiguous to
#: answer. A readback that paraphrases produces a "yes" that means something the
#: seat cannot act on. Since 2026-11-20 the person's own rule carries no tag:
#: the call returns the whole read-back, and the relay holds the reply to it.
#:
#: AND WHY IT FORBIDS ONE SENTENCE BY NAME. On 2026-08-21 two rehearsal turns
#: spoken by an Operator admin were both answered "captured and queued for
#: review, not in effect until a person acts" — which was true of the code and
#: false of the promise (ADR 0085 §3). Telling a partner their own instruction
#: needs somebody else's permission is the failure this path exists to end.
_ESTABLISH_NUDGE = (
    "When someone tells you how work should read from now on — not just this "
    "one message — that is a standing rule, and you have a way to make it real. "
    "Two kinds, and the difference is who the work is for:\n"
    "- about work produced FOR THEM (their drafts, what you send them, length, "
    f"tone, what to lead with): {_PROPOSE} with scope 'person' and subject "
    "{{person: their exact address}}.\n"
    "- about how a KIND OF FIRM OUTPUT reads (every client letter, every memo): "
    f"{_PROPOSE} with scope 'firm_adjust' and subject {{output_class, "
    "property}} — 'voice' for how it sounds, 'format' for how it is shaped.\n"
    "The call returns the read-back: the rule, what it covers, and the line "
    "asking them to reply yes. Include it in your reply word for word, as its "
    "own paragraph, with no id or tag of your own: they are agreeing to that "
    "sentence, not to your summary of it. Put the rule in `text` in their words, "
    "and keep any citation they wrote exactly as they wrote it.\n"
    "Never tell someone their own rule is queued for review, or not in effect "
    "until a person acts, when they are the person. Say what it will do and ask "
    "them to confirm. And do not say it IS in effect until you have called "
    f"{_STATUS} and seen it installed."
)

#: Appended when a non-admin states a FIRM rule: it is recorded, it waits, and
#: the reply names who can release it and the exact words that do it.
#:
#: IT NAMES THE ROUTING LIST, NOT ``scope.admins`` (ss-console#2546 follow-up).
#: Live on the pilot, 2026-08-22: the Operator told a paralegal it had "flagged
#: it to scott@smd.services and ss-probe-admin" -- the administrators -- while
#: the request had gone to ``rule_requests_to``, which is a different list by
#: design (a partner is not paged for every request). The dispatch was right and
#: the sentence was wrong, which is the worse half: the person is told to chase
#: an answer from somebody who was never asked, and the two lists diverge
#: silently because nothing renders both.
_FOR_ADMIN_LINE = (
    "This person is not one of the firm's Operator admins, so a rule about the "
    "firm's own output is recorded and waits for one. Propose it with "
    "for_admin true, then tell them plainly that it is recorded and that {routed} "
    "can put it in force by replying 'apply that' on this thread. Do not tell "
    "them it is in effect."
)

#: Appended to every attributed turn, because choosing the class is the step
#: that went wrong silently (ss-console#2546 follow-up): four rules on the pilot
#: were attached to 'letter' and 'demand_letter', installed into directories
#: nothing reads, and reported in force. The gate refuses those now, but a gate
#: the model keeps hitting is a worse instrument than a map it reads first.
_CLASS_CHOICE_LINE = (
    "A firm rule attaches to an OUTPUT CLASS, and the class is decided by who "
    f"reads the output: {_CLASS_CHOICE_MAP} When you propose one, tell the "
    "person in plain words which kind of output the rule will apply to, so they "
    "can correct you before it is in force."
)

#: Appended when the stated rule is about a scheduled routine rather than about
#: how something reads. Two different things, and confirming both as one is the
#: over-confirmation card 11 was written against.
_SCHEDULE_LIMIT_LINE = (
    "If part of what they asked for is WHEN something runs or WHICH channel it "
    "arrives on, that is seat configuration and this does not change it: a "
    "scheduled turn has no sender, so it never sees a personal rule at all. "
    "Record the shape half, and say plainly which half you could not record and "
    "that an Operator admin changes it."
)


def turn_nudges(is_admin: bool, routed: Callable[[], str]) -> list[str]:
    """The rule notes for one attributed turn: the read-back instructions, or on
    a ``rules_by_hand`` seat the one note that replaces them."""
    if rules_by_hand.on():
        return [rules_by_hand.NUDGE]
    admin_line = [] if is_admin else [_FOR_ADMIN_LINE.format(routed=routed())]
    return [_ESTABLISH_NUDGE, *admin_line, _CLASS_CHOICE_LINE, _SCHEDULE_LIMIT_LINE]
