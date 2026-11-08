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
"""

from __future__ import annotations

from typing import Any

from operator_connector_sdk.library import roster as roster_mod

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
    fmt = (firm.attorney_for(who) or {}).get("format")
    if fmt:
        out["format"] = fmt
    return out


__all__ = ["signer_for"]
