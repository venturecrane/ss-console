"""Whose instructions the drafting inputs' style file is (design 2026-11-06,
piece 8: "an attorney's instructions are scoped to that attorney").

WHY. The drafting lane has one firm-wide style file (``drafting-firm.yaml``
``style``), sent as THE HOUSE STYLE at the top of every compose. On the one seat
with drafting live it is one attorney's own written instructions, sent by the
firm as his, and it reaches every drafting request whoever's name is on the
document. An attorney's instructions never become the firm's base by default.

THE SCOPE IS AUTHORED, beside the file it scopes: ``style_attorney: <name>``
in the drafting inputs, the case system's name for that attorney (the firm's
attorney roster's entry when one is installed; ``jobs.attorneys``). Absent, the
style file is the firm's, exactly as before.

IT IS SELECTED THE WAY EVERY WORDING RULE IS. The style file becomes one rule
scoped to that attorney and to the drafting kinds (:func:`rule`), and the
wording-rules store's own selection (``wording_rules_store.applicable``, by
kind, attorney and reader) decides whether a job's document is in its scope;
``firm_wording.freeze`` records the answer with the job's other rules, so a
resumed job keeps it. A document in his name sends exactly today's
instructions. A document in another attorney's name sends today's minus THE
HOUSE STYLE block and nothing else (``compose.Drafter.house_style``).

DARK. Behind the seat's ``logic_in_code.wording_rules`` switch (the switch that
makes a firm's rules take effect where documents are made), and only on a seat
whose wording store is installed: off, or no store, or no ``style_attorney``,
every request gets the style file, as today.
"""

from __future__ import annotations

import hashlib
from typing import Any

KEY = "style_attorney"
RULE_ID = "drafting-style"
#: The kinds the style file reaches: the drafting lane's (``drafting/job.py``).
KINDS = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")


def validate(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{KEY}: expected the case system's name for the attorney whose instructions the style file is"]
    return []


def rule(firm: Any) -> dict[str, Any] | None:
    """The style file as one rule scoped to its attorney, or None when the
    drafting inputs say it is the firm's (no ``style_attorney``)."""
    who = firm.style_attorney
    if not who:
        return None
    digest = hashlib.sha256(firm.style.encode("utf-8")).hexdigest()
    return {
        "rule_id": RULE_ID,
        "text": f"the drafting inputs' style file (sha256 {digest})",
        "wording": [f"the drafting inputs' style file (sha256 {digest})"],
        "doc_kinds": list(KINDS),
        "attorney": who,
        "reader": None,
    }


__all__ = ["KEY", "KINDS", "RULE_ID", "rule", "validate"]
