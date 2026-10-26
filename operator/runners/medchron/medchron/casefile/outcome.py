"""What a case file job ends with: the coded reason, the exceptions that carry
it, and the ONE JSON verdict object the lane reads.

* ``held``: a person decides before the firm sees anything (no matter in the
  scope, a name the staff list cannot settle, every matter held, a filing
  folder holding a file this run did not send, nothing approved to apply).
* ``failed``: our machinery (a limit, a fetch a retry can fix, a short
  read-back, a stage that did not finish). Resumable; SMD is alerted.
* ``delivered``: the File Status of every matter with something new is filed
  and read back, the caseload workbook is filed and read back, the proposed
  lines are numbered. Or, for an apply job, every approved line has an outcome.

Per-matter problems never hold the job: a matter whose read or record did not
pass is held ALONE (``matters_held``) and named in the workbook as not reviewed
this run. Only when every matter in scope is held does the job hold
(``all_matters_held``).

The verdict carries the money the way the price is measured: cents and token
counts per stage and per matter (input, output, cache read, cache write).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

REASON_CODES = (
    "envelope_invalid",
    "config_missing",
    "scope_empty",
    "scope_unresolved",
    "inventory_unreadable",
    "fetch_unfinished",
    "disk_short",
    "read_incomplete",
    "all_matters_held",
    "filing_refused",
    "readback_mismatch",
    "stage_unfinished",
    "limit",
    "no_verdict",
    "unexpected",
    "rehearsal",
    "apply_refused",
    "nothing_approved",
    # The library matter named by number is not exactly one Open matter.
    "library_unresolved",
)
REASON = re.compile(r"^(" + "|".join(REASON_CODES) + r"): ")
EXIT = {"delivered": 0, "held": 1, "failed": 2}


class CasefileHold(RuntimeError):
    """A person decides: held, nothing more filed."""


class CasefileFailed(RuntimeError):
    """Our machinery: failed, resumable."""


@dataclass
class Verdict:
    verdict: str
    stage: str
    cents: int = 0
    mode: str = "full"
    matters_total: int = 0
    matters_read: int = 0
    matters_current: int = 0
    matters_held: int = 0
    lines_proposed: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)
    folder_id: str = ""
    reason: str | None = None
    proof: dict[str, Any] | None = None
    #: The numbered proposal lines (``propose.py``): ids and hashes only.
    lines: list[dict[str, Any]] = field(default_factory=list)
    #: An apply job's per-line outcomes and every write it made.
    line_outcomes: list[dict[str, Any]] = field(default_factory=list)
    writes: list[dict[str, Any]] = field(default_factory=list)
    #: An apply that did not deliver: the line numbers with a journaled write
    #: intent or outcome, which the broker never hands to another apply.
    intents: list[int] = field(default_factory=list)
    #: ``{"stages": {stage: usage}, "matters": {matter_id: usage}}`` (``metrics.py``).
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "verdict": self.verdict,
            "stage": self.stage,
            "mode": self.mode,
            "cents": int(self.cents),
            "matters_total": int(self.matters_total),
            "matters_read": int(self.matters_read),
            "matters_current": int(self.matters_current),
            "matters_held": int(self.matters_held),
            "lines_proposed": int(self.lines_proposed),
            "files": list(self.files),
            "folder_id": self.folder_id,
        }
        for key in ("lines", "line_outcomes", "writes", "intents"):
            if getattr(self, key):
                d[key] = list(getattr(self, key))
        if self.metrics:
            d["metrics"] = dict(self.metrics)
        if self.reason and self.verdict != "delivered":
            d["reason"] = self.reason[:500]
        if self.proof is not None and self.verdict == "delivered":
            d["proof"] = dict(self.proof)
        return json.dumps(d)

    @property
    def exit_code(self) -> int:
        return EXIT.get(self.verdict, 2)


__all__ = ["REASON", "REASON_CODES", "CasefileFailed", "CasefileHold", "Verdict"]
