"""The sentence a drafting request is acknowledged with, in one home.

Moved out of ``plugins/hermes-smd-lanes/drafting.py`` (2026-11-23) so the
establishment plugin can say the same words when an attorney who just confirmed
a rule follows up about the draft already queued for him: "Received. We'll make
those changes to the discovery responses for matter ...". The lane re-exports
every name here.
"""

from __future__ import annotations

#: How a reply names a class to the firm (ss-console drafting_ledger.CLASS_LABEL),
#: also the acknowledgment's and the refusals' words.
DOCUMENT = {
    "mediation_brief": "mediation brief",
    "discovery_set": "propounded discovery",
    "discovery_response": "discovery responses",
    "memo": "memo",
    "depo_outline": "deposition outline",
}

#: The document phrases that read as plural ("the discovery responses ... are").
PLURAL = frozenset({"discovery_response"})


def doc(klass: str, number: str) -> str:
    return f"{DOCUMENT.get(klass, 'document')} for matter {number}"


def is_(klass: str) -> str:
    return "are" if klass in PLURAL else "is"


def acknowledgment(klass: str, number: str, *, revising: bool = False) -> str:
    """The sentence a received drafting request is answered with, true whoever
    does the work: no time, no mechanics, nothing about who drafts it."""
    if revising:
        return (
            f"Received. We'll make those changes to the {doc(klass, number)} and reply in this thread "
            "when the new version is filed."
        )
    return (
        f"Received. The {doc(klass, number)} {is_(klass)} with us, and you'll get a reply in this thread when "
        "the draft is filed in the matter."
    )


__all__ = ["DOCUMENT", "PLURAL", "acknowledgment", "doc", "is_"]
