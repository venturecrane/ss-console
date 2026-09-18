"""Is a paged vendor listing provably the WHOLE set?

The rule both functions share, and `contact_listing_is_complete` below is the
older of the two: **a truncated listing is byte-identical
to a complete one**, so anything short of proof is False. The vendor sends no
total and no "more" flag.

Why the file listing needs it (ss#2834). The medical-chronology routine computes
an update's document set as "the matter's current listing MINUS what the
delivery covered". A short listing silently shrinks that set, so a record past
the cap is never submitted, never read, and never appears in the chronology --
with no refusal, no hold, and nothing in the reply. A live matter returns
exactly 500 files against a 500 default today, which is what moved this from
theoretical to live.
"""

from __future__ import annotations

from typing import Any


def with_listing_completeness(resp: Any, *, offset: int, limit: int) -> Any:
    """Stamp `listingComplete` onto a listing response and return it.

    Three ways to be unprovable, each a real case rather than defensive
    padding:

    * a non-zero `offset` -- one page of a set says nothing about the set.
    * a page that came back exactly full -- indistinguishable from a truncated
      one, which is precisely the case that must not be trusted.
    * a malformed envelope -- no `value` list to count.
    """
    if not isinstance(resp, dict):
        return resp
    items = resp.get("value")
    resp["listingComplete"] = not offset and isinstance(items, list) and len(items) < limit
    return resp


def contact_listing_is_complete(resp: dict, *, offset: int, limit: int, narrowed: bool) -> bool:
    """Is a contact-filtered ``list_matters`` response provably the WHOLE set of
    matters this contact is a party to? (ss#2264, the contact axis.)

    Membership has two axes and only the matter axis was implemented. ``parties``
    + ``parties_complete`` close a MATTER's own party list, so "this recipient is
    not among them" proves non-membership. The other direction proves it just as
    validly: if the full list of matters a PERSON is party to is known, and the
    cited matter is not in it, the person is not a party. That axis is keyed off
    the read the reply lane actually performs — ``list_matters`` fires on 34 of 86
    reply turns against ``get_matter``'s 8 (vfy_01KZRRWG2WZKTRNZQRDEX494GZ) — so
    it is where the gate can actually conclude something.

    The fail-safe rule is the one ``_attach_parties`` is built on, applied to this
    shape: a TRUNCATED listing is byte-identical to a complete one, so anything
    short of proof is ``False``, which the binding must read as *membership
    unresolved* and never as *not a party*. Four ways to be unprovable:

    * ``narrowed`` — any ``status`` / ``is_lead`` / ``matter_type_id`` / ``search``
      / ``updated_since`` filter. This is the subtle one and the reason the flag
      is computed at the call site rather than inferred here: a listing filtered
      to ``status=Open`` legitimately omits the CLOSED matter the recipient is a
      party to, so an absence in it would manufacture a mismatch against a real
      client. A narrowed listing is not a smaller answer to the same question; it
      is an answer to a different one.
    * a non-zero ``offset`` — one page of a set says nothing about the set.
    * a full page (``len(items) >= limit``) — indistinguishable from a truncated
      one, which is precisely the case that must not be trusted.
    * a malformed envelope — no ``value`` list to count.
    """
    if narrowed or offset:
        return False
    items = resp.get("value")
    if not isinstance(items, list):
        return False
    return len(items) < limit
