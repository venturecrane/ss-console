"""The rehearsal fence: an email marked as a rehearsal or a test reaches SMD only.

WHY (2026-10-16). A rehearsal of a client check-in on the Brokery seat was
scheduled as "send exactly one email to smdurgan@smdurgan.com ... write it to
Tim". The model chose the client, who is rostered, and he received an email
whose subject read "[Rehearsal 2] Getting your pilot set up". Every fence that
existed passed it, correctly by its own rule: the recipient was authored for
the roster lane. What no fence asked is whether the message itself said it was
not meant for a client.

THE RULE. A transmit whose subject carries a rehearsal or test marker is
refused unless every recipient is SMD-owned: an SMD domain, or one of SMD's
own probe inboxes (``ss-probe-*@agentmail.to``, the stand-ins rehearsals
address as a firm person). A marked reply is refused unless the email it
answers came from an SMD-owned address. The recipient is the model's choice
on every turn; whether a message announces itself as a rehearsal is the
message's own text, so this holds no matter which prompt, lane or approval
produced the send.

THE MARKER. Bracketed ``[Rehearsal ...]``, ``[Test ...]``, ``[SMD-PROBE ...]``,
``[Probe ...]`` or ``[Dry run ...]`` anywhere in the subject, or a subject
that BEGINS (after any Re:/Fwd: prefixes) with the word "Rehearsal". An
unbracketed leading "Test" is deliberately not a marker: a law firm's real
subject "Test results from Dr. Lee" must still reach its attorney.

PURE, like participant_fence: it reads the payload and decides. Looking up a
reply's original sender is handed in as a callable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .participant_fence import FENCE_REHEARSAL, SMD_DOMAINS, FenceRefused
from .recipient_policy import domain_of, normalize_address

_BRACKETED = re.compile(r"\[\s*(?:rehearsal|test|smd-probe|probe|dry[\s-]?run)\b[^\]]*\]", re.IGNORECASE)
_LEADING = re.compile(r"^\s*(?:(?:re|fwd?)\s*:\s*)*rehearsal\b", re.IGNORECASE)
_PROBE_INBOX = re.compile(r"^ss-probe-[a-z0-9-]+@agentmail\.to$")


def is_marked(subject: object) -> bool:
    """True when the subject announces the email as a rehearsal or a test."""
    if not isinstance(subject, str) or not subject.strip():
        return False
    return bool(_BRACKETED.search(subject) or _LEADING.search(subject))


def is_smd_owned(address: str) -> bool:
    value = normalize_address(address)
    return domain_of(value) in SMD_DOMAINS or bool(_PROBE_INBOX.match(value))


def check_send(subject: object, recipients: Iterable[str]) -> None:
    """Refuse a marked send that would reach anyone outside SMD."""
    if not is_marked(subject):
        return
    outside = sorted({normalize_address(r) for r in recipients if r and not is_smd_owned(r)})
    if outside:
        raise FenceRefused(
            FENCE_REHEARSAL,
            f"this email is marked as a rehearsal or test and would reach {len(outside)} recipient(s) "
            "outside SMD: " + ", ".join(outside) + ". A rehearsal goes to SMD only.",
            outside,
        )


def check_reply(subject: object, original_sender: Callable[[], str]) -> None:
    """Refuse a marked reply unless the email it answers came from SMD."""
    if not is_marked(subject):
        return
    sender = original_sender()
    if not sender or not is_smd_owned(sender):
        raise FenceRefused(
            FENCE_REHEARSAL,
            "this reply is marked as a rehearsal or test and answers an email from outside SMD. "
            "A rehearsal goes to SMD only.",
            [sender] if sender else [],
        )


__all__ = ["FENCE_REHEARSAL", "check_reply", "check_send", "is_marked", "is_smd_owned"]
