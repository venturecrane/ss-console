"""Does a reply body assert, unhedged, that a rule is already in force?

Split out of the establishment plugin's ``__init__`` (2026-11-21, the module-size
ratchet), for :func:`_in_effect_gate`. The phrase lists stay in the plugin
(:data:`_EFFECT_CLAIMS`, :data:`_EFFECT_HEDGES`, :data:`_HEDGE_WINDOW`), read
at call time, because the operations-closure gate reads them too.

THE NEGATION REACH (2026-11-21). The hedge window is one short clause, 24
characters, so that a "will" belonging to another verb cannot launder a claim.
A NEGATION is different: "I cannot say for certain that the rule is in effect"
puts "cannot" 30 characters before the phrase, and the gate read the sentence
as a claim. A negation anywhere earlier in the same sentence (back to the last
full stop, question mark, exclamation mark or line break, at most
:data:`NEGATION_REACH` characters) now hedges it. A denial in an earlier
sentence still launders nothing.
"""

from __future__ import annotations

import re
import sys
from types import ModuleType

from shared import rule_confirm

#: The negations that hedge a claim anywhere earlier in its own sentence.
NEGATIONS = (
    "cannot",
    "can't",
    "could not",
    "couldn't",
    "not yet",
    "yet to be",
    "is not",
    "isn't",
    "was not",
    "wasn't",
    "were not",
    "has not",
    "hasn't",
    "have not",
    "haven't",
    "unable to",
    "failed to",
)
NEGATION_REACH = 160
#: A line break arrives JSON-escaped (the gate reads the serialized tool args).
_SENTENCE_END = re.compile(r"[.!?\n]|\\n")


#: The same gate for a person's own rule this turn's code COMMITTED (2026-11-21):
#: it is recorded, and only its install has not been seen yet. Telling the model
#: the commit failed is how an attorney was told "nothing was recorded".
RECORDED_MESSAGE = (
    "Held: this message says the rule {rule} is already in force. It IS recorded: the "
    "person's yes was committed this turn, and there is nothing to commit or submit. "
    "Only its install has not been seen yet, so do not say it is in effect now. Keep the "
    "seat's sentence exactly as given, and say nothing else about whether the rule is "
    "recorded: never that it was not recorded, never that it is waiting on their yes."
)


def _plugin() -> ModuleType:
    return sys.modules[__name__.rpartition(".")[0]]


def _hedged(lowered: str, found: int) -> bool:
    plug = _plugin()
    window = lowered[max(0, found - plug._HEDGE_WINDOW) : found]
    if any(hedge in window for hedge in plug._EFFECT_HEDGES):
        return True
    sentence = _SENTENCE_END.split(lowered[max(0, found - NEGATION_REACH) : found])[-1]
    return any(negation in sentence for negation in NEGATIONS)


def claims(blob: str) -> bool:
    """Two halves, both required. The ANCHOR keeps the scan on messages that are
    about a rule at all: a tag, or the bare word. The CLAIM is a completed-state
    phrase that is not hedged (:func:`_hedged`)."""
    lowered = blob.lower()
    if not rule_confirm.RULE_TAG.search(lowered) and not re.search(r"\brules?\b", lowered):
        return False
    for phrase in _plugin()._EFFECT_CLAIMS:
        start = 0
        while (found := lowered.find(phrase, start)) >= 0:
            if not _hedged(lowered, found):
                return True
            start = found + 1
    return False


__all__ = ["NEGATIONS", "NEGATION_REACH", "RECORDED_MESSAGE", "claims"]
