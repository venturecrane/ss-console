"""What a records job ends with: the coded reason, the exceptions that carry
it, and the ONE JSON verdict object the lane reads.

* ``delivered``: every record asked for is accounted for under one heading of
  the facts block (attached, not found, not established), the cuts are on the
  job's disk with their hashes, and nothing held. The reply still waits in the
  SMD review row the lane writes; the gateway sends it.
* ``held``: the reply is words only and SMD finishes it (a cap, an item that
  names someone else, a file that could not be cut, a record the request does
  not tie to a file).
* ``failed``: our machinery before any reply could be composed (the envelope,
  the firm inputs, an unexpected error). Resumable.
* ``dry_run``: a proof run that reached its reply; ``would_be`` says how the
  live job would end (``held`` or ``delivered``), with the hold's reason. A
  capped run that wrote its row is the designed outcome, so it judges passed.

THE VERDICT CARRIES NO CLIENT TEXT. Ids, counts, sha256s and coded reasons
only: no file name, no page text, no request words. The reply's words live in
``data/facts.json`` in the job dir, which only root and the job's uid read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Hold reasons (the plan's list) and the failure codes.
HOLD_CODES = (
    "per_job_cap_usd",
    "monthly_budget_usd",
    "finder_failed",
    "cut_refused",
    "scope_unresolved",
    "match_unsettled",
    "for_another_person",
)
FAIL_CODES = ("envelope_invalid", "config_missing", "unexpected", "no_verdict", "rehearsal")
REASON_CODES = HOLD_CODES + FAIL_CODES
REASON = re.compile(r"^(" + "|".join(REASON_CODES) + r")(: |$)")
EXIT = {"delivered": 0, "held": 1, "dry_run": 0, "failed": 2}
#: Each record's outcome token (the broker's ``records_lines.outcome``).
OUTCOMES = ("attached", "not_found", "not_established", "unplaced", "unresolved", "held")


class RecordsHold(RuntimeError):
    """A person finishes it: the reply holds as words only. The message starts with its code."""


class RecordsFailed(RuntimeError):
    """Our machinery: failed, resumable."""


@dataclass
class Verdict:
    verdict: str
    stage: str
    cents: int = 0
    reason: str | None = None
    #: ``[{n, outcome, files}]``: one per record asked for (``OUTCOMES``).
    items: list[dict[str, Any]] = field(default_factory=list)
    #: ``[{n, k, file_id, pages, page_count, sha256, size}]``: the cuts on disk.
    cuts: list[dict[str, Any]] = field(default_factory=list)
    proof: dict[str, Any] | None = None
    #: A proof run's real ending (``held`` or ``delivered``) beside its ``dry_run``.
    would_be: str | None = None

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "verdict": self.verdict,
            "stage": self.stage,
            "cents": int(self.cents),
            "items": [
                {"n": int(i["n"]), "outcome": str(i["outcome"]), "files": int(i.get("files") or 0)} for i in self.items
            ],
            "cuts": [
                {k: c[k] for k in ("n", "k", "file_id", "pages", "page_count", "sha256", "size") if k in c}
                for c in self.cuts
            ],
        }
        if self.reason and self.verdict != "delivered":
            d["reason"] = self.reason[:500]
        if self.would_be:
            d["would_be"] = self.would_be
        if self.proof is not None and self.verdict == "delivered":
            d["proof"] = dict(self.proof)
        return json.dumps(d, sort_keys=True)

    @property
    def exit_code(self) -> int:
        return EXIT.get(self.verdict, 2)


#: The outcomes that show the run searched and settled a record.
SETTLED = ("attached", "not_found", "not_established")


def proof_ending(hold: str | None, items: list[dict[str, Any]]) -> str | None:
    """A proof run's ``would_be`` (``held`` or ``delivered``), or None when it
    never reached a reply worth reviewing: that run stays ``held`` and judges
    failed. A reviewable run settled at least one record (attached, not found,
    not established). Never reviewable: the finder failed, a cut was refused,
    or the match did not answer (review of #3725: with the match model down on
    every call, both records held and the proof was accepted)."""
    h = hold or ""
    if h.startswith(("finder_failed", "cut_refused")) or "the match did not answer" in h:
        return None
    if not any(i.get("outcome") in SETTLED for i in items):
        return None
    return "delivered" if hold is None else "held"


__all__ = [
    "FAIL_CODES",
    "HOLD_CODES",
    "OUTCOMES",
    "REASON",
    "REASON_CODES",
    "RecordsFailed",
    "RecordsHold",
    "Verdict",
    "proof_ending",
]
