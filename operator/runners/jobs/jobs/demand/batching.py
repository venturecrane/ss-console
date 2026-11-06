"""Which paid stages of a demand job go through the provider's batch queue,
and what one batch is projected to cost.

The queue is half the price and slower: a round takes as long as the queue
takes, usually under an hour and at most a day. On 2026-09-24 every summary
fell into it by accident and a four-demand batch took five hours, so the lane
has run live ever since, and it still does unless the firm says otherwise.

A firm says otherwise with two levers (``demand-firm.yaml``):

* ``levers.batch_stages``: the stages to queue, from ``firm.BATCHABLE_STAGES``
  (transcription and the two summary stages: one independent call per page or
  chunk). The drafting stages are never queued.
* ``levers.batch_over_usd``: queue them only on a job whose pre-paid estimate
  is at least this many dollars. An ordinary demand stays as fast as it was; a
  file of thousands of pages costs half. Absent, every job is queued.

The decision is a comparison of two authored numbers, made again on every
attempt from the same preflight, so a resume takes the road the first attempt
took and finds the queue's record where that attempt left it.
"""

from __future__ import annotations

from typing import Any


def stages(levers: dict[str, Any], estimate_usd: float) -> frozenset[str]:
    """The stages to queue on a job with this pre-paid estimate."""
    named = frozenset(levers.get("batch_stages") or [])
    if not named or estimate_usd < float(levers.get("batch_over_usd") or 0):
        return frozenset()
    return named


def projected_usd(budget: dict[str, Any], batch_multiplier: float, stage: str, items: int, chars: int) -> float:
    """One batch's projected cost: the firm's own estimate rate for the stage,
    at the batch price. Checked against the cap and the month's budget before
    the batch is submitted, because nothing checks between its items."""
    full = {
        "vision": items * float(budget["usd_per_scanned_page"]),
        "digest": chars / 1e6 * float(budget["usd_per_million_chars"]),
        "condense": chars / 1e6 * float(budget["usd_per_million_condense_chars"]),
    }.get(stage, 0.0)
    return full * batch_multiplier
