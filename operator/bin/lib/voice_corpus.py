"""The audience labels a voice corpus is fetched and surveyed under.

What remains of the voice-corpus tracer after the sample-transform mechanism
was removed (ss ADR 0083 decision 6, 2026-11-07). The ingest into vault
samples, its structural differ and its leak invariant went with it. The
read-in-place bridge (``bin/voice-fetch-corpus.py``) and its survey step
(``bin/voice-survey-corpus.py``) stay, repointed at distillation, and still
label each fetched document with the reader it was written for.

The labels are a fixed vocabulary in code. A seat's ``customer.yaml`` no longer
authors them: the block that did is refused by both validators.
"""

from __future__ import annotations

from pathlib import Path

#: The audience labels a fetched document may carry: the four base labels and
#: ``adjuster``, which both firm seats authored when the block existed.
BASE_COHORTS = frozenset({"client", "adjuster", "opposing-counsel", "court", "internal"})


def load_cohort_vocabulary(customer_yaml: str | Path | None = None) -> frozenset[str]:
    """The audience labels: always :data:`BASE_COHORTS`. ``customer_yaml`` is
    accepted and ignored, so the bridge's ``--customer-yaml`` flag keeps working."""
    del customer_yaml
    return BASE_COHORTS


__all__ = ["BASE_COHORTS", "load_cohort_vocabulary"]
