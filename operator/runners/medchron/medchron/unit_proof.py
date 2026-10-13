"""The chronology driver's half of proven delivery (2026-10-13): the proof
block a delivered unit carries to the daemon, and the reasons it pages on.

Kept out of ``driver.py`` (which sits at the module-size ceiling) and apart
from ``proof/`` (lane-agnostic): this module knows the chronology's own files,
``runs/<unit>/delivery.json`` rows as ``stages/upload._prove`` leaves them, the
unit's ``review.json`` and its usage ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .proof import review as review_mod

#: The reasons the daemon pages SMD on (proof/watch.py). A stage that ends on
#: one says so in its own log line, and the driver carries that line as the
#: reason verbatim: the exit_map's fixed text would bury the prefix the page
#: keys on, and the findings with it.
PAGED_PREFIXES = ("review_held:", "readback_mismatch:")


def paged_line(lines: list[str]) -> str | None:
    """The stage's last line naming a paged reason, verbatim (capped), or None."""
    for ln in reversed(lines):
        if ln.startswith(PAGED_PREFIXES):
            return ln[:480]
    return None


def block(rd: Path, files: list[dict[str, Any]], pricing: Any) -> dict[str, Any]:
    """The proof block for a delivered unit. ``confirmed`` only when EVERY
    delivery row was proven by hash: a package delivered by a runner that
    predates the hash read-back says ``unproven``, and the daemon fails it
    rather than believe it."""
    proven = [f for f in files if f.get("proven") and f.get("file_id")]
    rec = review_mod.load(rd)
    return {
        "readback": "confirmed" if files and len(proven) == len(files) else "unproven",
        "files": [
            {"name": f["name"], "file_id": str(f["file_id"]), "size": int(f.get("bytes") or 0), "sha256": f["sha256"]}
            for f in proven
        ],
        "review": review_mod.status(rec),
        "review_cents": review_mod.stage_cents(unit_by_stage(rd, pricing)),
        "review_model": str((rec or {}).get("model") or ""),
    }


def unit_by_stage(rd: Path, pricing: Any) -> dict[str, float]:
    """Spend by stage off ONE unit's ledger. ``Budget.by_stage`` sums every
    unit's, which on a joint matter would book each client's review cost on
    both outcomes (and the daemon adds them)."""
    out: dict[str, float] = {}
    ledger = rd / "usage-ledger.jsonl"
    if not ledger.is_file():
        return out
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        stage = str(row.get("stage") or "?")
        out[stage] = out.get(stage, 0.0) + pricing.price_row(row)
    return out


__all__ = ["PAGED_PREFIXES", "block", "paged_line", "unit_by_stage"]
