"""A document made IN a firm's own Word file: its layout, our words, proven.

Every lane that renders on our own base with hand-typed layout keys gets the
attorney's corrections to that layout, four of them in one day on 2026-11-04.
This package is the general answer: the firm's file is the base, every part of
it stays by its bytes, and only named slots change.

::

    base = FirmBase.load("mediation_brief", source, adopted_bytes)
    fill = open(base)
    fill.put("attorney", lines)
    fill.put("body", [("body.h1", runs), ("body.chart2", Rows(...)), ...])
    blob, proof = fill.save()
    proof.require()   # firm_base_lost on any non-empty list

Slots are marked in the adopted file (``base.py``), made by ``adopt.py`` plus the
document class's own reading of where its parts are. The base is the attorney's most
recent accepted save of the class, else a firm-authored past file; an Operator render
he did not accept is never adopted (``adopt.py``).
"""

from __future__ import annotations

from .adopt import Candidate, choose, clean_package, may_adopt, operator_authored
from .base import Fill, FirmBase, FirmBaseLost, FirmBaseUnfit, Line, Rows, Slot, Source, open
from .proof import Proof, layout_diff, prove

__all__ = [
    "Candidate",
    "Fill",
    "FirmBase",
    "FirmBaseLost",
    "FirmBaseUnfit",
    "Line",
    "Proof",
    "Rows",
    "Slot",
    "Source",
    "choose",
    "clean_package",
    "layout_diff",
    "may_adopt",
    "open",
    "operator_authored",
    "prove",
]
