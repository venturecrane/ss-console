"""Setting up the firm's document library from its own files (design 2026-11-06,
piece 3): an administrator says "set up the document library" (every kind at
once), or "set up our discovery responses" (one kind), for the firm or for one
of its attorneys, and the job finds the firm's own finished
documents of that kind across its matters and names the best candidates by file.

THIS PIECE (3a) READS AND COUNTS. It never writes to a library, a ledger or a
vault, and it never sends anything itself: what the firm reads is rendered by the
broker from the job's row (``workspace_broker/library_setup_wording.py``), and
only when the seat's ``logic_in_code.library_setup`` switch is on. The yes that
makes the library file is piece 3b.

* ``roster``: which attorneys a request may name, and the name each is filed
  under (the third partial attorney list; the unified roster replaces it).
* ``job``: the run, from ``job.json`` to its verdict.
* ``cli``: ``jobs library-setup <job_dir>``.
"""

from __future__ import annotations

#: The kinds a firm can set up this way: every kind the drafting job makes
#: (``drafting/firm.py`` CLASSES), in the document-kind registry's order
#: (``operator/contracts/document-kinds.yaml``). A brief or a memo is made with
#: its marked plan (``drafting/brief_base.py``, ``drafting/memo_base.py``) when
#: the firm's document fits it, else as a styles base (``styles_make.py``); the
#: other three are always styles bases. A demand letter is not here: the demand
#: job already writes in the firm's own demand (its prototypes file).
KINDS = ("discovery_set", "discovery_response", "mediation_brief", "memo", "depo_outline")
#: The kinds with a marked plan.
MARKED_KINDS = ("mediation_brief", "memo")
#: ``setup_kind`` for "set up the document library": one hunt per kind, one
#: list per kind in one reply.
LIBRARY = "library"
#: The library file contracts this job makes (``operator_connector_sdk.library``).
MARKED, STYLES = "marked", "styles"

__all__ = ["KINDS", "LIBRARY", "MARKED", "MARKED_KINDS", "STYLES"]
