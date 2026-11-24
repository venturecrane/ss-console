"""The firm's rules applied by code (design C, "S: the shared layer").

* ``types``: the values the stage takes and returns;
* ``book``: the firm's phrase book (the optional ``phrase_book`` section);
* ``stage``: one document through the rules stage;
* ``parts``: a response read into its sentences;
* ``hook``: the lanes' call sites, each a no-op until the firm switches it on.

Rules stay the seat's recorded words (``firm_wording``); nothing here keeps a
second copy of them.
"""

from __future__ import annotations

from . import book, hook, parts, stage, types

__all__ = ["book", "hook", "parts", "stage", "types"]
