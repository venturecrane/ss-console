"""Proven delivery: what every lane must show before a job is recorded delivered.

WHY (2026-10-13). "Delivered" was the job's own claim. The daemon recorded the
runner's verdict as it stood, a dry run counted as a delivery, and three wrong
outputs were marked delivered with no alert: a false coverage report filed
instead of a demand, a wrong coverage report emailed to the requester, and a
false CCP 2030.050 declaration handed to an attorney. Each was found by SMD
reading the work afterwards, never by the system.

The rule this package carries, for every lane:

* **Reviewed before filing.** Code checks the content against its own sources
  (``checks``), and where judgement is needed a model reads it with an
  adversarial prompt of its own (``review``). A MUST-FIX holds the job: nothing
  is filed, SMD is alerted, and the hold is resumable two ways (``redo`` the
  review after a fix, or an SMD release that files with the findings in the
  attorney notes). A hold is never a dead end.
* **Read back after filing.** The far end is re-read by file id, its size and
  its sha256 compared with the bytes built (``readback``). Only then is the
  job delivered.
* **The daemon checks the claim** (``gate``): a delivered outcome must carry a
  ``proof`` block saying both of the above happened; one that does not is
  recorded ``failed`` (resumable) and SMD is alerted (``alert``).
"""

from __future__ import annotations

PROOF_KEY = "proof"
#: The review statuses a delivered outcome may carry. ``code_only``: the lane's
#: output is data, not prose (a negotiation row), and code settled it.
REVIEW_PASSED = frozenset({"passed", "released", "code_only"})

__all__ = ["PROOF_KEY", "REVIEW_PASSED"]
