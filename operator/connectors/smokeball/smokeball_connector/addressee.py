"""Who a letter is written for: the role its addressee holds on the matter.

A letter's reader is its addressee's (the document-kind registry's ``letter``
``reader: addressee``; ``operator_connector_sdk.library.readers``). It is read
from the matter's contact roles in code, the same records the firm's forms
already read (``form_letters.py``: the insurer, the adjuster and the client):

* the client role's own contact: ``client``;
* a relationship named Insurer or Adjuster, on either side: ``adjuster``;
* the other side's role or relationship whose name says counsel (attorney,
  counsel, lawyer, law firm; the demand lane's ``defense_counsel`` test):
  ``opposing_counsel``.

Anything else, a vendor included, is not distinguished by the role data, so the
answer is None and the letter is carried by the firm's base way of writing,
which its record says (``readers.record`` ``reader_from: addressee_unknown``).
Never a guess from a name.

No caller passes an addressee yet: the chat tool takes none, and the letter
piece of the document-library design (piece 6, "reader from the addressee")
wires it. Until then every letter's record says its addressee is unknown.
"""

from __future__ import annotations

import re
from typing import Any

from .form_letter_facts import Parties, _contact_of

_COUNSEL = re.compile(r"(?i)\b(attorney|counsel|lawyer|law firm)\b")
_CARRIER = frozenset({"insurer", "adjuster"})


def _named(record: dict[str, Any]) -> str:
    return " ".join(str(record.get("name") or "").split()).casefold()


def reader_of(parties: Parties, contact_id: str | None) -> tuple[str | None, str]:
    """(the reader, how the roles said it) for a letter to ``contact_id``; (None,
    why) when the roles do not distinguish the addressee."""
    if not contact_id:
        return None, "no addressee was named"
    found: set[str] = set()
    for role in parties.roles:
        rels = [r for r in role.get("relationships") or [] if isinstance(r, dict)]
        if role.get("isClient") is True and _contact_of(role) == contact_id:
            found.add("client")
        for rel in rels:
            if _contact_of(rel) == contact_id and _named(rel) in _CARRIER:
                found.add("adjuster")
        if role.get("isOtherSide") is True:
            for rec in (role, *rels):
                if _contact_of(rec) == contact_id and _COUNSEL.search(str(rec.get("name") or "")):
                    found.add("opposing_counsel")
    if len(found) == 1:
        reader = next(iter(found))
        return reader, f"the addressee holds the matter's {reader.replace('_', ' ')} role"
    if found:
        return None, "the addressee holds more than one role on the matter"
    return None, "the matter's roles do not say who the addressee is"


__all__ = ["reader_of"]
