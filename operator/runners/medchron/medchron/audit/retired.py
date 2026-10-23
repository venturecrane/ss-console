"""Controls SMD retired as badly chosen (``medchron.audit_decide``).

A control is only a control when its page cannot carry the claim. A person
who finds a SUPPORTED control GENUINE (the page really states the claim, e.g.
a diagnosis index) records it here; the round report and the final verdict
gate leave those rows out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FILE = "audit-retired-controls.json"


def path(out: Path) -> Path:
    return out / FILE


def live_controls(rows: list[dict[str, Any]], out: Path) -> list[dict[str, Any]]:
    """Every control row except the ones SMD retired."""
    gone = keys(out)
    return [r for r in rows if str(r.get("kind", "")).startswith("control") and r.get("key") not in gone]


def keys(out: Path) -> set[str]:
    p = path(out)
    if not p.is_file():
        return set()
    body = json.loads(p.read_text(encoding="utf-8"))
    return {str(r["key"]) for r in body.get("retired") or [] if r.get("key") and r.get("reason")}
