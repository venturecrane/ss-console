"""Setting up one kind of document from the firm's own files (design 2026-11-06,
piece 3): an administrator says "set up our mediation briefs" (or memos), for the
firm or for one of its attorneys, and the job finds the firm's own finished
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

#: The kinds a firm can set up this way: the two whose firm-base plans exist
#: (``drafting/brief_base.py``, ``drafting/memo_base.py``) and whose hunt rules
#: exist (``firmbase/hunt.RULES``).
KINDS = ("mediation_brief", "memo")

__all__ = ["KINDS"]
