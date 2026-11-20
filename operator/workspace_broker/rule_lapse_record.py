"""The record of a person's own rule that lapsed with no letter owed (2026-11-19).

``PendingRuleStore.sweep`` lapses a rule nobody else had to approve (kind
``rule``, ``for_admin`` 0) silently: the lapse letter says no administrator
answered, which was false for the person who stated it. ``RULE_LAPSED`` is the
only record that a rule died unanswered, and it is normally written when that
letter is away (``establishment_rules.lapse_notified``). No letter will ever be
sent for these, so the row is written here, at the lapse, with ``notified``
false: still the record, and nobody is told.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .establishment_constants import RULE_LAPSED_ACTION_TYPE

logger = logging.getLogger(__name__)

#: Why the row was written at the lapse rather than after a letter.
REASON = "own_rule_unconfirmed"


def record(ledger: Any, rows: list[dict[str, Any]]) -> None:
    """One ``RULE_LAPSED`` row per silently lapsed rule. Best-effort like the
    sweep that calls it: a row that cannot be written is logged, never a
    refused verb."""
    if ledger is None:
        return
    for row in rows:
        try:
            ledger.append(
                {
                    "action_type": RULE_LAPSED_ACTION_TYPE,
                    "actor": "workspace-broker",
                    "actor_role": "broker",
                    "metadata": json.dumps(
                        {
                            "proposal_id": row["proposal_id"],
                            "scope": row["scope"],
                            "instructed_by": row["instructed_by"],
                            "for_admin": row["for_admin"],
                            "text_sha256": row["text_sha256"],
                            "notified": False,
                            "reason": REASON,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
        except Exception:  # a record that cannot be written never refuses the verb
            logger.exception("establishment: the silent lapse of %s was not recorded", row.get("proposal_id"))
