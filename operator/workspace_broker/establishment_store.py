"""The broker's half of the establishment spool.

Split out of ``establishment.py`` (2026-08-24). This is the stateful half: it
owns the staging/runs/results directories, the audit-ledger writes, and the
propose / read-back / confirm lifecycle for rules, acts and operations requests.

It composes :class:`~workspace_broker.pending_rule_store.PendingRuleStore` for
the proposals table.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import sqlite3
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc
from .establishment_constants import *  # vocabulary and tuning surface
from .establishment_validation import *  # validators and renderers
from .establishment_constants import (  # noqa: F401 — `import *` skips _names
    _CLASS_SLUG_CHARS,
    _ID_PATTERN,
    _MAX_ACT_DISPLAY_NAME,
    _MAX_ASSERTIONS,
    _MAX_ASSERTIONS_BYTES,
    _MAX_CLASS_SLUG,
    _MAX_NAME_INPUT,
    _MAX_NAME_SLUG,
    _MAX_SHORT_TEXT,
    _NAME_SLUG_KEEP,
    _PROPOSAL_ID_PATTERN,
)
from .establishment_validation import (  # noqa: F401 — `import *` skips _names
    _URL_PATTERN,
    _bounded_str,
    _column,
    _hash_text,
    _optional_text,
    _require_class_slug,
    _require_display_name,
    _require_id,
    _require_property,
    _require_proposal_id,
    _require_text,
)
from .pending_rule_store import PendingRuleStore

logger = logging.getLogger(__name__)


class EstablishmentStore:
    """The broker's half of the establishment spool.

    Layout (created and moded by the entrypoint, never here — the spool root is
    root-owned and the broker uid cannot create it):

        <root>/staging/<staging_id>/meta.json      broker-written
        <root>/staging/<staging_id>/docs/<id>.json broker-written (holds text)
        <root>/staging/<staging_id>/analysis/      ROOT-written (intake), 0700
        <root>/runs/<run_id>/submission.json       broker-written
        <root>/runs/<run_id>/docs/<id>.json        broker-moved from staging
        <root>/results/<run_id>.json               ROOT-written 0640, one-shot

    Runs are assembled complete (INCLUDING submission.json) in a dot-prefixed
    temp dir and atomically renamed into place; the intake skips dot-prefixed
    entries and run dirs without a submission.json, so it never observes a
    half-written submission (overlay establish_intake/intake.py, the other
    half of this contract).

    Lifecycle split with the root intake: the intake purges each RUN dir after
    writing its result, purges the whole staging set after an install run, and
    backstop-sweeps staging at its own longer TTL — because the broker cannot
    remove the root-owned ``analysis/`` subdir the analyze phase leaves in a
    staging set. The broker's sweep therefore only removes staging sets it
    fully owns, and enforces expiry on the rest by refusal.
    """

    def __init__(
        self,
        spool_root: str | Path,
        ledger: Any,
        pending_db_path: str | Path | None = None,
        customer_path: str | Path | None = None,
    ) -> None:
        self.root = Path(spool_root)
        self.staging_dir = self.root / "staging"
        self.runs_dir = self.root / "runs"
        self.results_dir = self.root / "results"
        self._ledger = ledger
        # ss-console#2529. Absent on a broker with no audit DB configured, in
        # which case the propose/confirm verbs refuse by name rather than
        # half-working: a rule the firm cannot be shown back is a rule it cannot
        # confirm, and committing one without the readback is the thing this
        # whole path exists to avoid.
        self.pending = PendingRuleStore(pending_db_path) if pending_db_path else None
        # ss-console#2536. The seat's own authored config, read by THIS uid and
        # never taken off the wire: an act may only be proposed with the values
        # the firm authored, so the broker has to be able to read them itself.
        # Absent (a broker built with no config handle) means no act can be
        # proposed, which is the fail-closed direction.
        self.customer_path = Path(customer_path) if customer_path else None

    # ------------------------------------------------------------------
    # TTL sweep
    # ------------------------------------------------------------------

    def sweep(self, now: float | None = None) -> None:
        """Remove expired staging sets and unread results.

        Best-effort by design: a sweep failure must not refuse the verb that
        triggered it. Run dirs are NOT swept here — their lifecycle belongs to
        the root intake, which purges each run after writing its result.
        """
        now = time.time() if now is None else now
        if self.staging_dir.is_dir():
            for entry in self.staging_dir.iterdir():
                if not entry.is_dir():
                    continue
                if (entry / "analysis").is_dir():
                    # Root-owned analyze artifacts the broker cannot remove.
                    # Don't try — a partial rmtree leaves a zombie set. The
                    # intake's backstop sweep purges these as root; expiry is
                    # still ENFORCED broker-side by _require_staging's age
                    # check, so the lingering dir grants nothing.
                    continue
                created = self._staging_created_at(entry)
                if now - created > STAGING_TTL_SECONDS:
                    shutil.rmtree(entry, ignore_errors=True)
        if self.results_dir.is_dir():
            for entry in self.results_dir.iterdir():
                if not entry.is_file():
                    continue
                try:
                    if now - entry.stat().st_mtime > RESULT_TTL_SECONDS:
                        entry.unlink(missing_ok=True)
                except OSError:
                    continue
        if self.pending is not None:
            try:
                self.pending.sweep(now)
            except sqlite3.Error:
                # Best-effort, same as the rest of this sweep: a table that
                # cannot be swept must not refuse the verb that triggered it.
                # Expiry is still ENFORCED at read and at submit, so a lingering
                # row grants nothing.
                pass

    def _staging_created_at(self, staging_path: Path) -> float:
        meta_path = staging_path / "meta.json"
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            created = meta.get("created_at")
            if isinstance(created, (int, float)):
                return float(created)
        except (OSError, ValueError):
            pass
        try:
            return staging_path.stat().st_mtime
        except OSError:
            return 0.0

    # ------------------------------------------------------------------
    # establish_stage_document
    # ------------------------------------------------------------------

    def stage_document(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate one corpus document and write it into a staging set.

        The stored file is rebuilt from the bounded field set below; the
        sha256 is computed here from the bytes being stored (a wire-supplied
        hash is never read).
        """
        name = safe_slug(request.get("name"))

        text = request.get("text")
        if not isinstance(text, str):
            raise EstablishmentValidationError("text must be a string")
        if not text.strip():
            raise EstablishmentValidationError("text must not be empty")
        text_bytes = text.encode("utf-8")
        if len(text_bytes) > MAX_DOC_TEXT_BYTES:
            raise EstablishmentValidationError(f"text is {len(text_bytes)} bytes; the ceiling is {MAX_DOC_TEXT_BYTES}")

        source_raw = request.get("source")
        if not isinstance(source_raw, dict):
            raise EstablishmentValidationError("source must be an object with connector and document_id")
        source = {
            "connector": _require_text(source_raw.get("connector"), "source.connector", _MAX_SHORT_TEXT),
            "document_id": _require_text(source_raw.get("document_id"), "source.document_id", _MAX_SHORT_TEXT),
            "matter_id": _optional_text(source_raw.get("matter_id"), "source.matter_id", _MAX_SHORT_TEXT),
        }

        staging_id_raw = request.get("staging_id")
        if staging_id_raw is None:
            # Lowercase hex so the id always matches the intake's _SAFE_SEGMENT.
            staging_id = secrets.token_hex(12)
            staging_path = self.staging_dir / staging_id
            (staging_path / "docs").mkdir(parents=True)
            (staging_path / "meta.json").write_text(json.dumps({"created_at": time.time()}), "utf-8")
        else:
            staging_id, staging_path = self._require_staging(staging_id_raw)

        existing = self._load_staged_docs(staging_path)
        if len(existing) + 1 > MAX_DOCS_PER_SET:
            raise EstablishmentValidationError(
                f"staging set already holds {len(existing)} documents; the ceiling is {MAX_DOCS_PER_SET}"
            )
        set_bytes = sum(int(doc.get("size_bytes", 0)) for doc in existing)
        if set_bytes + len(text_bytes) > MAX_SET_BYTES:
            raise EstablishmentValidationError(
                f"staging set would grow to {set_bytes + len(text_bytes)} bytes; the ceiling is {MAX_SET_BYTES}"
            )

        doc_id = f"doc-{len(existing) + 1:03d}"
        while (staging_path / "docs" / f"{doc_id}.json").exists():
            doc_id = f"doc-{secrets.token_hex(4)}"
        digest = _hash_text(text)
        record = {
            "doc_id": doc_id,
            "name": name,
            "sha256": digest,
            "size_bytes": len(text_bytes),
            "source": source,
            "staged_at": time.time(),
            "text": text,
        }
        (staging_path / "docs" / f"{doc_id}.json").write_text(json.dumps(record, sort_keys=True), "utf-8")
        return {
            "ok": True,
            "staging_id": staging_id,
            "doc_id": doc_id,
            "name": name,
            "sha256": digest,
            "doc_count": len(existing) + 1,
            "set_bytes": set_bytes + len(text_bytes),
        }

    def _require_staging(self, value: Any) -> tuple[str, Path]:
        staging_id = _require_id(value, "staging_id")
        staging_path = self.staging_dir / staging_id
        if not staging_path.is_dir():
            raise EstablishmentValidationError("unknown or expired staging_id; stage the documents again")
        # Expiry is enforced here by refusal, not only by the sweep: a set the
        # broker cannot remove (root-owned analysis/ inside) lingers until the
        # intake's backstop purge, and lingering must not extend its life.
        if time.time() - self._staging_created_at(staging_path) > STAGING_TTL_SECONDS:
            raise EstablishmentValidationError(
                f"staging set expired ({STAGING_TTL_SECONDS // 60}-minute TTL); stage the documents again"
            )
        return staging_id, staging_path

    def _load_staged_docs(self, staging_path: Path) -> list[dict[str, Any]]:
        docs_dir = staging_path / "docs"
        docs: list[dict[str, Any]] = []
        if not docs_dir.is_dir():
            return docs
        for entry in sorted(docs_dir.glob("*.json")):
            try:
                record = json.loads(entry.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise EstablishmentValidationError(
                    f"staged document {entry.name} is unreadable; stage the documents again"
                ) from exc
            record["_path"] = entry
            docs.append(record)
        return docs

    # ------------------------------------------------------------------
    # establish_propose  (ss-console#2529)
    # ------------------------------------------------------------------

    def _require_pending(self) -> PendingRuleStore:
        if self.pending is None:
            raise EstablishmentValidationError("this broker has no rule store configured; nothing was recorded")
        return self.pending

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
        self._ledger.append(
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
        self._ledger.append(
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
            self._ledger.append(
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
        self._ledger.append(
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

        self._ledger.append(
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
            open_rows = [row] if visible else []
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

    # ------------------------------------------------------------------
    # act_propose / act_commit  (ss-console#2536)
    # ------------------------------------------------------------------

    def _seat_config(self) -> dict[str, Any]:
        """The seat's own customer.yaml, re-read per call.

        Never cached: the file is root-owned and can be re-applied under a
        running broker, and a cached copy would let a config the firm has
        already changed keep authorizing acts.
        """
        if self.customer_path is None:
            raise EstablishmentValidationError("this broker has no customer.yaml handle; no act can be proposed")
        try:
            import yaml

            data = yaml.safe_load(self.customer_path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise EstablishmentValidationError(
                f"the seat config is not readable ({exc.__class__.__name__}); no act can be proposed"
            ) from exc
        except Exception as exc:
            raise EstablishmentValidationError(
                f"the seat config is not parseable ({exc.__class__.__name__}); no act can be proposed"
            ) from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _require_act_exposure(data: dict[str, Any], tool: str) -> None:
        """Refuse unless SOME persona on this seat authors ``commitment: confirm``.

        WHAT THIS IS AND IS NOT. The persona-aware gate is the seat's
        enforcement hook, which knows which persona is running this turn and
        clamps the act against that persona's authored exposure. This check
        cannot: the broker is not told the persona. What it CAN say is whether
        the seat authorizes confirmable acts at all, and a seat that authorizes
        none must not be able to write an act row through any path, including a
        hook with a bug in it. So it is deliberately the weaker of the two
        checks and it fails in the safe direction: a seat with no
        ``commitment: confirm`` anywhere proposes nothing.
        """
        personas = data.get("personas")
        if not isinstance(personas, list):
            personas = []
        for persona in personas:
            if not isinstance(persona, dict):
                continue
            entitlements = persona.get("entitlements")
            if not isinstance(entitlements, dict):
                continue
            exposure = entitlements.get("exposure")
            if isinstance(exposure, dict) and exposure.get("commitment") == "confirm":
                return
        raise EstablishmentValidationError(
            f"this seat authors no persona with exposure.commitment set to 'confirm', so "
            f"{tool} cannot be proposed to anybody. Authoring it is a config change the "
            "firm agrees to, not something this turn can do"
        )

    def _authored_act_payload(self, tool: str) -> dict[str, Any]:
        """The one payload this seat may propose for this tool, from its own
        authored config. Refused by name when there is none.

        THE MODEL'S ONLY ROLE IN AN ACT IS TO ASK FOR IT. Every value the act
        carries is read here, out of the file the firm authored and a PR
        changed, so "the Operator created a different matter than the one we
        agreed" has no route into the system: a payload that is not byte-equal
        to this is refused, and a seat with nothing authored can propose
        nothing at all.
        """
        if tool != "mcp_smokeball_create_matter":
            raise EstablishmentValidationError(f"no authored act payload is defined for {tool!r}")
        data = self._seat_config()
        self._require_act_exposure(data, tool)
        block: Any = data
        for key in ACT_CONFIG_KEYS:
            block = block.get(key) if isinstance(block, dict) else None
        if not isinstance(block, dict):
            raise EstablishmentValidationError(
                "this seat has no authored "
                + ".".join(ACT_CONFIG_KEYS)
                + " block; the firm authors the matter to create, and until it "
                "does there is nothing to propose"
            )
        fields = ACT_TOOLS[tool]
        missing = [f for f in fields if not isinstance(block.get(f), str) or not block[f].strip()]
        if missing:
            raise EstablishmentValidationError(
                "the authored "
                + ".".join(ACT_CONFIG_KEYS)
                + f" block is missing {sorted(missing)}; every field the readback "
                "names has to be authored before the act can be proposed"
            )
        names = ACT_NAME_KEYS.get(tool, ())
        extra = sorted(set(block) - set(fields) - set(names))
        if extra:
            raise EstablishmentValidationError(
                "the authored "
                + ".".join(ACT_CONFIG_KEYS)
                + f" block carries unknown keys {extra}; the act carries exactly "
                f"{sorted(fields)} plus the display names {sorted(names)} and nothing else"
            )
        return {f: block[f].strip() for f in fields}

    def _authored_act_names(self, tool: str) -> dict[str, str]:
        """The authored display names beside the act payload, if the block
        carries them. Empty when it does not; the caller then needs them from
        the request, and refuses by name when nobody supplied them."""
        data = self._seat_config()
        block: Any = data
        for key in ACT_CONFIG_KEYS:
            block = block.get(key) if isinstance(block, dict) else None
        if not isinstance(block, dict):
            return {}
        out: dict[str, str] = {}
        for key in ACT_NAME_KEYS.get(tool, ()):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
        return out

    @staticmethod
    def _require_act_tool(value: Any) -> str:
        tool = _require_text(value, "tool", _MAX_SHORT_TEXT)
        if tool not in ACT_TOOLS:
            raise EstablishmentValidationError(
                f"{tool!r} is not an act this broker can propose; the closed vocabulary is {sorted(ACT_TOOLS)}"
            )
        return tool

    @staticmethod
    def _require_act_payload(value: Any, tool: str) -> dict[str, Any]:
        """The caller's payload, shape-checked before it is compared.

        Shape first, then equality, so an oversize or wrong-typed field is a
        named refusal rather than a mismatch that reads like a config problem.
        """
        if not isinstance(value, dict):
            raise EstablishmentValidationError("payload must be an object")
        fields = ACT_TOOLS[tool]
        names = ACT_NAME_KEYS.get(tool, ())
        unknown = sorted(set(value) - set(fields) - set(names))
        if unknown:
            raise EstablishmentValidationError(
                f"payload carries fields {unknown} that {tool} does not take; "
                f"it takes exactly {sorted(fields)} (plus the display names {sorted(names)})"
            )
        return {f: _require_text(value.get(f), f"payload.{f}", _MAX_SHORT_TEXT) for f in fields}

    @staticmethod
    def _payload_names(value: Any, tool: str) -> dict[str, str]:
        """The display names riding in a payload, if any (the hook sends the
        authored block whole). Shape-checked like every other caller string."""
        if not isinstance(value, dict):
            return {}
        out: dict[str, str] = {}
        for key in ACT_NAME_KEYS.get(tool, ()):
            if value.get(key) is not None:
                out[key] = _require_text(value.get(key), f"payload.{key}", _MAX_SHORT_TEXT)
        return out

    def act_propose(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record one TOOL CALL as pending and return the line to send.

        Nothing is created here. What comes back is the exact sentence to put in
        front of an administrator, naming every value the act will carry, and
        the tag they quote when they answer. The act happens on the confirming
        turn, through the tool, under the overlay's own gate.

        NOT A TOOL. This verb is reachable only from the seat's enforcement hook
        (overlay PR 2); no agent-callable tool maps to it. The model can ask for
        the authored matter and cannot compose one.
        """
        pending = self._require_pending()
        tool = self._require_act_tool(request.get("tool"))
        payload = self._require_act_payload(request.get("payload"), tool)
        instructed_by = require_address(request.get("instructed_by"), "instructed_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)
        authored = self._authored_act_payload(tool)
        authored_names = self._authored_act_names(tool)
        payload_names = self._payload_names(request.get("payload"), tool)
        # Names in the payload must be the authored names: the read-back the
        # administrator says yes to is rendered from them, so a caller-composed
        # name is the one fabrication this verb exists to refuse.
        for key, value in payload_names.items():
            if key in authored_names and value != authored_names[key]:
                raise EstablishmentValidationError(
                    f"the proposed payload's {key} does not match the authored "
                    + ".".join(ACT_CONFIG_KEYS)
                    + " block; the read-back carries the authored name"
                )
        contact_name = _require_display_name(
            request.get("contact_name")
            or payload_names.get("client_contact_name")
            or authored_names.get("client_contact_name"),
            "contact_name (or an authored client_contact_name)",
        )
        matter_type_name = _require_display_name(
            request.get("matter_type_name")
            or payload_names.get("matter_type_name")
            or authored_names.get("matter_type_name"),
            "matter_type_name (or an authored matter_type_name)",
        )
        if payload != authored:
            # The refusal names the FIELDS, never the two values: a refusal that
            # printed both sides would put a caller-supplied string into the
            # ledger and the reply, which is the one thing this comparison
            # exists to keep out.
            differing = sorted(f for f in authored if payload.get(f) != authored[f])
            raise EstablishmentValidationError(
                f"the proposed {tool} payload does not match this seat's authored "
                + ".".join(ACT_CONFIG_KEYS)
                + f" block on {differing}; the act carries the authored values, "
                "and changing them is a config change the firm makes"
            )
        # The hook may pass the block it read as well. It has to agree with the
        # copy THIS uid read, or the two are looking at different files.
        supplied_authored = request.get("authored")
        if supplied_authored is not None:
            if not isinstance(supplied_authored, dict) or {k: v for k, v in supplied_authored.items()} != authored:
                raise EstablishmentValidationError(
                    "the authored block supplied with this proposal disagrees with the "
                    "one the broker read from the seat config; refusing to choose "
                    "between two configs"
                )

        text = normalize_rule_text(
            act_readback_text(tool, authored, contact_name=contact_name, matter_type_name=matter_type_name)
        )
        payload_sha256 = _hash_text(json.dumps(authored, sort_keys=True, separators=(",", ":")))
        row = pending.create(
            scope="act",
            subject={"tool": tool, "payload_sha256": payload_sha256},
            text=text,
            instructed_by=instructed_by,
            # ALWAYS for an admin. An act is the firm's own record changing;
            # the person who may bless it is a Named Administrator, and the
            # seat-side gate decides who that is.
            for_admin=True,
            kind="tool_call",
            payload=authored,
        )
        self._ledger.append(
            {
                "action_type": ACT_PROPOSED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(
                    {
                        "proposal_id": row["proposal_id"],
                        "kind": "tool_call",
                        "tool": tool,
                        "instructed_by": instructed_by,
                        "source_ref": source_ref,
                        # The payload's DIGEST, not the payload. The commit row
                        # carries the same digest, so the two together prove the
                        # act performed is the act proposed without either row
                        # holding a second copy of the firm's values.
                        "payload_sha256": payload_sha256,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        return {
            "ok": True,
            "proposal_id": row["proposal_id"],
            "kind": "tool_call",
            "tool": tool,
            "payload": authored,
            "payload_sha256": payload_sha256,
            "for_admin": True,
            "expires_at": row["expires_at"],
            "readback": readback_for(row["proposal_id"], text, "tool_call"),
        }

    def act_commit(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record that a proposed act was performed. Consumes the row exactly once.

        Called AFTER the tool succeeded, by the seat's post-tool hook. A failed
        tool call does not reach here: the row stays open for its TTL so the
        person's "yes" survives one transport failure rather than being spent by
        it.
        """
        pending = self._require_pending()
        row = self._claim_proposal(request, "act")
        tool = self._require_act_tool(request.get("tool"))
        stored_tool = row["subject"].get("tool")
        if tool != stored_tool:
            raise EstablishmentValidationError(
                f"act {row['proposal_id']} was proposed for {stored_tool!r}, not {tool!r}; "
                "refusing to commit a different act under a confirmation for this one"
            )
        stored_payload = row["payload"]
        if not isinstance(stored_payload, dict):
            raise EstablishmentValidationError(
                f"act {row['proposal_id']} holds no payload; nothing can be committed under it"
            )
        supplied = request.get("payload")
        if supplied is not None:
            if self._require_act_payload(supplied, tool) != stored_payload:
                raise EstablishmentValidationError(
                    f"payload does not match act {row['proposal_id']} as it was proposed "
                    "and confirmed; the act carries the proposal's values, not this "
                    "request's"
                )
        confirmed_by = require_address(request.get("confirmed_by"), "confirmed_by")
        confirmed_message_id = _require_text(
            request.get("confirmed_message_id"), "confirmed_message_id", _MAX_SHORT_TEXT
        )
        outcome_raw = request.get("outcome")
        if outcome_raw is not None and not isinstance(outcome_raw, dict):
            raise EstablishmentValidationError("outcome must be an object when present")
        outcome = outcome_raw or {}

        run_id = secrets.token_hex(16)
        if not pending.consume(row["proposal_id"], run_id):
            raise EstablishmentValidationError(f"act {row['proposal_id']} was already committed; it has been done")

        payload_sha256 = _hash_text(json.dumps(stored_payload, sort_keys=True, separators=(",", ":")))
        metadata = {
            "proposal_id": row["proposal_id"],
            "run_id": run_id,
            "kind": "tool_call",
            "tool": tool,
            "instructed_by": row["instructed_by"],
            # WHO said yes and IN WHICH MESSAGE. The pair is what makes the
            # confirmation joinable to the inbound row that carried it
            # (ss#2497), so "an admin approved this" is checkable rather than
            # asserted.
            "confirmed_by": confirmed_by,
            "confirmed_message_id": confirmed_message_id,
            "payload_sha256": payload_sha256,
            # A bounded rebuild of the tool's own result. Three fields, each
            # read for its type: what the vendor returned is not forwarded.
            "created": bool(outcome.get("created")),
            "pending": bool(outcome.get("pending")),
            "matter_id": _bounded_str(outcome.get("matter_id")),
        }
        self._ledger.append(
            {
                "action_type": ACT_COMMITTED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )
        return {
            "ok": True,
            "proposal_id": row["proposal_id"],
            "run_id": run_id,
            "tool": tool,
            "payload_sha256": payload_sha256,
        }

    def _claim_proposal(self, request: dict[str, Any], scope: str) -> dict[str, Any]:
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

    @staticmethod
    def _refuse_restated(request: dict[str, Any], row: dict[str, Any], fields: dict[str, Any]) -> None:
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

    # ------------------------------------------------------------------
    # establish_submit
    # ------------------------------------------------------------------

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate a submission, append its audit row, and materialize the run.

        The audit row is appended BEFORE the run dir is renamed into place: a
        run the root intake can see without a ledger row would be an unaudited
        install path, which is the worse failure than a row for a run that
        never materialized.

        Three scopes (ADR 0085 §2/§4/§6). ``firm`` (the default) is the staged-
        corpus path below — Operator admins only, gated seat-side. ``person``
        records the SPEAKER's own preferences: no staging, no corpus, no
        compiler gates; the seat-side predicate pins the subject to the
        attributed sender, and the intake re-validates shape + roster.
        ``firm_adjust`` commits one confirmed sentence against an output class,
        and REQUIRES a proposal id — there is no route by which a firm-wide rule
        installs without a person having been shown it and having said yes.
        """
        scope = request.get("scope") or "firm"
        if scope not in ("firm", "person", "firm_adjust"):
            raise EstablishmentValidationError(f"scope must be 'firm', 'person', or 'firm_adjust'; got {scope!r}")
        if scope == "firm_adjust":
            return self._submit_firm_adjust(request, secrets.token_hex(16))
        if scope == "person":
            return self._submit_person(request, secrets.token_hex(16))
        staging_id, staging_path = self._require_staging(request.get("staging_id"))
        phase = _require_text(request.get("phase"), "phase", _MAX_SHORT_TEXT)
        if phase not in SUBMIT_PHASES:
            raise EstablishmentValidationError(f"phase must be one of {sorted(SUBMIT_PHASES)}; got {phase!r}")

        staged = self._load_staged_docs(staging_path)
        if not staged:
            raise EstablishmentValidationError("staging set holds no documents; stage the corpus first")
        # Integrity re-check of the broker's own files (defense in depth — the
        # intake re-verifies too): every staged text must still hash to the
        # digest recorded when it was staged.
        for doc in staged:
            if _hash_text(doc.get("text", "")) != doc.get("sha256"):
                raise EstablishmentValidationError(
                    f"staged document {doc.get('doc_id')} failed its integrity re-hash; stage the documents again"
                )

        # Lowercase hex so the id always matches the intake's _SAFE_SEGMENT.
        run_id = secrets.token_hex(16)
        if phase == "analyze":
            return self._submit_analyze(staging_id, staging_path, staged, run_id)
        return self._submit_install(request, staging_id, staging_path, staged, run_id)

    def _submit_analyze(
        self,
        staging_id: str,
        staging_path: Path,
        staged: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        doc_summaries = [{"name": d["name"], "sha256": d["sha256"]} for d in staged]
        # The intake's submission contract (its module docstring): run_id,
        # staging_id, phase, created_at. The doc files in docs/ carry the rest.
        submission = {
            "phase": "analyze",
            "scope": "firm",
            "run_id": run_id,
            "staging_id": staging_id,
            "created_at": time.time(),
        }
        row = {
            "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
            "actor": "operator",
            "actor_role": "agent",
            "metadata": json.dumps(
                {
                    "phase": "analyze",
                    "run_id": run_id,
                    "staging_id": staging_id,
                    "docs": doc_summaries,
                    "doc_count": len(staged),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        self._ledger.append(row)
        # Analyze COPIES the corpus into the run: the staging set must survive
        # so the later install submission can hash-bind against it.
        self._materialize_run(run_id, submission, staged, move=False)
        return {"ok": True, "run_id": run_id, "phase": "analyze", "status": "queued"}

    def _submit_install(
        self,
        request: dict[str, Any],
        staging_id: str,
        staging_path: Path,
        staged: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        output_class = _require_class_slug(request.get("output_class"))
        prop = _require_property(request.get("property"))

        body_raw = request.get("spec_body")
        if not isinstance(body_raw, str):
            raise EstablishmentValidationError("spec_body must be a string")
        # LF-normalize BEFORE the ceiling and the hash (portal precedent): the
        # byte count, the digest, and the installed file must agree, on LF.
        body = normalize_lf(body_raw).strip()
        if not body:
            raise EstablishmentValidationError("spec_body must not be empty")
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > MAX_SPEC_BODY_BYTES:
            raise EstablishmentValidationError(
                f"spec_body is {len(body_bytes)} bytes after LF normalization; the ceiling is {MAX_SPEC_BODY_BYTES}"
            )
        spec_digest = sha256(body_bytes).hexdigest()

        assertions = self._validate_assertions(request.get("assertions"))

        manifest_raw = request.get("corpus_manifest")
        if not isinstance(manifest_raw, list) or not manifest_raw:
            raise EstablishmentValidationError("corpus_manifest must be a non-empty list of {doc_id, sha256}")
        if len(manifest_raw) > MAX_DOCS_PER_SET:
            raise EstablishmentValidationError(
                f"corpus_manifest holds {len(manifest_raw)} entries; the ceiling is {MAX_DOCS_PER_SET}"
            )
        staged_by_id = {d["doc_id"]: d for d in staged}
        seen: set[str] = set()
        selected: list[dict[str, Any]] = []
        for index, entry in enumerate(manifest_raw):
            if not isinstance(entry, dict):
                raise EstablishmentValidationError(f"corpus_manifest[{index}] must be an object with doc_id and sha256")
            doc_id = _require_text(entry.get("doc_id"), f"corpus_manifest[{index}].doc_id", 64)
            claimed = _require_text(entry.get("sha256"), f"corpus_manifest[{index}].sha256", 64)
            if doc_id in seen:
                raise EstablishmentValidationError(
                    f"corpus_manifest names {doc_id} twice; refusing an ambiguous corpus"
                )
            seen.add(doc_id)
            doc = staged_by_id.get(doc_id)
            if doc is None:
                raise EstablishmentValidationError(f"corpus_manifest names {doc_id}, which is not in this staging set")
            # The claim must match the broker's OWN hash of the staged bytes —
            # the spec is bound to exactly the corpus the agent staged, and a
            # manifest that disagrees is a refusal, never a repair.
            if claimed != doc["sha256"]:
                raise EstablishmentValidationError(
                    f"corpus_manifest hash for {doc_id} does not match the staged document"
                )
            selected.append(doc)

        instructed_by = _require_text(request.get("instructed_by"), "instructed_by", _MAX_SHORT_TEXT)
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        doc_summaries = [{"name": d["name"], "sha256": d["sha256"]} for d in selected]
        # The intake's submission contract (its module docstring). The manifest
        # is REBUILT from the broker-verified selection — the intake re-checks
        # that it maps 1:1 onto the run's docs with matching hashes.
        submission = {
            "phase": "install",
            "scope": "firm",
            "run_id": run_id,
            "staging_id": staging_id,
            "output_class": output_class,
            "property": prop,
            "spec_body": body,
            "spec_sha256": spec_digest,
            "assertions": assertions,
            "corpus_manifest": [{"doc_id": d["doc_id"], "sha256": d["sha256"]} for d in selected],
            # Provenance for the audit trail, never authorization — the broker
            # cannot verify a claimed instructor (same posture as corrections
            # ``stated_by``); the authorization gate is the admin hook seat-side.
            "instructed_by": instructed_by,
            "source_ref": source_ref,
            "created_at": time.time(),
        }
        row = {
            "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
            "actor": "operator",
            "actor_role": "agent",
            "metadata": json.dumps(
                {
                    "phase": "install",
                    "run_id": run_id,
                    "staging_id": staging_id,
                    "output_class": output_class,
                    "property": prop,
                    "spec_sha256": spec_digest,
                    "docs": doc_summaries,
                    "doc_count": len(selected),
                    "assertion_count": len((assertions or {}).get("rules") or []),
                    "instructed_by": instructed_by,
                    "source_ref": source_ref,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        self._ledger.append(row)
        # Install MOVES the manifest docs into the run. The staging set itself
        # is deliberately NOT removed here: the intake's leak check reads the
        # root-owned analysis/approved_strings.json out of it DURING the run,
        # and the intake purges the whole set (analysis included, as root)
        # after the run completes — pass or fail.
        self._materialize_run(run_id, submission, selected, move=True)
        return {"ok": True, "run_id": run_id, "phase": "install", "status": "queued"}

    def _submit_firm_adjust(self, request: dict[str, Any], run_id: str) -> dict[str, Any]:
        """Commit one confirmed sentence as a standing adjustment.

        A PROPOSAL ID IS NOT OPTIONAL HERE. The whole control on this path is
        that a person was shown the rule and answered; a submit that carried its
        own text would be the agent writing a firm-wide rule off its own reading
        of an email, which is the witness-never-author line ADR 0085 §4 moved
        only as far as "an admin's confirmed sentence".

        WHO IS RECORDED AS WHAT. ``instructed_by`` on the ROW is whoever stated
        the rule — often a paralegal whose firm-level remark waits for an admin.
        ``applied_by`` is the sender confirming this submit. Both ride onto the
        installed adjustment and both render in the spec file, so the firm can
        read who asked for a rule and who put it in force.
        """
        row = self._claim_proposal(request, "firm_adjust")
        subject = row["subject"]
        output_class = _require_class_slug(subject.get("output_class"))
        prop = _require_property(subject.get("property"))
        self._refuse_restated(
            request,
            row,
            {
                "output_class": output_class,
                "property": prop,
                "text": row["text"],
                "spec_body": row["text"],
            },
        )
        applied_by = require_address(request.get("instructed_by"), "instructed_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        if not self._require_pending().consume(row["proposal_id"], run_id):
            # Lost the race to a concurrent confirmation. Refuse rather than
            # install twice: the firm's sentence rendering twice in its own spec
            # file is a worse outcome than one redundant refusal.
            raise EstablishmentValidationError(f"rule {row['proposal_id']} was already committed; it is in effect")

        adjustment = {
            "id": row["proposal_id"],
            "text": row["text"],
            "sha256": row["text_sha256"],
            "instructed_by": row["instructed_by"],
            "applied_by": applied_by,
            "at": _iso_utc(),
        }
        submission = {
            "phase": "install",
            "scope": "firm_adjust",
            "run_id": run_id,
            "output_class": output_class,
            "property": prop,
            "adjustment": adjustment,
            "instructed_by": applied_by,
            "source_ref": source_ref,
            "created_at": time.time(),
        }
        self._ledger.append(
            {
                "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(
                    {
                        "phase": "install",
                        "scope": "firm_adjust",
                        "run_id": run_id,
                        "proposal_id": row["proposal_id"],
                        "output_class": output_class,
                        "property": prop,
                        # Digest, never the sentence (ADR 0083's retention
                        # posture). It is also what lets a later reader prove
                        # the committed rule is the proposed one, since the
                        # RULE_PROPOSED row carries the same digest.
                        "spec_sha256": row["text_sha256"],
                        "instructed_by": row["instructed_by"],
                        "applied_by": applied_by,
                        "for_admin": row["for_admin"],
                        "source_ref": source_ref,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        self._materialize_run(run_id, submission, [], move=False)
        return {
            "ok": True,
            "run_id": run_id,
            "phase": "install",
            "scope": "firm_adjust",
            "proposal_id": row["proposal_id"],
            "status": "queued",
        }

    def _submit_person(self, request: dict[str, Any], run_id: str) -> dict[str, Any]:
        """A person-scoped install: the speaker's own preferences, docs-less.

        Refuses every firm-path field a person submit must not carry —
        "present" means NON-NULL (the overlay sends ``staging_id: null``, key
        present). The subject was already pinned to the attributed sender by
        the seat-side predicate; the broker re-validates SHAPE only, and the
        intake re-validates shape + roster (defense in depth, same split as
        the firm path).

        WITH A ``proposal_id`` (ss-console#2529) the person and the body come
        out of the pending row rather than off the wire — the same readback
        discipline the firm path requires, offered here because a person who is
        asked "shall I remember that?" and says yes has told the Operator
        something an unconfirmed guess at their words has not. Without one the
        original direct path stands unchanged.

        ``append`` adds to the existing preference instead of replacing it.
        """
        for forbidden in ("staging_id", "corpus_manifest", "output_class", "property"):
            if request.get(forbidden) is not None:
                raise EstablishmentValidationError(f"{forbidden} must not be supplied on a person-scoped submit")
        append_raw = request.get("append", False)
        if not isinstance(append_raw, bool):
            raise EstablishmentValidationError("append must be a boolean")

        proposal_id: str | None = None
        if request.get("proposal_id") is not None:
            claimed = self._claim_proposal(request, "person")
            person = require_address(claimed["subject"].get("person"), "subject.person")
            self._refuse_restated(
                request,
                claimed,
                {"person": person, "spec_body": claimed["text"], "text": claimed["text"]},
            )
            confirmer = require_address(request.get("instructed_by"), "instructed_by")
            if confirmer != person:
                raise EstablishmentValidationError(
                    "a personal rule is confirmed by the person it belongs to; "
                    f"{confirmer} cannot confirm a preference for {person}"
                )
            if not self._require_pending().consume(claimed["proposal_id"], run_id):
                raise EstablishmentValidationError(
                    f"rule {claimed['proposal_id']} was already committed; it is in effect"
                )
            proposal_id = claimed["proposal_id"]
            body = claimed["text"]
            spec_digest = claimed["text_sha256"]
            instructed_by = confirmer
        else:
            person = require_address(request.get("person"), "person")
            body_raw = request.get("spec_body")
            if not isinstance(body_raw, str):
                raise EstablishmentValidationError("spec_body must be a string")
            body = normalize_lf(body_raw).strip()
            if not body:
                raise EstablishmentValidationError("spec_body must not be empty")
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > MAX_SPEC_BODY_BYTES:
                raise EstablishmentValidationError(
                    f"spec_body is {len(body_bytes)} bytes after LF normalization; the ceiling is {MAX_SPEC_BODY_BYTES}"
                )
            spec_digest = sha256(body_bytes).hexdigest()
            instructed_by = _require_text(request.get("instructed_by"), "instructed_by", _MAX_SHORT_TEXT)

        assertions = self._validate_assertions(request.get("assertions"))
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        submission = {
            "phase": "install",
            "scope": "person",
            "run_id": run_id,
            "person": person,
            "spec_body": body,
            "spec_sha256": spec_digest,
            "assertions": assertions,
            "append": append_raw,
            # Provenance for the audit trail, never authorization (firm-path
            # posture; the authorization gate is the seat-side predicate).
            "instructed_by": instructed_by,
            "source_ref": source_ref,
            "created_at": time.time(),
        }
        metadata: dict[str, Any] = {
            "phase": "install",
            "scope": "person",
            "run_id": run_id,
            "person": person,
            "spec_sha256": spec_digest,
            "assertion_count": len((assertions or {}).get("rules") or []),
            "append": append_raw,
            "instructed_by": instructed_by,
            "source_ref": source_ref,
        }
        if proposal_id is not None:
            metadata["proposal_id"] = proposal_id
        row = {
            "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
            "actor": "operator",
            "actor_role": "agent",
            "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        }
        self._ledger.append(row)
        self._materialize_run(run_id, submission, [], move=False)
        result = {"ok": True, "run_id": run_id, "phase": "install", "status": "queued"}
        if proposal_id is not None:
            result["proposal_id"] = proposal_id
        return result

    def _validate_assertions(self, value: Any) -> dict[str, Any] | None:
        """Shape-and-bound check for assertions.

        The wire shape is an OBJECT carrying a ``rules`` list (the intake reads
        ``assertions.get("rules")`` and forwards the rules to the selftest
        compiler). Full rule-schema validation is deliberately NOT here: the
        selftest owns the rule schema and refuses malformed rules (exit 1,
        design §5). The broker guarantees the payload is a bounded JSON object
        whose rules are objects, and nothing else.
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            raise EstablishmentValidationError("assertions must be an object (with an optional 'rules' list)")
        rules = value.get("rules")
        if rules is not None:
            if not isinstance(rules, list):
                raise EstablishmentValidationError("assertions.rules must be a list")
            if len(rules) > _MAX_ASSERTIONS:
                raise EstablishmentValidationError(
                    f"assertions.rules holds {len(rules)} rules; the ceiling is {_MAX_ASSERTIONS}"
                )
            for index, entry in enumerate(rules):
                if not isinstance(entry, dict):
                    raise EstablishmentValidationError(f"assertions.rules[{index}] must be an object")
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > _MAX_ASSERTIONS_BYTES:
            raise EstablishmentValidationError(
                f"assertions serialize to {len(serialized)} bytes; the ceiling is {_MAX_ASSERTIONS_BYTES}"
            )
        return json.loads(serialized)

    def _materialize_run(
        self,
        run_id: str,
        submission: dict[str, Any],
        docs: list[dict[str, Any]],
        move: bool,
    ) -> None:
        """Assemble the run in a dot-prefixed temp dir, then atomically rename.

        The root intake polls the runs dir and ignores dot-prefixed entries, so
        it can never observe a half-written submission (same-filesystem rename
        is atomic).
        """
        tmp_dir = self.runs_dir / f".tmp-{run_id}"
        try:
            (tmp_dir / "docs").mkdir(parents=True)
            for doc in docs:
                source_path: Path = doc["_path"]
                target = tmp_dir / "docs" / source_path.name
                if move:
                    source_path.rename(target)
                else:
                    shutil.copyfile(source_path, target)
            (tmp_dir / "submission.json").write_text(json.dumps(submission, sort_keys=True), "utf-8")
            tmp_dir.rename(self.runs_dir / run_id)
        except OSError:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # ------------------------------------------------------------------
    # establish_status
    # ------------------------------------------------------------------

    def _stamp_installed(self, run_id: str, result: dict[str, Any]) -> None:
        """Record ``installed`` durably, because the read that carries it is
        one-shot (ss-console#2546 follow-up).

        The result file is deleted after the first successful read, so the fact
        that a rule went into force lives for exactly one call and then only in
        an audit row nobody queries. That is why the requester was never told:
        every path that wanted to say "your rule is in effect" had to be the
        one call that read the result, and none of them reliably was.

        A column on the proposal instead. It is written from the root-authored
        result and from the broker's own commit record, never from a request
        field, and it is what the seat's outcome view keys on.

        Best-effort: a stamping fault must not cost the caller the result they
        asked for, which is the same rule the ledger append one line up follows.
        """
        if self.pending is None or result.get("status") != STATUS_INSTALLED:
            return
        try:
            proposal_id = self.pending.mark_installed(run_id)
        except sqlite3.Error:
            logger.warning("run %s installed but the proposal could not be stamped", run_id)
            return
        if proposal_id:
            logger.info("rule %s observed installed on run %s", proposal_id, run_id)

    def status(self, request: dict[str, Any]) -> dict[str, Any]:
        """Read a run's result. One-shot: the result file is deleted after the
        first successful read, and its retained trace is the bounded
        ESTABLISHMENT_RESULT audit row (appended before the delete, so a failed
        append leaves the result readable and retryable)."""
        run_id = _require_id(request.get("run_id"), "run_id")
        result_path = self.results_dir / f"{run_id}.json"
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"result for run {run_id} is unreadable; the TTL sweep will clear it") from exc
            if not isinstance(result, dict):
                raise ValueError(f"result for run {run_id} is not an object; the TTL sweep will clear it")
            self._ledger.append(build_result_row(run_id, result))
            self._stamp_installed(run_id, result)
            # One-shot delete. The results dir is 0770 root:workspace-broker
            # (entrypoint-authored; the intake re-hardens to the same, its
            # overlay#221 fix), so this unlink succeeds in production. The
            # guard is resilience only: against a mis-hardened dir the read
            # must still succeed (the agent is owed the result it was
            # promised) and the intake's 30-min TTL sweep becomes the remover.
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": True, "run_id": run_id, "status": "complete", "result": result}
        if (self.runs_dir / run_id).is_dir():
            return {"ok": True, "run_id": run_id, "status": "pending"}
        raise EstablishmentValidationError(
            f"unknown run_id; results are one-shot reads and expire after {RESULT_TTL_SECONDS // 60} minutes"
        )
