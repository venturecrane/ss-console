"""The rehearsal fence: an email marked as a rehearsal reaches SMD only.

WHY (2026-10-16). A rehearsal of a client check-in on the Brokery seat was
scheduled as "send exactly one email to smdurgan@smdurgan.com ... write it to
Tim". The model chose the client, who is rostered, and he received an email
whose subject read "[Rehearsal 2] Getting your pilot set up". Every fence that
existed passed it, correctly by its own rule: the recipient was authored for
the roster lane. What no fence asked is whether the message itself said it was
not meant for a client.

THE RULE. A transmit whose subject carries a rehearsal marker is
refused unless every recipient is SMD-owned: an SMD domain, or one of SMD's
own probe inboxes (``ss-probe-*@agentmail.to``, the stand-ins rehearsals
address as a firm person). A marked reply is refused unless the email it
answers came from an SMD-owned address, or the marker is inherited (see
:func:`check`). The recipient is the model's choice
on every turn; whether a message announces itself as a rehearsal is the
message's own text, so this holds no matter which prompt, lane or approval
produced the send.

THE MARKER. Bracketed ``[Rehearsal ...]``, ``[SMD-PROBE ...]`` or
``[Dry run ...]`` anywhere in the subject, or a subject that BEGINS (after any
Re:/Fwd: prefixes) with the word "Rehearsal". "Test" and "Probe" are
deliberately not markers, bracketed or not (narrowed 2026-10-16 on the
Captain's call): a client's own subjects use those words for real work, e.g.
"[Test results] Smith matter" or "Test results from Dr. Lee", and those must
still reach the attorney.

PURE, like participant_fence: it reads the payload and decides. Looking up a
reply's original sender is handed in as a callable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .participant_fence import FENCE_REHEARSAL, SMD_DOMAINS, FenceRefused, Participants
from .recipient_policy import domain_of, normalize_address

_BRACKETED = re.compile(r"\[\s*(?:rehearsal|smd-probe|dry[\s-]?run)\b[^\]]*\]", re.IGNORECASE)
_LEADING = re.compile(r"^\s*(?:(?:re|fwd?)\s*:\s*)*rehearsal\b", re.IGNORECASE)
_PROBE_INBOX = re.compile(r"^ss-probe-[a-z0-9-]+@agentmail\.to$")


def is_marked(subject: object) -> bool:
    """True when the subject announces the email as a rehearsal."""
    if not isinstance(subject, str) or not subject.strip():
        return False
    return bool(_BRACKETED.search(subject) or _LEADING.search(subject))


def is_smd_owned(address: str) -> bool:
    value = normalize_address(address)
    return domain_of(value) in SMD_DOMAINS or bool(_PROBE_INBOX.match(value))


def check(
    subject: object,
    recipients: Iterable[str],
    answered: Callable[[], Participants] | None,
    *,
    reply: bool = False,
) -> None:
    """Refuse a marked transmit that would reach anyone outside SMD, unless the
    marker is INHERITED from a conversation the outsider is already in.

    ``answered`` reads the email this transmit answers (the reply's target, or a
    send's anchor); ``None`` when it answers nothing. A reply reaches that
    email's sender, so for a reply the recipient set is that sender.

    INHERITED (2026-10-16, the same day): the client answered the leaked
    rehearsal in its own thread, so every reply to him now carries
    "Re: [Rehearsal 2]". Refusing those would cut off a live client because of
    SMD's mistake. A marker passes only when the email being answered already
    carries it, was written by the outsider (not by SMD), and every outside
    recipient was already on it. A NEW marked email to a client has no such
    email to answer, and is refused.
    """
    if not is_marked(subject):
        return
    if reply:
        if answered is None:
            raise FenceRefused(FENCE_REHEARSAL, "a marked reply must name the email it answers.")
        home = answered()
        recipients = [home.sender]
    outside = sorted({normalize_address(r) for r in recipients if r and not is_smd_owned(r)})
    if not outside:
        return
    if answered is not None:
        home = answered()
        if (
            is_marked(home.subject)
            and home.sender
            and not is_smd_owned(home.sender)
            and set(outside) <= {normalize_address(a) for a in home.everyone()}
        ):
            return
    raise FenceRefused(
        FENCE_REHEARSAL,
        f"this email is marked as a rehearsal and would reach {len(outside)} recipient(s) "
        "outside SMD: " + ", ".join(outside) + ". A rehearsal goes to SMD only.",
        outside,
    )


__all__ = ["FENCE_REHEARSAL", "check", "is_marked", "is_smd_owned"]
