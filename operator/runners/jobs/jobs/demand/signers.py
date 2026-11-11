"""How the matter's responsible attorney signs a demand: the firm's attorney
roster when one is installed, else the demand inputs' signer map, as before.

The roster (``operator_connector_sdk.library.roster``) says WHO is an attorney
and how each signs a demand (``signs.demand``: signer, title, initials). The
demand inputs keep saying which FORMAT an attorney's demands are written in
(``demand-firm.yaml`` ``attorneys.<name>.format``, read by
``DemandFirm.variants_for``), so the format choice does not move.

* ``absent`` (no roster installed): ``DemandFirm.attorney_for``, unchanged.
* ``ok``: the roster's demand signature for the attorney the matter names, with
  the inputs' ``format`` for them when it names one. Someone the roster does not
  list as an attorney, or one it authors no demand signature for, gets None,
  which holds the demand exactly as an unmapped attorney does today.
* ``unusable``: the demand fails for SMD before anything is spent.

When both lists name an attorney and say different things, the roster wins and
the seat check ``attorney-roster-agrees`` (``jobs.attorneys.agree``) pages SMD.

The row carries ``bar_number`` when one is authored (the roster's, else the
demand inputs' row): the crabtree layout prints "<signer>, (SBN n)".

``who_signs`` is WHO: the attorney the request names (``terms.named_attorney``),
else the matter's responsible attorney.
"""

from __future__ import annotations

from typing import Any

from operator_connector_sdk.library import roster as roster_mod

from . import terms
from .outcome import DemandFailed

_KEYS = ("signer", "title", "initials")


def signer_for(firm: Any, who: str | None, roster: roster_mod.Roster) -> dict[str, str] | None:
    """The signature row ``firm.select`` takes for the matter's attorney ``who``."""
    if roster.status == roster_mod.ABSENT:
        return firm.attorney_for(who)
    if roster.status != roster_mod.OK:
        raise DemandFailed(f"the firm's attorney roster could not be read ({roster.reason}). Nothing was spent.")
    person = roster.by_name(who)
    sig = roster.signature(person, roster_mod.DEMAND) if person is not None and person.attorney else None
    if sig is None:
        return None
    out = {k: sig[k] for k in _KEYS}
    row = firm.attorney_for(who) or {}
    if row.get("format"):
        out["format"] = row["format"]
    bar = person.bar_number or row.get("bar_number") or ""
    if bar:
        out["bar_number"] = bar
    # The script line prints the name the attorney signs letters with ("Craig
    # F. Ashton" over the printed "Craig Fontaine Ashton", the 11-10 letter).
    script = (roster.signature(person, roster_mod.LETTERS) or {}).get("name") or row.get("script") or ""
    if script:
        out["script"] = script
    return out


def _names(firm: Any, roster: roster_mod.Roster) -> dict[str, list[str]]:
    """Each attorney who can sign a demand, by staff name, with every full
    form of their name the firm writes."""
    if roster.status == roster_mod.OK:
        out = {}
        for p in roster.attorneys:
            sig = roster.signature(p, roster_mod.DEMAND)
            if sig is None:
                continue
            letters = roster.signature(p, roster_mod.LETTERS) or {}
            out[p.name] = [p.name, sig["signer"], *([letters["name"]] if letters.get("name") else [])]
        return out
    return {name: [name, row["signer"]] for name, row in firm.data["attorneys"].items()}


def who_signs(firm: Any, responsible: str | None, request: str, roster: roster_mod.Roster) -> tuple[str | None, bool]:
    """``(the attorney whose demand this is, named by the request)``: the one
    attorney the request names, else the matter's responsible attorney."""
    named = terms.named_attorney(request, _names(firm, roster)) if roster.status != roster_mod.UNUSABLE else None
    return (named, True) if named else (responsible, False)


__all__ = ["signer_for", "who_signs"]
