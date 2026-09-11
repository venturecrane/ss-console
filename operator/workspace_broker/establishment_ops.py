"""The operations-request lifecycle: a request to SMD is recorded, asked, and answered.

Split out of ``establishment_store.py`` (2026-09-11); see
:mod:`.establishment_lifecycle` for the shape.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .establishment_constants import (
    OPS_OUTCOMES,
    OPS_REQUEST_KIND,
    OPS_REQUEST_RECORDED_ACTION_TYPE,
    OPS_REQUEST_RESOLVED_ACTION_TYPE,
    _MAX_SHORT_TEXT,
)
from .establishment_lifecycle import ProposalLifecycle
from .establishment_validation import (
    EstablishmentValidationError,
    _require_proposal_id,
    _require_text,
    normalize_outcome_reason,
    normalize_rule_text,
    proposal_state,
    readback_for,
    require_address,
)


class OpsRequests(ProposalLifecycle):
    """ops_propose / ops_resolve / ops_ask_sent (ss-console#2546, the operations half)."""

    # ------------------------------------------------------------------
    # ops_propose / ops_resolve / ops_ask_sent  (ss-console#2546)
    # ------------------------------------------------------------------

    def ops_propose(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record one OPERATIONS request and return the tag SMD will quote back.

        Nothing is changed by this and nothing is promised. ADR 0085's
        2026-08-22 amendment puts routines, schedules, channels, memory,
        autonomy and on/off with SMD rather than with the firm, so the Operator's
        honest answer to "send me a digest every Monday" is that SMD makes those
        changes. What this verb adds is the half that was missing: the request
        becomes a row with an id, so the answer can find its way back to the
        person who asked instead of ending in a polite sentence.

        THE TAG IS THE CAPABILITY, stated plainly because it is the accepted
        risk on this path. ``[ops XXXX]`` is eight hex characters minted here,
        and quoting it from an address the firm authored on
        ``scope.ops_reply_from`` is what lets an answer resolve this row. No seat
        gets an SPF or DKIM verdict, so that is the same spoof class for every
        address on that list; what bounds it is that the whole effect of a forged
        answer is one templated notice to the person who asked.
        """
        pending = self._require_pending()
        instructed_by = require_address(request.get("instructed_by"), "instructed_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)
        text = normalize_rule_text(request.get("text"))

        # The same person, the same request, already waiting. Hand back the row
        # they have and write nothing: no second row, no second
        # OPS_REQUEST_RECORDED, and no second email to SMD carrying a different
        # tag, only one of which answering would close.
        existing = pending.find_open_duplicate(instructed_by=instructed_by, scope="ops", text=text)
        if existing is not None:
            return {
                "ok": True,
                "duplicate_of": existing["proposal_id"],
                "proposal_id": existing["proposal_id"],
                "kind": OPS_REQUEST_KIND,
                "instructed_by": existing["instructed_by"],
                "expires_at": existing["expires_at"],
                "readback": readback_for(existing["proposal_id"], existing["text"], OPS_REQUEST_KIND),
            }

        row = pending.create(
            scope="ops",
            # No subject. A rule is about an output class or a person; an
            # operations request is about the seat, and there is nothing here
            # that a subject would name.
            subject={},
            text=text,
            instructed_by=instructed_by,
            # for_admin, and it is load-bearing rather than decorative: the
            # sweeper's committing arm requires it, so this is what lets a
            # ``done`` reach the requester through the path that already exists.
            for_admin=True,
            kind=OPS_REQUEST_KIND,
        )
        self.ledger.append(
            {
                "action_type": OPS_REQUEST_RECORDED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(
                    {
                        "proposal_id": row["proposal_id"],
                        "instructed_by": instructed_by,
                        "source_ref": source_ref,
                        # Ids and a digest, never the sentence -- ADR 0083's
                        # retention posture, identical to RULE_PROPOSED.
                        "text_sha256": row["text_sha256"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        return {
            "ok": True,
            "duplicate_of": None,
            "proposal_id": row["proposal_id"],
            "kind": OPS_REQUEST_KIND,
            "instructed_by": instructed_by,
            "expires_at": row["expires_at"],
            "readback": readback_for(row["proposal_id"], text, OPS_REQUEST_KIND),
        }

    def ops_resolve(self, request: dict[str, Any]) -> dict[str, Any]:
        """End one operations request on SMD's answer, exactly once.

        Three outcomes and no fourth (:data:`OPS_OUTCOMES`):

        ``done``       SMD made the change. The requester is told.
        ``declined``   SMD said no, with the reason they wrote. The requester is
                       told, and the reason is quoted rather than paraphrased --
                       an Operator that composed its own explanation of somebody
                       else's refusal would be inventing client-facing content.
        ``withdrawn``  the seat could not get the request out of the building, so
                       it gives the row back. Nothing is sent, because nothing
                       was ever asked, and the requester already heard that in
                       the refusal they got in the same turn.

        WHAT THIS VERB CANNOT DO, and each is enforced rather than documented:
        it cannot touch a rule or an act (the UPDATE requires the kind), it
        cannot answer a request twice (the UPDATE requires the row open), and it
        cannot decide who at SMD is entitled to answer. That last one is the
        SEAT's, from ``scope.ops_reply_from`` in a config this uid cannot read --
        the same posture ``establish_decline`` takes toward the admin list.
        """
        pending = self._require_pending()
        proposal_id = _require_proposal_id(request.get("proposal_id"))
        outcome = _require_text(request.get("outcome"), "outcome", _MAX_SHORT_TEXT)
        if outcome not in OPS_OUTCOMES:
            raise EstablishmentValidationError(f"outcome must be one of {sorted(OPS_OUTCOMES)}; got {outcome!r}")
        resolved_by = require_address(request.get("resolved_by"), "resolved_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)
        reason = normalize_outcome_reason(request.get("reason"))

        row = pending.get(proposal_id)
        if row is None:
            raise EstablishmentValidationError(
                f"no operations request was recorded under {proposal_id}; nothing to answer"
            )
        self._refuse_unresolvable(row, proposal_id)
        if not pending.resolve_ops(proposal_id, outcome, resolved_by, reason):
            # Lost the race to another answer. Whichever won, the answer is the
            # state now on the row, never this call's.
            raise EstablishmentValidationError(
                f"operations request {proposal_id} was already answered; nothing was changed"
            )

        self.ledger.append(
            {
                "action_type": OPS_REQUEST_RESOLVED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(
                    {
                        "proposal_id": proposal_id,
                        "outcome": outcome,
                        "instructed_by": row["instructed_by"],
                        "resolved_by": resolved_by,
                        "source_ref": source_ref,
                        "text_sha256": row["text_sha256"],
                        # WHETHER a reason was given, never the reason. It is a
                        # person's prose about a business decision, and retained
                        # rows carry ids, names, and counts.
                        "has_reason": reason is not None,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        state = proposal_state(pending.get(proposal_id) or row)
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "outcome": outcome,
            "state": state,
            # Who to tell and what they asked for. The seat composes the notice
            # from these, never from anything on the wire.
            "instructed_by": row["instructed_by"],
            "resolved_by": resolved_by,
            "reason": reason,
            "text": row["text"],
            "readback": readback_for(proposal_id, row["text"], OPS_REQUEST_KIND),
        }

    @staticmethod
    def _refuse_unresolvable(row: dict[str, Any], proposal_id: str) -> None:
        """Name the reason an answer cannot land, before the UPDATE tries it.

        The UPDATE is the enforcement; this exists so the refusal says which of
        four things happened rather than "nothing was changed".
        """
        if row["kind"] != OPS_REQUEST_KIND:
            raise EstablishmentValidationError(
                f"{proposal_id} is not an operations request; a rule is answered by "
                "an administrator of the firm, not by SMD"
            )
        if row["consumed_at"] is not None:
            raise EstablishmentValidationError(
                f"operations request {proposal_id} was already answered; SMD made that change"
            )
        if row["declined_at"] is not None:
            raise EstablishmentValidationError(
                f"operations request {proposal_id} was already declined; nothing was changed"
            )
        if row["lapsed_at"] is not None or row["expires_at"] < time.time():
            raise EstablishmentValidationError(f"operations request {proposal_id} lapsed unanswered; ask for it again")

    def ops_ask_sent(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record that SMD has been asked, once, to answer in words this parses.

        Called AFTER the ask is away, so a send that failed leaves the row
        unmarked and the next observer tries again -- the ordering
        ``lapse_notified`` uses, for the same reason. A second call is a named
        refusal rather than a silent no-op, so a seat that has lost track of the
        mark learns it here instead of mailing SMD on every turn.
        """
        pending = self._require_pending()
        proposal_id = _require_proposal_id(request.get("proposal_id"))
        row = pending.get(proposal_id)
        if row is None:
            raise EstablishmentValidationError(
                f"no operations request was recorded under {proposal_id}; nothing to ask about"
            )
        if row["kind"] != OPS_REQUEST_KIND:
            raise EstablishmentValidationError(
                f"{proposal_id} is not an operations request; there is nothing to ask SMD"
            )
        if not pending.mark_ask_sent(proposal_id):
            raise EstablishmentValidationError(
                f"operations request {proposal_id} has already been asked once, or is "
                "no longer open; nothing was changed"
            )
        return {"ok": True, "proposal_id": proposal_id, "ask_sent": True}
