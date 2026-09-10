"""The pending-rules table: proposals stated but not yet confirmed.

Split out of ``establishment.py`` (2026-08-24). Owns one SQLite table in the
broker's audit DB and nothing else. ``EstablishmentStore`` composes this class;
the dependency runs one way only, and this module writes no audit action types.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
import secrets
import time
from typing import Any

from .establishment_constants import *  # vocabulary and tuning surface (F403 not enabled; RUF100 keeps this a plain comment)
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

logger = logging.getLogger(__name__)

class PendingRuleStore:
    """Rules stated but not yet confirmed, in the broker-owned audit DB.

    WHY A TABLE AND NOT THE SPOOL. Every inbound email is its own Hermes
    session (the webhook chat id is ``route:delivery_id``), so nothing
    session-keyed survives from the turn that proposes a rule to the turn that
    confirms it. The spool is transient by design and swept on a 30-minute TTL,
    which is shorter than a partner's lunch. This needs to outlive both, and the
    audit DB is already the one file on this Machine the broker uid owns
    read-write and the agent uid can only read — the same reasoning that put the
    job ledger there (``job_ledger.py``): one mount, one uid boundary.

    THE AUDIT LOG IS UNTOUCHED. No verb here writes ``audit_log``; its
    append-only guarantee is the absence of any update or delete verb over that
    table, and these touch only ``pending_rules``.

    BROKER CLOCK ONLY. Every timestamp is read from this process, never off the
    wire. A caller that could supply ``now`` could hold a proposal open forever,
    or expire someone else's.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        # Connection discipline copied from JobLedgerWriter deliberately: a
        # fresh connection per operation, journal_mode=DELETE (keeps the
        # agent-uid mode=ro read seam off the cross-uid -wal/-shm surface), and
        # a busy timeout to serialize concurrent writers.
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(CREATE_PENDING_RULES_SQL)
            # ss-console#2536: a table created by #2529 predates the two act
            # columns. Add them, tolerate the ones already there, and leave the
            # rows alone; ``kind`` defaults to 'rule', which is what those rows
            # are.
            for alter_sql in PENDING_RULES_COLUMN_ALTERS:
                try:
                    conn.execute(alter_sql)
                except sqlite3.OperationalError as err:
                    if "duplicate column" not in str(err):
                        raise
            conn.execute(CREATE_PENDING_RULES_INDEX_SQL)
            conn.commit()
        finally:
            conn.close()

    def sweep(self, now: float | None = None) -> int:
        """Mark expired proposals LAPSED, then delete long-terminal rows.

        Returns rows deleted. Called on every establishment verb, so the table
        stays bounded without a timer.

        THE CHANGE ss-console#2546 MAKES, and why it is the whole point of the
        issue: an expired row used to be deleted here, and deletion is why a
        lapse was silent. The person who asked for the rule got nothing — the
        row that would have let anyone tell them was gone, and "nobody ever
        proposed that" is what the table then said. So expiry now writes
        ``lapsed_at`` and leaves the row, which gives the seat something true to
        report and gives this sweep a second, later job: delete the row once
        every terminal state is older than TERMINAL_RETENTION_SECONDS.

        Ordering matters and is deliberate: mark first, then delete. A row that
        expired long ago is marked on the pass that also becomes eligible to
        delete it, so it is never deleted without first having existed as a
        lapse the seat could have read.
        """
        now = time.time() if now is None else now
        cutoff = now - TERMINAL_RETENTION_SECONDS
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE pending_rules SET lapsed_at=? WHERE "
                "consumed_at IS NULL AND declined_at IS NULL AND lapsed_at IS NULL "
                "AND expires_at < ?",
                (now, now),
            )
            cursor = conn.execute(
                "DELETE FROM pending_rules WHERE "
                "(consumed_at IS NOT NULL AND consumed_at < ?) OR "
                "(declined_at IS NOT NULL AND declined_at < ?) OR "
                "(lapsed_at IS NOT NULL AND lapsed_at < ?)",
                (cutoff, cutoff, cutoff),
            )
            conn.commit()
            return cursor.rowcount or 0
        finally:
            conn.close()

    def create(
        self,
        *,
        scope: str,
        subject: dict[str, Any],
        text: str,
        instructed_by: str,
        for_admin: bool,
        kind: str = "rule",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store one proposal and return it. The id and both times are minted
        here; the digest is computed here over the stored text.

        ``kind`` and ``payload`` carry an ACT (ss-console#2536): the payload is
        the exact field set the confirmed tool call will be made with, stored so
        the commit replays the row rather than the wire.
        """
        now = time.time()
        ttl = ttl_for_kind(kind)
        digest = _hash_text(text)
        payload_json = (
            None
            if payload is None
            else json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
        conn = self._connect()
        try:
            for _attempt in range(8):
                proposal_id = secrets.token_hex(PROPOSAL_ID_HEX_BYTES)
                try:
                    conn.execute(
                        "INSERT INTO pending_rules ("
                        "proposal_id, scope, subject_json, text, text_sha256, "
                        "instructed_by, for_admin, created_at, expires_at, "
                        "kind, payload_json"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            proposal_id,
                            scope,
                            json.dumps(subject, sort_keys=True, separators=(",", ":")),
                            text,
                            digest,
                            instructed_by,
                            1 if for_admin else 0,
                            now,
                            now + ttl,
                            kind,
                            payload_json,
                        ),
                    )
                    conn.commit()
                    break
                except sqlite3.IntegrityError:
                    # A live proposal already holds that id. Mint another
                    # rather than reuse it: two rules answering to one tag is
                    # exactly the ambiguity the tag exists to remove.
                    continue
            else:
                raise EstablishmentValidationError(
                    "could not mint a free proposal id; try again"
                )
        finally:
            conn.close()
        return {
            "proposal_id": proposal_id,
            "scope": scope,
            "subject": subject,
            "text": text,
            "text_sha256": digest,
            "instructed_by": instructed_by,
            "for_admin": for_admin,
            "created_at": now,
            "expires_at": now + ttl,
            "kind": kind,
            "payload": payload,
        }

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        """One row in ANY state — open, expired, or consumed.

        Deliberately not filtered: the submit path needs to tell "expired" from
        "already committed" from "never existed", and those are three different
        sentences to a person waiting on an answer.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM pending_rules WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._hydrate(row) if row is not None else None

    def open_for(
        self, sender: str, include_for_admin: bool, now: float | None = None
    ) -> list[dict[str, Any]]:
        """Unconsumed, unexpired rules this sender may confirm.

        Their OWN pending rules always; every rule awaiting an admin only when
        the caller says so. The seat passes ``include_for_admin`` for an admin
        sender and not otherwise — the admin decision is one this uid cannot
        make, so it is taken as an argument rather than guessed at.
        """
        now = time.time() if now is None else now
        # ss-console#2546: declined and lapsed join consumed as reasons a row is
        # no longer confirmable. Without the declined_at clause an administrator's
        # "no" would leave the rule sitting in this list, and the Operator would
        # go on offering the firm a rule that has already been refused.
        # ss-console#2546 (the operations half): and an OPERATIONS request is
        # excluded outright, in SQL, because it is not a thing anybody at the
        # firm can confirm. It is for_admin, so without this clause every
        # administrator on the seat would be handed a routine change in the list
        # of things a "yes" applies to. The seat also skips the kind, and
        # ``consume`` refuses it, and ``establish_submit`` names it -- three
        # refusals for the one failure worth three.
        sql = (
            "SELECT * FROM pending_rules WHERE consumed_at IS NULL "
            "AND declined_at IS NULL AND lapsed_at IS NULL AND expires_at >= ? "
            "AND kind != 'ops_request' "
            "AND (instructed_by = ?"
        )
        params: list[Any] = [now, sender]
        if include_for_admin:
            sql += " OR for_admin = 1"
        sql += ") ORDER BY created_at ASC"
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._hydrate(row) for row in rows]

    def unreported_outcomes_for(
        self, sender: str | None, now: float | None = None
    ) -> list[dict[str, Any]]:
        """Rows that ENDED and whose author has not been told: declined by an
        administrator, lapsed unanswered, or observed installed.

        With a ``sender``, only rows THAT PERSON stated (``instructed_by``). A
        decline is news for the person who asked, not for the administrator who
        gave it, and an administrator shown every lapse in the firm is being
        shown other people's business.

        With ``sender=None``, every unreported outcome on the seat. That is not
        a widening of who may be told anything: each row still names its own
        author, and the only caller is the seat's own lapse sweeper, which sends
        each note to that author and nobody else. It exists because a lapse has
        no person in front of it by definition. Waiting for the requester's next
        message would make the report depend on the person continuing to talk to
        an Operator that has just gone silent on them, and enumerating the
        firm's authored people instead would miss anyone the roster covers by
        domain rather than by name (the paralegal this whole issue is about).

        THE SENDERLESS SHAPE ALSO HIDES A ROW ANOTHER OBSERVER IS CURRENTLY
        SENDING (ss-console#2546, the duplicate-letter fix), for
        ``NOTIFY_CLAIM_STALE_SECONDS`` and no longer. This is the cheap half of
        the once-guard, not the guard: two sweepers can still list the same row
        in the same instant, and what stops the second letter is
        :meth:`claim_notify`. What this buys is that the ordinary case does not
        even reach the claim, and that a row whose claimant died comes back into
        the list rather than going quiet forever.

        The SENDER-scoped shape is deliberately left alone. It answers "what
        does this person still need to hear", which is a true answer whether or
        not a sweeper is mid-send; the send itself is gated by the claim.
        """
        now = time.time() if now is None else now
        sql = (
            "SELECT * FROM pending_rules WHERE lapse_notified_at IS NULL AND ("
            # The two non-committing ends.
            "(consumed_at IS NULL AND (declined_at IS NOT NULL OR lapsed_at IS NOT NULL))"
            # ss-console#2546 follow-up: and the committing one. A rule an
            # administrator APPLIED is an outcome the person who asked is owed,
            # and it was the one outcome this view could not express -- its
            # every arm required consumed_at IS NULL, so a rule that went into
            # force fell out of the list that exists to report outcomes.
            #
            # THE ARM IS installed_at, NOT consumed_at, and that is the whole
            # honesty of it. Committed means the submission reached the intake
            # spool; installed means somebody read the run's result and saw the
            # word. Between them sits a converge window and a failure mode, and
            # a sweeper firing on consumed_at would mail "your rule is in
            # effect" about a rule that never installed.
            " OR (installed_at IS NOT NULL AND for_admin = 1)"
            ")"
        )
        params: tuple[Any, ...] = ()
        if sender is not None:
            sql += " AND instructed_by = ?"
            params = (sender,)
        else:
            sql += " AND (notify_claimed_at IS NULL OR notify_claimed_at < ?)"
            params = (now - NOTIFY_CLAIM_STALE_SECONDS,)
        sql += " ORDER BY created_at ASC"
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._hydrate(row) for row in rows]

    def claim_notify(
        self, proposal_id: str, claimed_by: str, now: float | None = None
    ) -> bool:
        """Take the right to send ONE row's outcome letter. True iff THIS call
        took it.

        The conditional UPDATE is the whole control, for the reason ``consume``
        and ``decline`` are conditional: claim-once has to be enforced by the
        database, because the two processes racing for it are two processes and
        no amount of in-memory bookkeeping in either one can see the other.
        overlay#315 put this claim in memory and the letter still went twice.

        Every condition is in the WHERE clause:
          - unclaimed, OR claimed longer ago than ``NOTIFY_CLAIM_STALE_SECONDS``
            (a claimant that died hands the row back by falling silent, because
            an un-sent letter is worse than a late one);
          - not already reported -- ``lapse_notified_at`` is the durable mark and
            it outranks any claim;
          - actually terminal. A row with no outcome has no letter to send, so a
            claim on one is refused rather than parked.

        A refusal is a ``False``, never an exception: losing this race is the
        system working, and the caller's correct response is to send nothing.
        """
        now = time.time() if now is None else now
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE pending_rules SET notify_claimed_at=?, notify_claimed_by=? "
                "WHERE proposal_id=? "
                "AND (notify_claimed_at IS NULL OR notify_claimed_at < ?) "
                "AND lapse_notified_at IS NULL "
                "AND (declined_at IS NOT NULL OR lapsed_at IS NOT NULL "
                "OR installed_at IS NOT NULL)",
                (now, claimed_by, proposal_id, now - NOTIFY_CLAIM_STALE_SECONDS),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def release_notify(self, proposal_id: str) -> bool:
        """Hand a claimed row back, unsent. True iff THIS call released it.

        The path is a send that FAILED: the claimant knows the letter did not go,
        and the honest thing is to give the row back immediately rather than
        leave the next observer waiting out the stale window.

        ``lapse_notified_at IS NULL`` is the guard, and it is the same one
        ``claim_notify`` carries: once a letter is recorded as sent, nothing may
        reopen the row for a second one. A release on a reported row is a
        ``False``, not a clearing.
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE pending_rules SET notify_claimed_at=NULL, notify_claimed_by=NULL "
                "WHERE proposal_id=? AND notify_claimed_at IS NOT NULL "
                "AND lapse_notified_at IS NULL",
                (proposal_id,),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def find_open_duplicate(
        self, *, instructed_by: str, scope: str, text: str, now: float | None = None
    ) -> dict[str, Any] | None:
        """An OPEN row this same person already stated, word for word.

        Matched on the normalized text's digest rather than the text, so
        "same rule" means here what it means everywhere else in this module.

        Why it exists (ss-console#2546): a rule now emails an administrator. A
        person who re-sends the same sentence, or whose mail client retries,
        would otherwise page that administrator twice for one request, and the
        second page would carry a different tag - so answering one would leave
        the other open. Returning the row the caller already has is both the
        cheaper and the truer answer.
        """
        now = time.time() if now is None else now
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM pending_rules WHERE instructed_by = ? AND scope = ? "
                "AND text_sha256 = ? AND consumed_at IS NULL AND declined_at IS NULL "
                "AND lapsed_at IS NULL AND expires_at >= ? ORDER BY created_at ASC LIMIT 1",
                (instructed_by, scope, _hash_text(text), now),
            ).fetchone()
        finally:
            conn.close()
        return self._hydrate(row) if row is not None else None

    def decline(self, proposal_id: str, declined_by: str) -> bool:
        """Refuse one proposal on an administrator's word. True iff THIS call
        declined it.

        A conditional UPDATE for the same reason ``consume`` is one: decline-once
        is enforced by the database, not by a read-then-write the caller could
        interleave. The WHERE clause carries every condition rather than trusting
        a check performed above it - open, not already answered, not expired,
        and ``for_admin`` (a rule somebody stated about their own work is not an
        administrator's to refuse; they simply do not confirm it).
        """
        now = time.time()
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE pending_rules SET declined_at=?, declined_by=? "
                "WHERE proposal_id=? AND consumed_at IS NULL AND declined_at IS NULL "
                "AND lapsed_at IS NULL AND for_admin = 1 AND expires_at >= ?",
                (now, declined_by, proposal_id, now),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def mark_outcome_reported(self, proposal_id: str) -> bool:
        """Record that the person who asked has been told how their rule ended.
        True iff THIS call marked it.

        Conditional again, and the condition is what stops a second note: a row
        can only be marked once, and only once it actually HAS an outcome. A
        seat that retries the send loses the race and sends nothing. That is
        also what makes this the cross-path lock for ss-console#2546's install
        notice: three observers can reach it and exactly one wins the UPDATE.

        ``installed_at`` joins the two non-committing ends as an outcome, so an
        applied rule can be marked reported at all. Without it the seat could
        send the note and never record having sent it, and every later observer
        would send it again.
        """
        now = time.time()
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE pending_rules SET lapse_notified_at=? WHERE proposal_id=? "
                "AND lapse_notified_at IS NULL "
                "AND (declined_at IS NOT NULL OR lapsed_at IS NOT NULL "
                "OR installed_at IS NOT NULL)",
                (now, proposal_id),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def mark_installed(self, run_id: str, now: float | None = None) -> str:
        """Record that the run that committed a rule was observed INSTALLED.

        Returns the proposal id this stamped, or ``""`` when it stamped nothing
        (no committed row carries that run, or one already does).

        THE ONLY WRITER IS :meth:`EstablishmentStore.status`, on the path where
        the broker has just read a root-authored result whose status is
        ``installed``. Nothing the agent says reaches this: the word comes off a
        file the intake wrote as root, and the run id comes off the row the
        broker itself stamped at commit. That is what lets the seat's sweeper
        treat the column as grounds to tell somebody their rule is in force.

        Conditional, like every other mark here, so two reads of one result
        stamp once.
        """
        now = time.time() if now is None else now
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT proposal_id FROM pending_rules WHERE consumed_run_id=? "
                "AND consumed_at IS NOT NULL AND installed_at IS NULL",
                (run_id,),
            ).fetchone()
            if row is None:
                return ""
            cursor = conn.execute(
                "UPDATE pending_rules SET installed_at=? WHERE consumed_run_id=? "
                "AND consumed_at IS NOT NULL AND installed_at IS NULL",
                (now, run_id),
            )
            conn.commit()
            return str(row["proposal_id"]) if cursor.rowcount else ""
        finally:
            conn.close()

    def resolve_ops(
        self,
        proposal_id: str,
        outcome: str,
        resolved_by: str,
        reason: str | None,
        now: float | None = None,
    ) -> bool:
        """End one operations request on SMD's word. True iff THIS call ended it.

        Conditional for the same reason every other terminal write here is: a
        second answer to the same request loses the race and changes nothing,
        so the person who asked is told once and told one thing. The WHERE
        clause carries every condition, including the kind -- a rule and an act
        end through their own verbs, and this one must not be able to end either.

        WHICH COLUMNS EACH OUTCOME WRITES, and why they are the existing ones
        rather than a new state machine:

        ``done``       ``consumed_at`` and ``installed_at``. The seat's sweeper
                       reports an outcome off ``unreported_outcomes_for``, whose
                       committing arm is ``installed_at IS NOT NULL AND
                       for_admin = 1``; an ops row is for_admin, so stamping
                       both is what makes "SMD set this up" reach the requester
                       through the path that already exists. There is no run and
                       no spool: SMD made the change, and ``consumed_run_id``
                       stays NULL, which is what ``mark_installed`` needs to not
                       match it.
        ``declined``   ``declined_at`` and ``declined_by``. Same arm the firm's
                       own declines use, so the same sweeper tells the requester.
        ``withdrawn``  ``lapsed_at`` AND ``lapse_notified_at`` together, in one
                       statement. That pair is the row's way of saying "ended,
                       and nobody is owed a note" -- which is exactly right when
                       the seat could not get the request out of the building:
                       the requester was already told, in the refusal they got
                       in the same turn, that nothing was sent.
        """
        now = time.time() if now is None else now
        stamps: tuple[Any, ...]
        if outcome == "done":
            assignment = "consumed_at=?, installed_at=?"
            stamps = (now, now)
        elif outcome == "declined":
            assignment = "declined_at=?, declined_by=?"
            stamps = (now, resolved_by)
        elif outcome == "withdrawn":
            assignment = "lapsed_at=?, lapse_notified_at=?"
            stamps = (now, now)
        else:  # pragma: no cover - ops_resolve validates before calling
            raise EstablishmentValidationError(f"unknown outcome {outcome!r}")
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"UPDATE pending_rules SET {assignment}, resolved_by=?, outcome_reason=? "
                "WHERE proposal_id=? AND kind='ops_request' AND consumed_at IS NULL "
                "AND declined_at IS NULL AND lapsed_at IS NULL",
                (*stamps, resolved_by, reason, proposal_id),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def mark_ask_sent(self, proposal_id: str, now: float | None = None) -> bool:
        """Record that SMD has been asked, once, for an answer this parser reads.

        True iff THIS call marked it. Conditional and kind-gated like every other
        mark here, and it requires the row to still be OPEN: an ask about a
        request that has already ended is noise to the person who ended it.

        Why the column exists at all (the critique's item 4): a reply from SMD
        that says neither "done" nor "no" leaves the row open, and leaving it
        there silently is the same silence in a new place. So the seat asks for
        the two words -- and asks once, because a per-turn re-ask is how a
        helpful nudge becomes a mail loop.
        """
        now = time.time() if now is None else now
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE pending_rules SET ask_sent_at=? WHERE proposal_id=? "
                "AND kind='ops_request' AND ask_sent_at IS NULL AND consumed_at IS NULL "
                "AND declined_at IS NULL AND lapsed_at IS NULL",
                (now, proposal_id),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def consume(self, proposal_id: str, run_id: str) -> bool:
        """Mark one proposal committed. True iff THIS call consumed it.

        A conditional UPDATE, so consume-once is enforced by the database rather
        than by a read-then-write the caller could interleave. A second
        confirmation of the same rule loses the race and is told the rule is
        already in effect, which is both true and the only safe answer: the
        alternative is the firm's sentence rendered twice.

        AN OPERATIONS ROW IS REFUSED HERE (ss-console#2546), in the WHERE clause
        rather than above it. ``_claim_proposal`` already names the kind and
        gives the reader a sentence; this is the structural half, so a future
        caller that reaches ``consume`` by some other route still cannot turn a
        routine change into something the firm committed. ``ops_resolve`` is the
        only writer that ends one of those rows.
        """
        now = time.time()
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE pending_rules SET consumed_at=?, consumed_run_id=? "
                "WHERE proposal_id=? AND consumed_at IS NULL "
                "AND kind != 'ops_request'",
                (now, run_id, proposal_id),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
        try:
            subject = json.loads(row["subject_json"])
        except ValueError:
            subject = {}
        payload: dict[str, Any] | None = None
        raw_payload = row["payload_json"] if "payload_json" in row.keys() else None
        if raw_payload:
            try:
                decoded = json.loads(raw_payload)
            except ValueError:
                decoded = None
            payload = decoded if isinstance(decoded, dict) else None
        return {
            "proposal_id": row["proposal_id"],
            "scope": row["scope"],
            "subject": subject if isinstance(subject, dict) else {},
            "text": row["text"],
            "text_sha256": row["text_sha256"],
            "instructed_by": row["instructed_by"],
            "for_admin": bool(row["for_admin"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "consumed_at": row["consumed_at"],
            "consumed_run_id": row["consumed_run_id"],
            # A row written before #2536 has neither column; it is a rule, and
            # reading it as one is the migration working rather than failing.
            "kind": (row["kind"] if "kind" in row.keys() else None) or "rule",
            "payload": payload,
            # ss-console#2546. Same tolerance for a row written before the four
            # columns existed: absent reads as NULL, which is "open".
            "declined_at": _column(row, "declined_at"),
            "declined_by": _column(row, "declined_by"),
            "lapsed_at": _column(row, "lapsed_at"),
            "lapse_notified_at": _column(row, "lapse_notified_at"),
            "installed_at": _column(row, "installed_at"),
            # ss-console#2546 (the operations half); same tolerance again.
            "resolved_by": _column(row, "resolved_by"),
            "outcome_reason": _column(row, "outcome_reason"),
            "ask_sent_at": _column(row, "ask_sent_at"),
            # ss-console#2546 (the duplicate-letter fix); same tolerance again.
            "notify_claimed_at": _column(row, "notify_claimed_at"),
            "notify_claimed_by": _column(row, "notify_claimed_by"),
        }

