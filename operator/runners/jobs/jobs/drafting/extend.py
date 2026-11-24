"""The one "more requests" pass for a set below the firm's minimum (design B, B1).

Runs once, after the compose and before the audit, so every request it adds
is audited with the rest. The drafter is told the set's count and its target
and given the set as drafted; it returns only the new requests, each a label
and its text. Code puts them after the set's last request and numbers the set
again (``set_items.append``). The pass is told never to pad: the record
decides how many more there are, and none is a right answer. Whatever it
returns, the set files: a set still short of the minimum is said in the
attorney notes, never held.
"""

from __future__ import annotations

import hashlib
from typing import Any

from . import set_items

STAGE = "compose"
#: Each variant's label words, as a label line prints them.
LABEL = {
    "special_interrogatories": "SPECIAL INTERROGATORY",
    "requests_for_admission": "REQUEST FOR ADMISSION",
    "requests_for_production": "REQUEST FOR PRODUCTION",
}


def prompt(doc: str, variant: str, count: int, bounds: tuple[int | None, int | None]) -> str:
    """The pass's instruction: the count, the target, the set, and the rules
    the new requests keep."""
    lo, hi = bounds
    one, plural = set_items.NAMES[variant]
    label = LABEL[variant]
    room = f"between {(lo or count) - count} and {hi - count}" if hi else f"up to {(lo or count) - count}"
    return (
        f"MORE REQUESTS. The set below has {count} {plural}; the firm's number for this set is "
        + (f"{lo} to {hi}" if lo and hi else f"at least {lo}")
        + f". Write {room} further {plural}, only where the record digest supports each one. Never write a "
        f"{one} the record does not support to reach the number: fewer, or none, is right when the record "
        "supports no more. Each new request asks for something no request in the set already asks for, uses "
        "only terms the set's DEFINITIONS define, and follows the set's own form. Output ONLY the new requests, "
        f'each as its own label line ("{label} NO. {count + 1}") followed by its text, numbered from '
        f"{count + 1}; no heading, no other text, no attorney notes.\n\n=== THE SET AS DRAFTED ===\n" + doc
    )


def more(
    drafter: Any, doc: str, variant: str, count: int, bounds: tuple[int | None, int | None], digest: str
) -> tuple[str, int]:
    """One pass: (the set with the new requests in place, how many were added).
    ``drafter`` is the unit's ``compose.Drafter`` (its house style, skeleton,
    firm prompt and exemplars, and the request with the unit's instruction)."""
    user = (
        drafter.context
        + "\n\n---\n\nTHE RECORD DIGEST (every fact you use must trace to a citation here):\n\n"
        + digest
        + "\n\n---\n\n"
        + prompt(doc, variant, count, bounds)
    )
    cid = "more-" + hashlib.sha256(user.encode("utf-8")).hexdigest()[:8]
    r = drafter._call(STAGE, drafter.compose_system(), user, cid)
    answer = str(r.text).split("=== ATTORNEY NOTES ===", 1)[0]
    return set_items.append(doc, variant, answer)
