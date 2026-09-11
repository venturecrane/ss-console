"""The rule lifecycle: a spoken rule is proposed, then confirmed, declined, or lapses.

Split out of ``establishment_store.py`` (2026-09-11); see
:mod:`.establishment_lifecycle` for the shape. Every verb here is reachable
through :class:`~workspace_broker.establishment_store.EstablishmentStore`,
which delegates to one instance of :class:`RuleProposals`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .establishment_constants import (
    OPS_REQUEST_KIND,
    OPS_REQUEST_LAPSED_ACTION_TYPE,
    PROPOSAL_SCOPES,
    RULE_DECLINED_ACTION_TYPE,
    RULE_LAPSED_ACTION_TYPE,
    RULE_PROPOSED_ACTION_TYPE,
    _MAX_SHORT_TEXT,
)
from .establishment_lifecycle import ProposalLifecycle
from .establishment_validation import (
    EstablishmentValidationError,
    _require_class_slug,
    _require_property,
    _require_proposal_id,
    _require_text,
    normalize_rule_text,
    proposal_state,
    readback_for,
    require_address,
)


class RuleProposals(ProposalLifecycle):
    """propose / decline / lapse_notified / pending_rules (ss-console#2529, #2546)."""

    def propose(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record one spoken rule as PENDING and return the readback to send.

        Nothing is installed here and nothing is in effect. What the caller gets
        back is the exact block to put in front of the person, plus the id they
        will quote when they answer. The seat may not claim effect off the back
        of this call — that claim belongs after a submit whose result says
        ``installed`` (the honest-status rule the intake's converge-wait exists
        to support).
        """
        pending = self._require_pending()
        scope = request.get("scope")
        if scope not in PROPOSAL_SCOPES or scope == "act":
            raise EstablishmentValidationError(
                f"scope must be one of ['firm_adjust', 'person']; got {scope!r} (an act is proposed with act_propose)"
            )
        instructed_by = require_address(request.get("instructed_by"), "instructed_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        for_admin_raw = request.get("for_admin", False)
        if not isinstance(for_admin_raw, bool):
            raise EstablishmentValidationError("for_admin must be a boolean")
        for_admin = for_admin_raw

        subject_raw = request.get("subject")
        if not isinstance(subject_raw, dict):
            raise EstablishmentValidationError(
                "subject must be an object: {person} for a personal rule, {output_class, property} for a firm rule"
            )
        if scope == "person":
            if for_admin:
                raise EstablishmentValidationError(
                    "for_admin must be false on a personal rule; a person's own "
                    "preference is theirs to set and needs nobody to apply it"
                )
            person = require_address(subject_raw.get("person"), "subject.person")
            if person != instructed_by:
                # The seat gate says the same thing, and says it first. Repeated
                # here because a broker that would install one person's
                # preferences on another's say-so is a broker whose only defence
                # is a hook it cannot see.
                raise EstablishmentValidationError(
                    "a personal rule's subject must be the person stating it; "
                    "a rule about someone else's work is a firm rule"
                )
            subject: dict[str, Any] = {"person": person}
        else:
            subject = {
                "output_class": _require_class_slug(subject_raw.get("output_class")),
                "property": _require_property(subject_raw.get("property")),
            }

        text = normalize_rule_text(request.get("text"))
        # ss-console#2546: the same person, the same sentence, already waiting.
        # Hand back the row they already have and write NOTHING - no second row,
        # no second RULE_PROPOSED, and (the reason this matters now) no second
        # email to an administrator carrying a different tag, only one of which
        # answering would close.
        existing = pending.find_open_duplicate(instructed_by=instructed_by, scope=scope, text=text)
        if existing is not None:
            return {
                "ok": True,
                "duplicate_of": existing["proposal_id"],
                "proposal_id": existing["proposal_id"],
                "scope": existing["scope"],
                "subject": existing["subject"],
                "for_admin": existing["for_admin"],
                "expires_at": existing["expires_at"],
                "readback": readback_for(existing["proposal_id"], existing["text"]),
            }
        row = pending.create(
            scope=scope,
            subject=subject,
            text=text,
            instructed_by=instructed_by,
            for_admin=for_admin,
        )

        metadata = {
            "proposal_id": row["proposal_id"],
            "scope": scope,
            "for_admin": for_admin,
            "instructed_by": instructed_by,
            "source_ref": source_ref,
            # The rule's TEXT is not here and must never be. A proposal is a
            # sentence a person typed in an email, retained rows carry ids,
            # names, and counts (ADR 0083's posture), and the digest is what
            # makes the committed text checkable against the proposed one
            # without keeping a second copy of it in the ledger.
            "text_sha256": row["text_sha256"],
        }
        metadata.update(subject)
        self.ledger.append(
            {
                "action_type": RULE_PROPOSED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )
        return {
            "ok": True,
            # Always present, so a caller reads one field rather than testing
            # for a key's absence to decide whether it just created something.
            "duplicate_of": None,
            "proposal_id": row["proposal_id"],
            "scope": scope,
            "subject": subject,
            "for_admin": for_admin,
            "expires_at": row["expires_at"],
            "readback": readback_for(row["proposal_id"], text),
        }

    # ------------------------------------------------------------------
    # establish_decline / establish_lapse_notified  (ss-console#2546)
    # ------------------------------------------------------------------

    def decline(self, request: dict[str, Any]) -> dict[str, Any]:
        """Refuse one rule on an administrator's word.

        The other half of "apply that". Before this verb an administrator's "no"
        did nothing at all: the rule sat open until it expired, the person who
        asked heard nothing, and the only record of the decision was whatever
        the model happened to say in a reply. So a decline is now a row and a
        state, and the person who asked can be told.

        Four conditions, and every one of them is in the UPDATE's WHERE clause
        rather than checked above it, so two administrators answering at once
        cannot both win:
          - the row is open (not committed, not already declined, not lapsed);
          - it is ``for_admin`` - a rule somebody stated about their OWN work is
            not an administrator's to refuse, they simply do not confirm it;
          - it has not expired;
          - the decliner is not the person who stated it (that is a withdrawal,
            a different act, and letting it through here would let one address
            both raise and refuse a rule with no second person involved).

        The SEAT is what establishes that the decliner is an administrator, from
        the authored allow list this uid cannot read. What this verb enforces is
        everything that can be enforced from the row.
        """
        pending = self._require_pending()
        proposal_id = _require_proposal_id(request.get("proposal_id"))
        declined_by = require_address(request.get("declined_by"), "declined_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        row = pending.get(proposal_id)
        if row is None:
            raise EstablishmentValidationError(f"no rule was proposed under {proposal_id}; nothing to decline")
        self._refuse_undeclinable(row, proposal_id, declined_by)
        if not pending.decline(proposal_id, declined_by):
            # Lost the race to another decline or to the commit. Whichever won,
            # the answer is the state now on the row, never this call's.
            raise EstablishmentValidationError(f"rule {proposal_id} was already answered; nothing was changed")

        metadata = {
            "proposal_id": proposal_id,
            "scope": row["scope"],
            "instructed_by": row["instructed_by"],
            "declined_by": declined_by,
            "source_ref": source_ref,
            # The digest, never the sentence - same posture as RULE_PROPOSED.
            # The two rows join on it, so the ledger shows WHICH rule was
            # refused without holding a second copy of the firm's words.
            "text_sha256": row["text_sha256"],
        }
        metadata.update(row["subject"])
        self.ledger.append(
            {
                "action_type": RULE_DECLINED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "state": "declined",
            "scope": row["scope"],
            "subject": row["subject"],
            # Who to tell, and what they asked for. The seat composes the note to
            # the requester from these rather than from anything on the wire.
            "instructed_by": row["instructed_by"],
            "declined_by": declined_by,
            "text": row["text"],
            "readback": readback_for(proposal_id, row["text"], row["kind"]),
        }

    @staticmethod
    def _refuse_undeclinable(row: dict[str, Any], proposal_id: str, declined_by: str) -> None:
        """Name the reason a decline cannot land, before the UPDATE tries it.

        The UPDATE is the enforcement; this exists so the refusal a person reads
        says which of five different things happened.
        """
        # ss-console#2546 (the operations half): an administrator of the FIRM
        # does not decline an operations request, because it was never theirs to
        # answer -- it went to SMD. ``ops_resolve`` is the verb for that, and
        # keeping this one unable to touch the kind is what stops a decline
        # landing on a row whose requester is then told the firm refused it.
        if row["kind"] == OPS_REQUEST_KIND:
            raise EstablishmentValidationError(
                f"{proposal_id} is an operations request; it is answered by SMD with ops_resolve, not declined here"
            )
        if row["consumed_at"] is not None:
            raise EstablishmentValidationError(f"rule {proposal_id} was already committed; it is in effect")
        if row["declined_at"] is not None:
            raise EstablishmentValidationError(f"rule {proposal_id} was already declined; nothing was changed")
        if row["lapsed_at"] is not None or row["expires_at"] < time.time():
            raise EstablishmentValidationError(f"rule {proposal_id} lapsed unanswered; ask for it to be stated again")
        if not row["for_admin"]:
            raise EstablishmentValidationError(
                f"rule {proposal_id} was not waiting on an administrator; there is nothing to decline"
            )
        if row["instructed_by"] == declined_by:
            raise EstablishmentValidationError(
                "the person who stated a rule cannot decline it; leaving it unconfirmed is how they withdraw it"
            )

    def lapse_notified(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record that the person who asked has been told how their rule ended.

        Called by the seat AFTER the note is away, so a send that failed leaves
        the row unmarked and the next attributed turn tries again. Marking first
        would trade a duplicate note for a silence, and silence is the failure
        this whole issue exists to end.

        ONE ACTION TYPE, and it is RULE_LAPSED. A lapse has no other row
        anywhere, so this is the only record that a rule died unanswered. A
        DECLINE already wrote RULE_DECLINED at the moment the administrator
        answered, so telling the requester about it writes nothing further -
        this verb cannot emit a second type, which is the property that keeps
        it unable to forge one. An INSTALL (ss-console#2546 follow-up) is the
        third thing this can now mark, and it writes nothing either: the run
        already left its ESTABLISHMENT_RESULT row.
        """
        pending = self._require_pending()
        proposal_id = _require_proposal_id(request.get("proposal_id"))
        row = pending.get(proposal_id)
        if row is None:
            raise EstablishmentValidationError(f"no rule was proposed under {proposal_id}; nothing to report")
        # ss-console#2546 (the operations half): the same verb reports both, so
        # the noun follows the row rather than the code path. A person told
        # "rule 1a2b has no outcome" about a request for a Monday digest is
        # being told about something they never asked for.
        noun = "operations request" if row["kind"] == OPS_REQUEST_KIND else "rule"
        if row["declined_at"] is None and row["lapsed_at"] is None and row.get("installed_at") is None:
            raise EstablishmentValidationError(
                f"{noun} {proposal_id} has no outcome to report; it is still open"
                if row["consumed_at"] is None
                # ss-console#2546 follow-up. Committed is not an outcome a
                # person can be told about yet, and saying so by name is what
                # stops a seat mailing "your rule is in effect" about a run
                # still inside its converge window.
                else f"{noun} {proposal_id} was committed but has not been observed "
                "installed; there is nothing to report yet"
            )
        if not pending.mark_outcome_reported(proposal_id):
            raise EstablishmentValidationError(
                f"the outcome of {noun} {proposal_id} was already reported; nothing was changed"
            )
        state = proposal_state(pending.get(proposal_id) or row)
        if state == "lapsed":
            # ss-console#2546 (the operations half). A lapsed OPERATIONS request
            # is not a lapsed rule, and the ledger must not say it was: nobody at
            # the firm failed to answer it, SMD did. One pinned type per kind,
            # chosen from the STORED kind so no caller can pick which row it
            # writes.
            lapsed_type = OPS_REQUEST_LAPSED_ACTION_TYPE if row["kind"] == OPS_REQUEST_KIND else RULE_LAPSED_ACTION_TYPE
            self.ledger.append(
                {
                    "action_type": lapsed_type,
                    "actor": "operator",
                    "actor_role": "agent",
                    "metadata": json.dumps(
                        {
                            "proposal_id": proposal_id,
                            "scope": row["scope"],
                            "instructed_by": row["instructed_by"],
                            "for_admin": row["for_admin"],
                            "text_sha256": row["text_sha256"],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
        return {"ok": True, "proposal_id": proposal_id, "state": state}

    # ------------------------------------------------------------------
    # establish_pending  (ss-console#2529)
    # ------------------------------------------------------------------

    def pending_rules(self, request: dict[str, Any]) -> dict[str, Any]:
        """What this sender can still confirm. Read-only, so no audit row.

        Two shapes. By ``sender``: their own open proposals, plus every proposal
        awaiting an admin when ``include_for_admin`` is set. By ``proposal_id``:
        that one row if it is still open, an empty list otherwise — a lookup, not
        an assertion that it can be confirmed.
        """
        pending = self._require_pending()
        outcomes_raw = request.get("include_outcomes", False)
        if not isinstance(outcomes_raw, bool):
            raise EstablishmentValidationError("include_outcomes must be a boolean")
        proposal_id_raw = request.get("proposal_id")
        if proposal_id_raw is not None:
            proposal_id = _require_proposal_id(proposal_id_raw)
            row = pending.get(proposal_id)
            # ss-console#2546 follow-up. Under ``include_outcomes`` this lookup
            # answers "what became of this rule", so it returns the row in ANY
            # state and lets ``state`` say which. Without the flag it answers
            # the older question, "can this still be confirmed", and a committed
            # or expired row is correctly absent -- the same opt-in that keeps a
            # seat running the old plugin seeing exactly what it saw before.
            #
            # THIS IS THE LOOKUP THAT SILENTLY BROKE THE INSTALL NOTICE. The
            # seat fetched the row right after committing it, to learn whether
            # the rule was for_admin and who had asked for it, and got an empty
            # list every time -- because committing is precisely what took the
            # row out of this branch's answer.
            visible = row is not None and (
                outcomes_raw or (row["consumed_at"] is None and row["expires_at"] >= time.time())
            )
            open_rows = [row] if row is not None and visible else []
            return {"ok": True, "pending": [self._pending_view(r) for r in open_rows]}
        if request.get("sender") is None and outcomes_raw:
            # THE SWEEPER'S QUERY, and the only shape with no sender. A lapse
            # has nobody in front of it by definition, so the seat's sweeper
            # asks what ended unreported and tells each row's own author. It
            # returns terminal rows only: nothing here can be confirmed, so it
            # cannot become a second way to release a rule.
            return {
                "ok": True,
                "pending": [self._pending_view(r) for r in pending.unreported_outcomes_for(None)],
            }
        sender = require_address(request.get("sender"), "sender")
        include_raw = request.get("include_for_admin", False)
        if not isinstance(include_raw, bool):
            raise EstablishmentValidationError("include_for_admin must be a boolean")
        rows = pending.open_for(sender, include_raw)
        # ss-console#2546. OPT-IN, and the reason is version skew, not taste.
        # This module ships in the seat image; the plugin that reads it ships at
        # the pinned OVERLAY_REF, and the two move in separate PRs. A seat
        # running the new broker under the old plugin would be handed declined
        # and lapsed rows in a list whose every previous member was confirmable,
        # and would offer the firm a rule that has already been refused. Default
        # off means the old caller sees exactly what it saw before.
        if outcomes_raw:
            rows = rows + pending.unreported_outcomes_for(sender)
        return {"ok": True, "pending": [self._pending_view(r) for r in rows]}

    @staticmethod
    def _pending_view(row: dict[str, Any]) -> dict[str, Any]:
        """One row as the seat sees it, readback included.

        The readback is re-rendered from the stored text rather than stored
        alongside it, so a row can never carry a readback that disagrees with the
        sentence it holds.
        """
        return {
            "proposal_id": row["proposal_id"],
            "scope": row["scope"],
            "kind": row["kind"],
            "subject": row["subject"],
            "text": row["text"],
            "readback": readback_for(row["proposal_id"], row["text"], row["kind"]),
            "payload": row["payload"],
            # The tool an act row names, at the top level, because that is where
            # the seat reads it (hermes-smd-establishment._act_confirmation_note:
            # ``row.get("tool")``). It also lives inside ``subject``; read live on
            # pilot-smokeball 2026-08-22, a confirmed act came back "no longer
            # held" because this view left the top-level key out and the seat
            # took the empty string as "names no tool".
            "tool": (row["subject"] or {}).get("tool") if row["kind"] == "tool_call" else None,
            "instructed_by": row["instructed_by"],
            "for_admin": row["for_admin"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            # ss-console#2546. A row is no longer only "here to be confirmed":
            # it can be one the seat must REPORT, and the seat has to be able to
            # tell those apart without inferring it from timestamps.
            "state": proposal_state(row),
            "declined_by": row.get("declined_by"),
            "lapse_notified": row.get("lapse_notified_at") is not None,
            # ss-console#2546 (the operations half). Who at SMD answered, what
            # they wrote, and whether they have already been asked once for an
            # answer in the two words the parser reads. The seat needs all three
            # to compose the notice and to avoid re-asking; none of them means
            # anything on a rule or an act row, where they read back as
            # None/None/False.
            "resolved_by": row.get("resolved_by"),
            "outcome_reason": row.get("outcome_reason"),
            "ask_sent": row.get("ask_sent_at") is not None,
            # ss-console#2546 follow-up. "committed" and "in force" are not the
            # same fact, so the view carries both: ``state`` says the firm's
            # administrator applied it, this says somebody read the run result
            # and saw it land. Only the second entitles a note.
            "installed": row.get("installed_at") is not None,
            # ss-console#2546 (the duplicate-letter fix). Whether SOME observer
            # currently holds the right to send this row's outcome letter. It is
            # the raw column, not a freshness judgement: a claim older than
            # NOTIFY_CLAIM_STALE_SECONDS still reads True here and is still
            # takeable by claim_notify. Nothing may decide whether to send from
            # this field -- that is what the claim verb is for; it is here so a
            # seat can SAY why it is sending nothing.
            "notify_claimed": row.get("notify_claimed_at") is not None,
            "notify_claimed_by": row.get("notify_claimed_by"),
        }
