"""Broker-side validation for a captured correction (ADR 0083 §4, ss-console #2091).

WHAT A CORRECTION IS. A customer says, in the course of ordinary work, how an
output should have been shaped or sounded — *"could this be a table instead of
text"*. ADR 0083 §4 makes that statement an edit to the output class's stored
property, which is what turns "you correct it once and it stays corrected" into
a mechanism rather than a hope.

WHAT THE AGENT'S ROLE IS. **Witness, never author.** The Operator records that
the statement was made; it never promotes it, and nothing it writes reaches a
spec file. That is not caution — it follows from #2084's finding that
``read_file`` is READ-class, unfenced, and does not taint. A spec the agent
could write would be a persistent, untainted, self-authored prompt-injection
channel surviving restarts, and an agent that could promote its own correction
into a spec has exactly that one step removed. Promotion is portal-side, by a
Named Administrator, and the promoted bytes are the ones **they** submit.

WHY THE CAPTURE LIVES HERE AND NOT IN THE CONSOLE — A DECISION, NOT A
WORKAROUND. **Capture belongs where the agent is and cannot escalate; promotion
belongs where the human is.** This ledger is owned by the broker uid and the
agent uid cannot open it read-write at all, so "the agent cannot forge a
promotion" is a filesystem fact rather than a property of a credential the agent
holds and might leak. Putting capture in the console database instead would mean
giving the agent a console-write credential — strictly weaker, and reopening the
tenant-forgery hole ADR 0023 locked-decision #10 closed by stripping exactly such
a key from the agent env in ``bootstrap.sh``. The promotion half lives in
``migrations/0102_operator_voice_corrections.sql``, whose header carries the full
argument. Two stores is the design; collapsing them into one is the regression.

WHY VALIDATION LIVES HERE AND NOT IN THE CALLER. The caller is the agent. A
schema the agent enforces is a schema the agent can decline to enforce, so the
broker re-derives the row from scratch: it reads a bounded set of fields off
the request, checks each one, and builds the stored payload itself. Nothing the
caller sends is passed through unexamined, and any field it invents is dropped
rather than stored.

WHY ``status`` IS NOT AN INPUT. It is stamped ``proposed`` here, unconditionally
and unreadably from the wire. A validated-but-caller-supplied status is one
typo away from a caller-supplied ``approved``; a constant cannot be.
"""

from __future__ import annotations

import json
from typing import Any

# The audit action_type this verb is locked to. Pinned exactly one action_type
# per verb, as with the broker's other narrow verbs, so a capture can never be
# used to forge a row of any other kind.
CORRECTION_ACTION_TYPE = "CORRECTION_PROPOSED"

# The only status a captured correction can hold. Promotion is a portal-side
# act recorded in the console D1; it never writes back here, because this
# ledger is append-only by construction.
PROPOSED_STATUS = "proposed"

# The two spec properties an output class carries (ADR 0083 §2-3). Mirrors
# SPEC_PROPERTIES in src/lib/operator/output-class-specs.ts.
SPEC_PROPERTIES: frozenset[str] = frozenset({"voice", "format"})

# Output-class slug charset. Mirrors the applier's ``_safe_slug`` and the
# console writer's CLASS_SLUG_PATTERN. A slug outside it is refused rather than
# sanitized: a quietly-rewritten identifier is how a record lands under a name
# nobody looked for.
_CLASS_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
_MAX_CLASS_SLUG = 64

# Bounds on the free-text fields. The statement is prose a person said, so it
# needs room; it does not need unbounded room, and an unbounded field on an
# append-only ledger is a way to fill a volume.
_MAX_STATEMENT = 4000
_MAX_SHORT_TEXT = 200


class CorrectionValidationError(ValueError):
    """A proposed correction was malformed. Raised before anything is written."""


def _require_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise CorrectionValidationError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise CorrectionValidationError(f"{field} must not be empty")
    if len(text) > limit:
        raise CorrectionValidationError(f"{field} is {len(text)} characters; the ceiling is {limit}")
    return text


def _optional_text(value: Any, field: str, limit: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, field, limit)


def _require_class_slug(value: Any) -> str:
    slug = _require_text(value, "output_class", _MAX_CLASS_SLUG)
    if not set(slug) <= _CLASS_SLUG_CHARS:
        raise CorrectionValidationError("output_class must match [a-z0-9_-]; refusing to rewrite it")
    return slug


def _require_property(value: Any) -> str:
    prop = _require_text(value, "spec_property", _MAX_SHORT_TEXT)
    if prop not in SPEC_PROPERTIES:
        raise CorrectionValidationError(f"spec_property must be one of {sorted(SPEC_PROPERTIES)}; got {prop!r}")
    return prop


def build_correction_row(proposal: Any) -> dict[str, Any]:
    """Validate a proposed correction and build the audit row to append.

    The returned row is an ``audit_log`` row in the broker's agent-column shape:
    ``action_type`` is the pinned constant, and the structured correction rides
    in ``metadata`` as JSON. ``id``/``ts`` are stamped by the ledger, not here,
    so a caller cannot backdate a capture.

    Args:
        proposal: The request's ``proposal`` value. Anything but an object is a
            refusal — the field set below is read off it by name, so a list or a
            string has nothing this can validate.

    Returns:
        The row dict for :meth:`workspace_broker.audit_ledger.LedgerWriter.append`.

    Raises:
        CorrectionValidationError: Any field missing, mistyped, over its
            ceiling, or outside its allowed set.
    """
    if not isinstance(proposal, dict):
        raise CorrectionValidationError("correction_propose requires a 'proposal' object")

    metadata = {
        # Stamped here, never read from the wire. See the module header.
        "status": PROPOSED_STATUS,
        "output_class": _require_class_slug(proposal.get("output_class")),
        "spec_property": _require_property(proposal.get("spec_property")),
        # The statement as it was made. Stored so a Named Administrator can read
        # what was actually said before deciding anything — it is the prompt for
        # a human decision, not the payload of an automated one.
        "statement": _require_text(proposal.get("statement"), "statement", _MAX_STATEMENT),
        # Who said it, as the agent understood it. Provenance for the reviewer,
        # never an authorization fact: the broker cannot verify a claimed
        # speaker, so nothing downstream may treat this as identity.
        "stated_by": _optional_text(proposal.get("stated_by"), "stated_by", _MAX_SHORT_TEXT),
        # Where in the record the statement lives (message id, session id), so a
        # reviewer can go read the surrounding exchange.
        "source_ref": _optional_text(proposal.get("source_ref"), "source_ref", _MAX_SHORT_TEXT),
    }

    return {
        "action_type": CORRECTION_ACTION_TYPE,
        "actor": "operator",
        "actor_role": "agent",
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }


__all__ = [
    "CORRECTION_ACTION_TYPE",
    "PROPOSED_STATUS",
    "SPEC_PROPERTIES",
    "CorrectionValidationError",
    "build_correction_row",
]
