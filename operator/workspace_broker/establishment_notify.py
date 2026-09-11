"""The outcome-letter lifecycle: which of our own processes tells the requester.

Split out of ``establishment_store.py`` (2026-09-11); see
:mod:`.establishment_lifecycle` for the shape. Two verbs, both non-writing.
"""

from __future__ import annotations

from typing import Any

from .establishment_constants import (
    _MAX_SHORT_TEXT,
)
from .establishment_lifecycle import ProposalLifecycle
from .establishment_validation import (
    EstablishmentValidationError,
    _require_proposal_id,
    _require_text,
)


class OutcomeNotifications(ProposalLifecycle):
    """notify_claim / notify_release (ss-console#2546, the duplicate-letter fix)."""

    # ------------------------------------------------------------------
    # establish_notify_claim / establish_notify_release  (ss-console#2546)
    #
    # THE DUPLICATE-LETTER FIX. ``lapse_notified`` is written AFTER the letter is
    # away, on purpose -- marking first would trade a duplicate for a silence.
    # That ordering is safe against a retry inside one process and is not safe
    # against two processes, and the seat runs two: the gateway (pid 658) and its
    # webhook-gate child (pid 1115), each with its own sweeper thread. Observed
    # on pilot-smokeball 2026-08-23, overlay fc8f88c1: both read the row as
    # unreported, both sent, and the requester got the same letter 12 s apart
    # (vfy_01M0QK1927KP54R7J13J2TH3WZ). overlay#315 answered it with an
    # in-process lock, which on this seat is two locks.
    #
    # So the window between "decided to send" and "recorded as sent" gets a
    # holder, and it lives in the ONE process both observers share. Two verbs,
    # both non-writing (no audit row): claiming the right to send is not a
    # decision about the firm's work, it is bookkeeping about which of our own
    # processes is speaking.
    # ------------------------------------------------------------------

    def notify_claim(self, request: dict[str, Any]) -> dict[str, Any]:
        """Take the right to send one row's outcome letter.

        ``claimed: False`` is the ordinary answer for a caller that lost, and it
        is NOT an error: another observer holds the row, or the row was already
        reported, or it has no outcome yet. The caller's correct response to all
        three is identical -- send nothing, mark nothing.

        An UNKNOWN proposal id still raises, because that is a caller bug rather
        than a lost race, and answering it with ``claimed: False`` would let a
        typo look like ordinary contention forever.
        """
        pending = self._require_pending()
        proposal_id = _require_proposal_id(request.get("proposal_id"))
        # A process label ("gateway", "webhook-gate", a pid) rather than an
        # address: this names which of OUR processes holds the row, and it is
        # stored so a live duplicate can be traced to the two senders rather
        # than guessed at.
        claimed_by = _require_text(request.get("claimed_by"), "claimed_by", _MAX_SHORT_TEXT)

        row = pending.get(proposal_id)
        if row is None:
            raise EstablishmentValidationError(
                f"no proposal was recorded under {proposal_id}; there is no outcome to claim"
            )
        claimed = pending.claim_notify(proposal_id, claimed_by)
        return {
            "ok": True,
            "claimed": claimed,
            "proposal_id": proposal_id,
            # Why the claim was refused, for the caller's log. Read off the row
            # AFTER the attempt, so it describes the state that actually beat
            # this caller rather than one read before the race.
            "reason": (None if claimed else self._claim_refusal(pending.get(proposal_id) or row)),
        }

    @staticmethod
    def _claim_refusal(row: dict[str, Any]) -> str:
        """Name which of the three refusals happened. Diagnostic only -- the
        UPDATE is the enforcement, and none of these strings is load-bearing."""
        if row.get("lapse_notified_at") is not None:
            return "the outcome of this proposal has already been reported"
        if row.get("declined_at") is None and row.get("lapsed_at") is None and row.get("installed_at") is None:
            return "this proposal has no outcome to report yet"
        holder = row.get("notify_claimed_by") or "unnamed"
        return f"another observer is sending this outcome ({holder})"

    def notify_release(self, request: dict[str, Any]) -> dict[str, Any]:
        """Hand a claimed row back because the letter did NOT go.

        The failure path of a claim, and the reason a claim is not simply the
        mark: a send that raised must leave the row sendable, immediately, by
        somebody. ``released: False`` means there was nothing to give back --
        already reported, or never claimed -- and is not an error either.
        """
        pending = self._require_pending()
        proposal_id = _require_proposal_id(request.get("proposal_id"))
        row = pending.get(proposal_id)
        if row is None:
            raise EstablishmentValidationError(
                f"no proposal was recorded under {proposal_id}; there is no claim to release"
            )
        return {
            "ok": True,
            "released": pending.release_notify(proposal_id),
            "proposal_id": proposal_id,
        }
