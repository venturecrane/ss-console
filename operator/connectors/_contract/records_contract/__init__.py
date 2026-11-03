"""The matter-records contract (ADR 0102): one copy, imported by the overlay and the records lane.

* :mod:`records_contract.review_row`: the SMD review row, its digest, the root decision file.
* :mod:`records_contract.batches`: how staged files split into replies.
* :mod:`records_contract.facts`: the facts block rendered from the page finder's results.

Stdlib only, by rule (see README.md).
"""

from records_contract import batches, facts, review_row

__all__ = ["batches", "facts", "review_row"]
