"""What the evidence packet reads, and what those reads can and cannot say.

Split out of :mod:`adapter.evidence.packet` (2026-09-11, code review 2026-09-10,
Architecture 2): the builder there stays the orchestrator; this module is the
read side. It holds the read-executor protocol and its sqlite implementation,
the coverage model (:class:`AuditCoverage`, :class:`ChainPin`) that turns a
zero into an answerable or an unanswerable one, the nine ``_fetch_*`` reads
against the per-customer D1 snapshot, and the two refusal messages the
builder raises when a read says the packet must not be written.

Nothing here writes, renders, or signs. The rules that govern the reads
(no fabrication: an absent table is honest-empty for a dump and a distinct
fact for the coverage tally; the chain pin is looked up ledger-wide, never
period-scoped) are stated on the functions that apply them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Sequence

log = logging.getLogger("aie.evidence.packet")


# ---------------------------------------------------------------------------
# Audit coverage: what the audit log can and cannot say about this scope
# ---------------------------------------------------------------------------


# The export's own chain-of-custody rows are excluded from the coverage
# tally. A COMPLIANCE_PACKET_EXPORTED row records an act performed on a
# packet, not agent work performed on a matter, and it carries its own
# scope in metadata. Counting it would make every repeat export of a
# quiet matter look like an unresolvable gap.
_COVERAGE_EXCLUDED_ACTION_TYPE = "COMPLIANCE_PACKET_EXPORTED"

#: A chain head is a sha256 hexdigest -- ``compute_row_hash`` in
#: ``operator/workspace_broker/chain.py``. Matched, never trusted: see
#: PacketRequest.validate for why a malformed pin is refused up front.
_CHAIN_HEAD_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ChainPin:
    """Whether a head recorded off the Machine is still in this ledger (ss#2500).

    THE PROBLEM IT ANSWERS. The ledger is a hash chain, and this packet has
    always been able to say a mutated or deleted MIDDLE row would show. It could
    never say anything about rows cut off the END, because the surviving prefix
    is a valid chain -- shown, not argued: against a live 1,473-row export,
    deleting the last 50 rows and deleting the last 1 row both verified INTACT
    (vfy_01M0H8D1CV2X8J9ZACMAC8E6E2).

    A pin is the missing input. The console records the chain head from every
    heartbeat into ``audit_head_history``; a packet built with one of those heads
    can state that the ledger still contains a row that existed at a moment the
    Machine could not reach backwards into.

    ``present`` False with a pin supplied is a BREAK, and the builder halts on
    it: a compliance packet asserting a complete record over a ledger that lost
    rows is the exact artifact this whole mechanism exists to prevent.
    """

    #: False when the export source could not be asked at all -- no audit_log
    #: table, or a snapshot taken before the hash-chain columns were preserved.
    #: Kept distinct from ``present`` for the reason AuditCoverage.table_present
    #: exists: "could not look" and "looked and it is gone" are different facts
    #: and a compliance artifact must not present one as the other.
    pinned_head: Optional[str]
    present: bool
    chain_readable: bool
    source: str

    @property
    def was_checked(self) -> bool:
        return self.pinned_head is not None

    def narrative_lines(self) -> List[str]:
        """Plain-language statement for the README. One wording, one surface."""
        if not self.was_checked:
            return [
                "No externally pinned chain head was supplied for this export, so "
                "the audit rows here were checked for internal consistency only. "
                "Internal consistency does not detect rows removed from the END "
                "of the log: what remains after such a removal is itself a valid "
                "chain. Read this packet's audit section as complete only if you "
                "have an independent record of where the log ended.",
                "Such a record exists and was not used for this export: "
                f"{self.source}. Request a packet built against one if "
                "completeness of the log itself is the question you need "
                "answered.",
            ]
        return [
            "This export was checked against a chain head recorded outside the "
            f"Operator's own machine before the export was taken ({self.source}). "
            f"That head, {self.pinned_head}, is still present in this ledger, so "
            "no row that existed when it was recorded has since been removed, "
            "reordered, or altered.",
            "That check covers rows OLDER than the recorded head. Rows written "
            "after it were not yet covered by any external record when this "
            "export was taken.",
        ]

    def to_dict(self) -> dict:
        return {
            "pinned_head": self.pinned_head,
            "checked": self.was_checked,
            "present": self.present,
            "chain_readable": self.chain_readable,
            "source": self.source,
        }


#: Where a pin comes from, stated in the packet so a reader can go and check it.
CHAIN_PIN_SOURCE = (
    "the audit_head_history table on the SMD control plane, appended from every heartbeat the Operator sends"
)


def _rows_phrase(count: int) -> str:
    """ "1 row" / "4130 rows". The packet is read by lawyers; "1 rows"
    in a compliance artifact undercuts everything around it."""
    return "1 row" if count == 1 else f"{count} rows"


def _rows_verb(count: int) -> str:
    return "carries" if count == 1 else "carry"


@dataclass(frozen=True)
class AuditCoverage:
    """The coverage boundary of one packet's audit section.

    Answers the question an auditor actually has when a section is
    empty: is this "nothing happened", or "the system cannot say"?

    ``table_present`` is False when the export source has no
    ``audit_log`` table at all. That is not a zero; it is a packet that
    cannot report on activity, and it says so.
    """

    matter: str
    table_present: bool
    rows_in_period: int
    rows_matching_matter: int
    rows_unattributed: int
    unattributed_first_ts: Optional[str] = None
    unattributed_last_ts: Optional[str] = None
    gap_acknowledged: bool = False
    acknowledged_by: Optional[str] = None

    @property
    def is_customer_wide(self) -> bool:
        return self.matter == "all"

    @property
    def has_unattributed_rows(self) -> bool:
        return self.rows_unattributed > 0

    @property
    def is_unanswerable_empty(self) -> bool:
        """True when this packet's audit section would be empty for a
        reason the auditor could mistake for "no activity".

        A customer-wide export is never unanswerable: it includes every
        row regardless of attribution. A matter-scoped export is
        unanswerable when it matched nothing AND either the source had
        no audit table or the period holds rows that carry no
        attribution and so may belong to this matter.
        """
        if self.is_customer_wide:
            return False
        if self.rows_matching_matter > 0:
            return False
        return (not self.table_present) or self.has_unattributed_rows

    @property
    def zero_is_complete(self) -> bool:
        """True when an empty audit section is a truthful, complete zero."""
        return (
            not self.is_customer_wide
            and self.table_present
            and self.rows_matching_matter == 0
            and not self.has_unattributed_rows
        )

    def _span(self) -> str:
        first = self.unattributed_first_ts
        last = self.unattributed_last_ts
        if first and last and first == last:
            return f"at {first}"
        return f"from {first or 'unknown'} to {last or 'unknown'}"

    def narrative_lines(self) -> List[str]:
        """Plain-language coverage statement shared by README and PDF.

        One wording, two surfaces: a compliance artifact that describes
        its own limits differently in two places invites the question of
        which one is the real one.
        """
        if not self.table_present:
            return [
                "The audit_log table was not present in the export source read "
                "for this packet. This packet therefore cannot report on agent "
                "activity at all. Do NOT read its empty audit section as "
                "evidence that nothing happened.",
            ]

        if self.is_customer_wide:
            lines = [
                "This export is customer wide. Every audit row in the period is "
                "included regardless of matter attribution: "
                f"{_rows_phrase(self.rows_in_period)}.",
            ]
            if self.has_unattributed_rows:
                lines.append(
                    f"Of those, {self.rows_unattributed} "
                    f"{_rows_verb(self.rows_unattributed)} no matter "
                    f"attribution ({self._span()}). They are included here "
                    "because this export is not scoped to a matter, but they "
                    "cannot be assigned to any single matter."
                )
            return lines

        if self.rows_matching_matter > 0:
            lines = [
                f"This export is scoped to matter {self.matter}. "
                f"{_rows_phrase(self.rows_matching_matter)} in the period "
                f"{_rows_verb(self.rows_matching_matter)} that attribution and "
                f"{'is' if self.rows_matching_matter == 1 else 'are'} included "
                "in 03-audit-log.csv.",
            ]
            if self.has_unattributed_rows:
                lines.append(
                    f"A further {_rows_phrase(self.rows_unattributed)} in this "
                    f"period {_rows_verb(self.rows_unattributed)} no matter "
                    f"attribution at all ({self._span()}). They are NOT in this "
                    "packet. They may belong to this matter, to another matter, "
                    "or to no matter, and this system cannot tell which. Their "
                    "contents are withheld from a matter-scoped export because "
                    "they may concern other clients. Request the customer-wide "
                    "export if they need to be enumerated."
                )
                lines.append("Read the counts in this packet as a floor for this matter, not as a complete tally.")
            else:
                lines.append(
                    "Every audit row in this period carries a matter "
                    "attribution, so nothing in the period is unaccounted for."
                )
            return lines

        if self.zero_is_complete:
            return [
                f"This export is scoped to matter {self.matter}. No audit rows "
                "in this period carry that attribution, and no rows in this "
                "period lack attribution. This zero is complete: nothing was "
                "recorded against this matter during this period.",
            ]

        lines = [
            f"This export is scoped to matter {self.matter} and its audit "
            'section is EMPTY. Read that as "this system cannot answer the '
            'question", NOT as "nothing happened on this matter".',
            f"No audit row in this period carries an attribution to matter "
            f"{self.matter}. At the same time, "
            f"{_rows_phrase(self.rows_unattributed)} in this period "
            f"{_rows_verb(self.rows_unattributed)} no matter attribution at all "
            f"({self._span()}). Matter attribution was added to the audit "
            "schema after those rows were written and cannot be reconstructed "
            "for them. Any of them may concern this matter.",
            "This packet cannot show that nothing happened on this matter. It "
            "can only show that nothing was recorded under that label.",
        ]
        if self.acknowledged_by:
            lines.append(
                "The operator who generated this packet acknowledged this gap "
                f"before it was written: {self.acknowledged_by}."
            )
        return lines

    def to_dict(self) -> dict:
        """Structured form for manifest.json and the audit row metadata."""
        return {
            "matter": self.matter,
            "audit_table_present": self.table_present,
            "rows_in_period": self.rows_in_period,
            "rows_matching_matter": self.rows_matching_matter,
            "rows_unattributed": self.rows_unattributed,
            "unattributed_first_ts": self.unattributed_first_ts,
            "unattributed_last_ts": self.unattributed_last_ts,
            "zero_is_complete": self.zero_is_complete,
            "unanswerable_empty": self.is_unanswerable_empty,
            "gap_acknowledged": self.gap_acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "excludes_action_type": _COVERAGE_EXCLUDED_ACTION_TYPE,
        }


# ---------------------------------------------------------------------------
# Read executor protocol
# ---------------------------------------------------------------------------


class ReadExecutor(Protocol):
    """One method: run a SQL SELECT and return list of rows as dicts."""

    async def fetch_all(self, sql: str, params: Sequence[Any]) -> List[dict]: ...


class SqliteReadExecutor:
    """Sqlite-backed read executor for tests + local dev.

    Mirrors :class:`adapter.audit_log.SqliteExecutor` shape. Returns
    rows as dicts keyed by column name.
    """

    def __init__(self, connection) -> None:
        self._conn = connection
        self._conn.row_factory = _row_factory

    async def fetch_all(self, sql: str, params: Sequence[Any]) -> List[dict]:
        cur = self._conn.cursor()
        cur.execute(sql, list(params))
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def _row_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


# ---------------------------------------------------------------------------
# D1 reads
# ---------------------------------------------------------------------------


_AUDIT_LOG_COLUMNS = (
    "id",
    "ts",
    "action_type",
    "actor",
    "actor_role",
    "skill_name",
    "matter_ref",
    "input_digest",
    "output_digest",
    "diff_digest",
    "trust_ceiling",
    "metadata",
)

_BOOT_CHECK_COLUMNS = ("id", "ts", "invariant_num", "passed", "failure_detail")


async def _fetch_audit_log(
    reader: ReadExecutor,
    *,
    period_start: str,
    period_end: str,
    matter: str,
) -> List[dict]:
    if matter == "all":
        sql = "SELECT * FROM audit_log WHERE ts >= ? AND ts <= ? ORDER BY ts ASC, id ASC"
        params: list = [period_start, period_end]
    else:
        sql = "SELECT * FROM audit_log WHERE ts >= ? AND ts <= ? AND matter_ref = ? ORDER BY ts ASC, id ASC"
        params = [period_start, period_end, matter]
    return await _fetch_safe(reader, sql, params)


async def _fetch_chain_pin(
    reader: ReadExecutor,
    *,
    pinned_head: Optional[str],
) -> ChainPin:
    """Is the pinned head still somewhere in this ledger?

    Deliberately NOT scoped to the packet's period or matter. The question is
    whether the LEDGER still holds the row, and a period-scoped lookup would
    report a break every time the pin predated the export window -- a false
    accusation with the same words as a real one.

    A missing ``audit_log`` table is reported as ``table_present=False`` rather
    than as an absent head, for the reason :func:`_fetch_optional` exists: "the
    table is not there" and "the row is not there" are different facts and a
    packet must not present one as the other.
    """
    if pinned_head is None:
        return ChainPin(None, present=False, chain_readable=True, source=CHAIN_PIN_SOURCE)
    try:
        rows = await reader.fetch_all("SELECT row_hash FROM audit_log WHERE row_hash = ? LIMIT 1", [pinned_head])
    except Exception as exc:
        # Two ways the question cannot be asked, and neither is an answer: no
        # audit_log table, and an audit_log without the chain columns (a
        # snapshot written before they were preserved). Anything else re-raises
        # rather than being flattened into a reassuring shape.
        msg = str(exc).lower()
        if "no such table" in msg or "no such column" in msg or "does not exist" in msg:
            log.warning("chain pin lookup skipped (chain columns unreadable): %s", exc)
            return ChainPin(pinned_head, present=False, chain_readable=False, source=CHAIN_PIN_SOURCE)
        raise
    return ChainPin(pinned_head, present=len(rows) > 0, chain_readable=True, source=CHAIN_PIN_SOURCE)


def _chain_pin_refusal_message(pin: ChainPin) -> str:
    if not pin.chain_readable:
        return (
            "A pinned chain head was supplied but this export source carries no "
            "hash-chain columns to look it up in: either there is no audit_log "
            "table, or the snapshot predates the chain columns being preserved. "
            "A packet cannot assert an unbroken record it did not read. Point "
            "--read-db at a current ledger snapshot, or omit --pinned-head and "
            "accept a packet that states its audit section is unchecked for "
            "truncation."
        )
    return (
        f"HALTED: the pinned chain head {pin.pinned_head} is NOT present in this "
        "ledger. A head recorded off the Machine before this export has since "
        "disappeared from the log, which means rows that existed at that moment "
        "are gone: truncated, rewritten, or rolled back to an older copy. There "
        "is no acknowledge flag for this. Do not ship a compliance packet over "
        "it. Escalate per the invariants runbook and preserve the export as it "
        "stands."
    )


async def _fetch_audit_coverage(
    reader: ReadExecutor,
    *,
    period_start: str,
    period_end: str,
    matter: str,
    gap_acknowledged: bool,
    actor: str,
) -> AuditCoverage:
    """Tally what the audit log can and cannot attribute for this scope.

    One aggregate query rather than a second full row fetch: the packet
    needs counts and a time span, not the unattributed rows themselves
    (which it must not disclose in a matter-scoped export).
    """
    sql = (
        "SELECT "
        "COUNT(*) AS rows_in_period, "
        "SUM(CASE WHEN matter_ref IS NULL OR TRIM(matter_ref) = '' "
        "         THEN 1 ELSE 0 END) AS rows_unattributed, "
        "SUM(CASE WHEN matter_ref = ? THEN 1 ELSE 0 END) AS rows_matching_matter, "
        "MIN(CASE WHEN matter_ref IS NULL OR TRIM(matter_ref) = '' "
        "         THEN ts END) AS unattributed_first_ts, "
        "MAX(CASE WHEN matter_ref IS NULL OR TRIM(matter_ref) = '' "
        "         THEN ts END) AS unattributed_last_ts "
        "FROM audit_log "
        "WHERE ts >= ? AND ts <= ? AND action_type <> ?"
    )
    params = [
        matter,
        period_start,
        period_end,
        _COVERAGE_EXCLUDED_ACTION_TYPE,
    ]
    rows = await _fetch_optional(reader, sql, params)

    if rows is None:
        return AuditCoverage(
            matter=matter,
            table_present=False,
            rows_in_period=0,
            rows_matching_matter=0,
            rows_unattributed=0,
            gap_acknowledged=gap_acknowledged,
            acknowledged_by=actor if gap_acknowledged else None,
        )

    row = rows[0] if rows else {}
    total = int(row.get("rows_in_period") or 0)
    unattributed = int(row.get("rows_unattributed") or 0)
    matching = total if matter == "all" else int(row.get("rows_matching_matter") or 0)

    return AuditCoverage(
        matter=matter,
        table_present=True,
        rows_in_period=total,
        rows_matching_matter=matching,
        rows_unattributed=unattributed,
        unattributed_first_ts=row.get("unattributed_first_ts") or None,
        unattributed_last_ts=row.get("unattributed_last_ts") or None,
        gap_acknowledged=gap_acknowledged,
        acknowledged_by=actor if gap_acknowledged else None,
    )


def _coverage_refusal_message(coverage: AuditCoverage) -> str:
    """The error an operator sees instead of a silently empty packet."""
    if not coverage.table_present:
        cause = "the export source read for this packet has no audit_log table, so no activity can be reported at all."
    else:
        cause = (
            f"{_rows_phrase(coverage.rows_unattributed)} in this period "
            f"{_rows_verb(coverage.rows_unattributed)} no matter attribution "
            f"({coverage._span()}). Any of them may concern this matter."
        )
    return (
        f"matter-scoped export for matter {coverage.matter!r} matched 0 audit "
        f"rows, but {cause} An empty audit section here would read as "
        '"nothing happened on this matter" when the truth is "this system '
        'cannot attribute those rows either way". Refusing to write a packet '
        "that makes that claim. Choose one: (a) re-run with --matter all for "
        "the customer-wide export; (b) narrow --from/--to to a period after "
        "matter attribution began; or (c) re-run with "
        "--acknowledge-unattributed-gap to emit the packet with the gap stated "
        "on its face, which is recorded in manifest.json and in the "
        "COMPLIANCE_PACKET_EXPORTED audit row."
    )


async def _fetch_boot_checks(reader: ReadExecutor, *, period_start: str, period_end: str) -> List[dict]:
    sql = (
        "SELECT id, ts, invariant_num, passed, failure_detail "
        "FROM invariant_boot_checks "
        "WHERE ts >= ? AND ts <= ? "
        "ORDER BY ts ASC, id ASC"
    )
    return await _fetch_safe(reader, sql, [period_start, period_end])


async def _fetch_memory_snapshot(reader: ReadExecutor) -> dict:
    rules = await _fetch_safe(
        reader,
        "SELECT id, rule_type, category, content, source, source_ref, "
        "created_at, updated_at, deleted_at, version FROM memory_rules "
        "ORDER BY created_at ASC, id ASC",
    )
    persons = await _fetch_safe(
        reader,
        "SELECT id, canonical_name, role, email_addresses, firm_internal, "
        "notes, created_at, updated_at, deleted_at FROM person_mappings "
        "ORDER BY created_at ASC, id ASC",
    )
    # Redact external_ids entirely from the snapshot, per spec.
    for row in persons:
        row.pop("external_ids", None)

    voice_meta = await _fetch_safe(
        reader,
        "SELECT id, uploaded_at, uploaded_by, source, recipient_cohort_id, "
        "sanitized, active, used_in_blind_test FROM voice_samples "
        "ORDER BY uploaded_at ASC, id ASC",
    )

    cohorts = await _fetch_safe(
        reader,
        "SELECT id, name, description, tone_descriptors, match_rules, "
        "created_at, updated_at FROM recipient_cohorts "
        "ORDER BY created_at ASC, id ASC",
    )

    # ADR-0016 live memory (#1355): the Machine-local persona_observations
    # mirror, pulled into the snapshot DB by the seam preserver
    # (bin/lib/seam_pull.py) before decommission. SELECT * because the table
    # schema is owned by the overlay's memory-mirror plugin; absent table →
    # honest empty via _fetch_safe.
    observations = await _fetch_safe(reader, "SELECT * FROM persona_observations ORDER BY rowid ASC")

    return {
        "memory_rules": rules,
        "person_mappings": persons,
        "persona_observations": observations,
        "voice_samples_metadata": voice_meta,
        "voice_sample_bodies_included": False,
        "voice_sample_body_export_note": (
            "Voice sample bodies are not included in the default packet. "
            "Use operator/bin/export-voice-samples.sh (separate signed "
            "export path) per spec when full bodies are required."
        ),
        "recipient_cohorts": cohorts,
    }


async def _fetch_skill_catalog(reader: ReadExecutor, *, period_start: str, period_end: str) -> List[dict]:
    sql = (
        "SELECT skill_name, trust_ceiling, content_hash, activated_at, "
        "last_run_at, run_count, operator_may_approve, config "
        "FROM skill_state "
        "WHERE activated_at <= ? "
        "ORDER BY activated_at ASC, skill_name ASC"
    )
    rows = await _fetch_safe(reader, sql, [period_end])
    return rows


async def _fetch_safe(reader: ReadExecutor, sql: str, params: Optional[Sequence[Any]] = None) -> List[dict]:
    """Run a SELECT; return [] on table-missing errors.

    Production D1 will have every migration applied; tests may construct
    only the tables they exercise. Treating "table absent" as "no data"
    matches the no-fabrication contract.
    """
    rows = await _fetch_optional(reader, sql, params)
    return [] if rows is None else rows


async def _fetch_optional(
    reader: ReadExecutor, sql: str, params: Optional[Sequence[Any]] = None
) -> Optional[List[dict]]:
    """Run a SELECT; return ``None`` when the table does not exist.

    :func:`_fetch_safe` flattens "table absent" into "no data", which is
    right for the dump files. The coverage tally needs the distinction:
    a missing table is not a zero, and a packet must not present it as
    one.
    """
    try:
        return await reader.fetch_all(sql, list(params or []))
    except Exception as exc:
        msg = str(exc).lower()
        if "no such table" in msg or "does not exist" in msg:
            log.warning("evidence read skipped (table absent): %s", exc)
            return None
        raise
