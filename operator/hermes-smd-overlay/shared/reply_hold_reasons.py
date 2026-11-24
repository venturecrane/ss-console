"""The reply relay's closed vocabulary of hold reasons, which the heartbeat
rides on the wire as ``reply_held:<reason>`` (``shared/heartbeat.py``). Its own
module so the list grows without growing the heartbeat (2026-11-19)."""

from __future__ import annotations

#: The reply relay's own reasons for holding a reply (``hermes-smd-reply``:
#: ``_held`` call sites, ``relay.gate_body``, ``relay.RateLimiter``,
#: ``spec_gate.REASON_OUTPUT_CHECKLIST``). A CLOSED vocabulary: a reason not
#: listed rides as ``reply_held:other`` rather than as whatever string the row
#: carries, so nothing a row was given can reach the wire.
REPLY_HOLD_REASONS = frozenset(
    {
        "sender_not_on_roster",
        "recipient_mismatch",
        "no_inbox_id",
        "device_redirect_unsupported",
        "empty_body",
        "matter_mismatch",
        "output_checklist",
        "advertising_gate",
        "content_sensitive",
        "content_floor_error",
        "outbound_gate_error",
        "fabrication:tier1_marker",
        "fabrication:tier2_citation",
        "fabrication:load_error",
        "fabrication:blocked",
        "rate_limited",
        "rate_limited_per_sender",
        "rate_limited_global",
        "rate_limited_backstop",
        "queued_behind_held",
        # SMD's review of a records reply (``shared.matter_file_review``): the
        # reply is waiting for SMD, the wait ran out with no decision, or the
        # reply could not be held for review on this path at all.
        "matter_files_review",
        "matter_files_review_expired",
        "matter_files_review_unsupported",
        # A reply in words other than the fixed sentence its job or a tool's
        # whole-reply say wrote (the broker's fixed_wording refusal and the
        # relay's say check, 2026-11-19): held for the redraft that sends it.
        "fixed_wording",
        # A reply to a firm address that names the Operator's machinery ("this
        # turn", "drafting job"; ``shared.turn_says.TIER1_PHRASES``), held on
        # every turn and counted apart from fixed_wording (review 2026-11-19).
        "mechanics_named",
        # A reply to firm staff that asks them a question or offers choices
        # (``shared.firm_question``, 2026-11-23): held for the redraft that
        # states what will be done; past its cap it stays held.
        "firm_question",
    }
)

__all__ = ["REPLY_HOLD_REASONS"]
