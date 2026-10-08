"""What a litigation job ends with: the coded reason, the exceptions that carry
it, and the verdict the lane reads.

* ``held``: a PERSON must decide before the firm sees a list (a gate refused
  what the reads produced, an unexplained divergence from the last list, the
  filing folder is not ours to write). Final for this job; nothing filed.
* ``failed``: OUR machinery (a limit, a read that did not finish, a fetch a
  retry can fix, a short read-back). Resumable; SMD is alerted.

The verdict is ONE JSON object, the only line on stdout, and the same JSON in
``verdict.json`` (the lane prefers the file). Exit 0 delivered, 1 held,
2 failed.
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
    "inventory_unreadable",
    "fetch_unfinished",
    "read_incomplete",
    "gate_refused",
    "leak_found",
    "parity_hold",
    "filing_refused",
    "stage_unfinished",
    "limit",
    "no_verdict",
    "unexpected",
)
REASON = re.compile(r"^(" + "|".join(REASON_CODES) + r"): ")
EXIT = {"delivered": 0, "held": 1, "failed": 2}


class LitigationHold(RuntimeError):
    """A person decides: held, nothing filed."""


class LitigationFailed(RuntimeError):
    """Our machinery: failed, resumable."""


@dataclass
class Verdict:
    verdict: str
    stage: str
    cents: int = 0
    matters_total: int = 0
    matters_reread: int = 0
    flags_new: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)
    folder_id: str = ""
    reason: str | None = None

    def to_json(self) -> str:
        """The interface's verdict. ``reason`` rides only when the run did not
        deliver (the lane records it on the ledger row, never on the wake)."""
        d: dict[str, Any] = {
            "verdict": self.verdict,
            "stage": self.stage,
            "cents": int(self.cents),
            "matters_total": int(self.matters_total),
            "matters_reread": int(self.matters_reread),
            "flags_new": int(self.flags_new),
            "files": list(self.files),
            "folder_id": self.folder_id,
        }
        if self.reason and self.verdict != "delivered":
            d["reason"] = self.reason[:500]
        return json.dumps(d)

    @property
    def exit_code(self) -> int:
        return EXIT.get(self.verdict, 2)
