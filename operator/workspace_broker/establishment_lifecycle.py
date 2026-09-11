"""What the four proposal lifecycles share: the store they act on, and the claim.

Split out of ``establishment_store.py`` (2026-09-11, code review 2026-09-10,
Architecture 3). :class:`~workspace_broker.establishment_store.EstablishmentStore`
is the broker's stateful half; it owns the spool directories, the audit ledger
and the proposals table, and it used to carry every lifecycle verb as a method.
The verbs now live one lifecycle per module, each a small collaborator that
holds the store and reads its state through it, so a test that swaps
``store.pending`` or its ledger reaches every lifecycle at once:

* :mod:`.establishment_rules`   propose / decline / lapse_notified / pending_rules
* :mod:`.establishment_notify`  notify_claim / notify_release
* :mod:`.establishment_ops`     ops_propose / ops_resolve / ops_ask_sent
* :mod:`.establishment_acts`    act_propose / act_commit

:meth:`ProposalLifecycle.claim_proposal` and :func:`refuse_restated` are here
because they are the commit-side half of every lifecycle: the store's submit
path and the act commit both load a pending row by name and refuse a restated
one, and there is exactly one wording of each refusal.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .establishment_constants import (
    OPS_REQUEST_KIND,
)
from .establishment_validation import (
    EstablishmentValidationError,
    _require_proposal_id,
    normalize_rule_text,
)
from .pending_rule_store import PendingRuleStore

if TYPE_CHECKING:
    from .establishment_store import EstablishmentStore


class ProposalLifecycle:
    """One lifecycle of the pending-proposals table, acting on the store's state.

    The collaborator holds the store rather than copies of its fields: ``pending``
    may be None on a broker with no rule store, and tests replace it on the
    store to prove the refusal, so every read goes through the store.
    """

    def __init__(self, store: "EstablishmentStore") -> None:
        self._store = store

    @property
    def pending(self) -> PendingRuleStore | None:
        return self._store.pending

    @property
    def ledger(self) -> Any:
        return self._store._ledger

    def _require_pending(self) -> PendingRuleStore:
        return self._store._require_pending()

    def claim_proposal(self, request: dict[str, Any], scope: str) -> dict[str, Any]:
        """Load the pending row a submit names, and refuse it by NAME if it
        cannot be committed.

        Four refusals, deliberately distinguishable: never existed, expired,
        already committed, and stated for a different kind of rule. Collapsing
        them into one message would leave a person who confirmed a rule on
        Monday unable to tell "you already have it" from "it is gone".
        """
        pending = self._require_pending()
        proposal_id = _require_proposal_id(request.get("proposal_id"))
        # The noun follows the SCOPE BEING CLAIMED, not the row: a person who
        # said yes to an act should not be told their rule expired, and the
        # refusals below are read by that person through the reply.
        noun = "act" if scope == "act" else "rule"
        restate = "ask again" if scope == "act" else "state it again"
        unknown_tail = "ask again" if scope == "act" else "state the rule again"
        row = pending.get(proposal_id)
        if row is None:
            raise EstablishmentValidationError(f"no {noun} was proposed under {proposal_id}; {unknown_tail}")
        # ss-console#2546 (the operations half). Named FIRST, and by kind rather
        # than by the scope mismatch that would catch it two checks later,
        # because the two refusals read completely differently to the person who
        # gets them: "that was proposed as 'ops', not 'firm_adjust'" sounds like
        # a bug, and this says the true thing -- nobody at the firm confirms a
        # change to how the seat runs, so there is nothing here for a yes to do.
        if row["kind"] == OPS_REQUEST_KIND:
            raise EstablishmentValidationError(
                f"{proposal_id} is an operations request, not a {noun}; SMD makes "
                "those changes and answers them, so there is nothing to confirm"
            )
        if row["consumed_at"] is not None:
            raise EstablishmentValidationError(
                f"{noun} {proposal_id} was already committed; "
                + ("it has been done" if scope == "act" else "it is in effect")
            )
        # ss-console#2546. Two more distinguishable ends. A declined rule must
        # never commit on a later "yes" from anyone, and the person deserves to
        # hear WHICH thing happened: "an administrator declined it" and "nobody
        # answered in time" call for different next sentences from them.
        if row["declined_at"] is not None:
            raise EstablishmentValidationError(
                f"{noun} {proposal_id} was declined by an administrator; it is not in effect"
            )
        if row["lapsed_at"] is not None:
            # "Lapsed" is the right word for a rule, which somebody was waiting
            # on an answer to. An act was one call the Operator was holding, and
            # nobody was owed a report about it, so it keeps the sentence it has
            # always had.
            ended = "expired" if scope == "act" else "lapsed unanswered"
            raise EstablishmentValidationError(f"{noun} {proposal_id} {ended}; {restate}")
        if row["expires_at"] < time.time():
            raise EstablishmentValidationError(f"{noun} {proposal_id} expired; {restate}")
        if row["scope"] != scope:
            raise EstablishmentValidationError(
                f"{noun} {proposal_id} was proposed as {row['scope']!r}, "
                f"not {scope!r}; refusing to change what it applies to"
            )
        return row


def refuse_restated(request: dict[str, Any], row: dict[str, Any], fields: dict[str, Any]) -> None:
    """A submit may ECHO the proposal's fields; it may not change them.

    THE POINT OF THE WHOLE MECHANISM, stated as code: the person answered
    "yes" to one specific sentence about one specific kind of output, and
    that yes means nothing unless the committed bytes are the bytes they saw.
    So a differing value is a REFUSAL, never a silent substitution — the
    substitution is precisely the attack, and it would leave the firm holding
    a rule it never agreed to with a ledger row saying it confirmed one.
    """
    for field, expected in fields.items():
        supplied = request.get(field)
        if supplied is None:
            continue
        if isinstance(supplied, str) and isinstance(expected, str):
            if field in ("text", "spec_body"):
                supplied = normalize_rule_text(supplied)
            if supplied == expected:
                continue
        elif supplied == expected:
            continue
        raise EstablishmentValidationError(
            f"{field} does not match rule {row['proposal_id']} as it was proposed "
            "and confirmed; the committed rule comes from the proposal, "
            "not from this request"
        )
