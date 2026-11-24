"""The firm's rules applied by code (design C, "S: the shared layer").

* ``types``: the values the stage takes and returns;
* ``book``: the firm's phrase book (the optional ``phrase_book`` section);
* ``reading``: the job's rules read into clauses, verified against their words;
* ``parts``: a response read into its sentences;
* ``grid``: which item each judged clause applies to, per document;
* ``makers``: code writes the firm's sentences into a response;
* ``written``: a ground the book has no sentence for, written for the request;
* ``stage``: one document through the rules stage;
* ``hook``: the lanes' call sites, each a no-op until the firm switches it on.

Rules stay the seat's recorded words (``firm_wording``); nothing here keeps a
second copy of them.
"""

from __future__ import annotations

# Submodules are imported by name where used (``from .rulebook import stage``), never here:
# ``firm.py`` imports ``book`` to validate the firm file, and the fleet CLI runs that
# validator in a bare interpreter with only pyyaml (bin/lib/fleet/lane_validate.py).
