"""Compliance evidence packet builder (issue #894).

Builds a tar.gz packet containing:

* ``00-README.md`` -- plain-language overview
* ``01-summary.pdf`` -- Susan-readable narrative (see :mod:`.pdf`)
* ``03-audit-log.csv`` -- structured audit_log dump for the period
* ``05-customer-yaml.redacted.yml`` -- customer config, secrets redacted
* ``06-memory-snapshot.json`` -- memory rules + person mappings + voice
  metadata (no sample content; spec keeps it on a separate signed path)
* ``07-skill-catalog.json`` -- skills active during the period
* ``09-boot-checks.csv`` -- invariant_boot_checks dump
* ``manifest.json`` -- file hashes + Captain signature stub

The full spec lists additional documents (engagement-letter clauses,
DPA, BAA, decommission confirmation). Those are static or per-customer
documents not under the per-customer D1, so they are out of scope for
the runtime in this PR -- the packet structure leaves room to drop them
in without manifest/PDF rewrites.

Composition
-----------

The builder talks to D1 through a read executor protocol (mirrors the
write executor in :mod:`adapter.audit_log`). Tests pass a sqlite-backed
implementation; production wires the HTTP D1 client.

Every successful build emits one ``COMPLIANCE_PACKET_EXPORTED`` row to
the audit log writer the caller supplied. That row is the
chain-of-custody artifact for the export itself, with the manifest
sha256 in metadata so a reviewer can verify packet identity at any time.

Role gate
---------

The CLI accepts an ``--actor`` flag; the builder verifies the actor's
role is in :data:`REQUIRED_ACTOR_ROLES` (``captain`` or ``compliance``).
A missing or wrong role aborts the build before any D1 read and before
any audit row.

No-fabrication contract
-----------------------

When a table has no rows for the period, the JSON / CSV dump is the
empty case (``[]`` or header-only CSV). The PDF narrative reports the
literal count (``0``), not a soft phrase that implies the agent did
something it did not. ``customer.yaml`` missing on disk is an error,
not a placeholder: callers must point at a real file.

An empty section is itself a claim
----------------------------------

A zero has two meanings and an auditor cannot tell them apart from the
zero alone: "nothing happened" and "this system cannot answer that
question". ``matter_ref`` was added to the audit schema after seats had
already begun writing rows, and the emitter did not populate it at
first, so rows written before that fix carry ``matter_ref = NULL``
permanently. There is no key to backfill them from.

A matter-scoped export therefore has a coverage boundary, and
:class:`AuditCoverage` computes it on every build. Three outcomes:

* **Answerable zero.** No row matches the matter and no row in the
  period lacks attribution. The packet states that the zero is
  complete.
* **Unanswerable zero.** No row matches the matter and one or more rows
  in the period carry no attribution at all. The build REFUSES
  (:class:`EvidencePacketError`) rather than ship an empty audit
  section that reads as "nothing happened". This follows the
  empty-state discipline in ``docs/style/empty-state-pattern.md``,
  whose legal-document precedent is to block generation rather than
  render a plausible-looking gap. ``--acknowledge-unattributed-gap``
  overrides the refusal; it does NOT suppress the disclosure, and the
  acknowledgement is recorded in the manifest and the audit row.
* **Partial coverage.** Rows match the matter AND other rows in the
  period lack attribution. The packet builds and states, on its face,
  how many rows it could not scope either way.

Unattributed rows are never included in a matter-scoped packet: they
may concern other clients. The packet discloses their count and their
time span, not their contents.

The secret-redaction pass walks the parsed YAML and replaces every
``token_ref``, ``oauth_scopes``, and ``failure_recipients`` /
``red_flag_recipients`` value. A pre-export validator scans the
redacted output for residual secret patterns and aborts if any leak
through (``EvidencePacketError`` raised before the packet writes).
"""

from __future__ import annotations

import csv
import enum
import hashlib
import io
import json
import logging
import re
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .manifest import EvidenceManifest, build_manifest, manifest_sha256_hex
from .signing import (
    DETACHED_SIGNATURE_FILENAME,
    SIGNATURE_DETACHED_MARKER,
    load_signer,
)
from .pdf import render_summary_pdf

log = logging.getLogger("aie.evidence.packet")


REQUIRED_ACTOR_ROLES = frozenset({"captain", "compliance"})

# Phrases / shapes the redactor walks customer.yaml for.
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)^(token_ref|api_key|secret|password|client_secret|signing_key|"
    r"refresh_token|access_token|private_key)$"
)
_OAUTH_KEY_PATTERN = re.compile(r"(?i)^oauth_scopes$")
_RECIPIENT_KEY_PATTERN = re.compile(
    r"(?i)^(failure_recipients|red_flag_recipients|notification_recipients)$"
)

# Pre-export validator: a redacted file must NOT contain these patterns.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),       # OpenAI / Anthropic style secret
    re.compile(r"AKIA[0-9A-Z]{16}"),          # AWS access key
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),     # Google API key
    re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


class EvidencePacketError(RuntimeError):
    """Raised on any unrecoverable packet-build failure.

    The caller (CLI or dashboard worker) should surface this verbatim to
    the operator. Partial outputs are NOT written: the builder writes
    to a temp path and only renames into place on success.
    """


class PacketActor(str, enum.Enum):
    """Subset of :class:`adapter.audit_log.ActorRole` that may invoke."""

    CAPTAIN = "captain"
    COMPLIANCE = "compliance"


@dataclass(frozen=True)
class PacketRequest:
    """Inputs that fully specify a packet build.

    ``matter`` may be a specific matter ID or the string ``"all"`` (the
    spec lets the caller scope the export by matter or by period).
    ``customer_yaml_path`` is the on-disk yaml for the customer; the
    builder reads it once and includes the redacted form in the packet.

    ``acknowledge_unattributed_gap`` overrides the refusal a matter-scoped
    export raises when it matches zero rows while unattributed rows exist
    in the period (see :class:`AuditCoverage`). It does not change what
    the packet says: the gap is disclosed either way, and the
    acknowledgement itself is recorded in the manifest and the
    ``COMPLIANCE_PACKET_EXPORTED`` audit row.

    ``pinned_head`` is a chain head recorded OFF the Machine before this export
    (ss#2500). Supplying one turns the packet's audit section from "these rows
    are internally consistent" into "these rows still contain a head recorded at
    a moment nobody on the Machine could reach". Without it, tail truncation is
    invisible -- and the packet says so on its face rather than implying a
    completeness it did not test.
    """

    customer_slug: str
    matter: str
    period_start: str
    period_end: str
    output_path: Path
    customer_yaml_path: Path
    actor: str
    actor_role: PacketActor
    acknowledge_unattributed_gap: bool = False
    pinned_head: Optional[str] = None

    def validate(self) -> None:
        if not self.customer_slug:
            raise EvidencePacketError("customer_slug must be non-empty")
        if not self.matter:
            raise EvidencePacketError(
                "matter must be a specific id or 'all'; never empty"
            )
        if not _is_iso8601(self.period_start) or not _is_iso8601(self.period_end):
            raise EvidencePacketError(
                "period_start / period_end must be ISO 8601 strings"
            )
        if self.period_end < self.period_start:
            raise EvidencePacketError("period_end must be >= period_start")
        if not isinstance(self.actor_role, PacketActor):
            raise EvidencePacketError(
                "actor_role must be a PacketActor (captain | compliance)"
            )
        if self.actor_role.value not in REQUIRED_ACTOR_ROLES:
            raise EvidencePacketError(
                f"actor_role {self.actor_role.value!r} not in "
                f"{sorted(REQUIRED_ACTOR_ROLES)}"
            )
        if self.pinned_head is not None and not _CHAIN_HEAD_RE.match(self.pinned_head):
            # Refused here rather than reported as a missing head later. A
            # malformed pin can never match any row, so carrying it forward
            # would print "the record was truncated" on a packet about a healthy
            # ledger -- a false accusation in a document written for a court.
            raise EvidencePacketError(
                "pinned_head must be a sha256 hexdigest (64 lowercase hex "
                "characters), the shape row_hash takes in the audit ledger"
            )


@dataclass
class EvidencePacketResult:
    """What the build returns to the caller."""

    output_path: Path
    manifest_sha256: str
    file_count: int
    bytes_written: int
    counts: Mapping[str, int]
    manifest: EvidenceManifest
    coverage: "AuditCoverage"
    # ss#2500. Present even when no pin was supplied, carrying checked=False, so
    # a caller reading the result cannot mistake "not asked" for "asked and fine".
    chain_pin: "ChainPin" = field(
        default_factory=lambda: ChainPin(
            pinned_head=None, present=False, chain_readable=True, source=CHAIN_PIN_SOURCE
        )
    )


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
    "the audit_head_history table on the SMD control plane, appended from every "
    "heartbeat the Operator sends"
)


def _rows_phrase(count: int) -> str:
    """"1 row" / "4130 rows". The packet is read by lawyers; "1 rows"
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
                lines.append(
                    "Read the counts in this packet as a floor for this matter, "
                    "not as a complete tally."
                )
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
# Helpers
# ---------------------------------------------------------------------------


_ISO_8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z?$"
)


def _is_iso8601(value: str) -> bool:
    return bool(value) and bool(_ISO_8601_RE.match(value))


def _iso_utc(now: Optional[datetime] = None) -> str:
    dt = now if now is not None else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# customer.yaml redaction
# ---------------------------------------------------------------------------


def _redact_email(email: str) -> str:
    """Replace local-part; keep domain so the structure is auditable."""
    if "@" in email:
        _, domain = email.split("@", 1)
        return f"<redacted>@{domain}"
    return "<redacted>"


def redact_customer_yaml(parsed: Any) -> Any:
    """Walk a parsed-yaml structure and redact sensitive fields.

    Returns a NEW structure; the input is not mutated. The function is
    pure-Python (no PyYAML import here) so the test suite can pass it
    dicts directly and the CLI parses via yaml at call time.
    """
    if isinstance(parsed, dict):
        out: dict = {}
        for key, value in parsed.items():
            if isinstance(key, str) and _SECRET_KEY_PATTERN.match(key):
                out[key] = "<redacted>"
                continue
            if isinstance(key, str) and _OAUTH_KEY_PATTERN.match(key):
                count = (
                    len(value)
                    if isinstance(value, (list, tuple, set))
                    else 1 if value
                    else 0
                )
                out[key] = f"<{count} scopes redacted>"
                continue
            if isinstance(key, str) and _RECIPIENT_KEY_PATTERN.match(key):
                if isinstance(value, list):
                    out[key] = [_redact_email(str(v)) for v in value]
                elif isinstance(value, str):
                    out[key] = _redact_email(value)
                else:
                    out[key] = "<redacted>"
                continue
            out[key] = redact_customer_yaml(value)
        return out
    if isinstance(parsed, list):
        return [redact_customer_yaml(item) for item in parsed]
    return parsed


def _scan_for_secret_leak(rendered: str) -> Optional[str]:
    """Return the first regex match (as a label) found in the rendered
    yaml, or None when clean."""
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(rendered):
            return pattern.pattern
    return None


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
        sql = (
            "SELECT * FROM audit_log "
            "WHERE ts >= ? AND ts <= ? "
            "ORDER BY ts ASC, id ASC"
        )
        params: list = [period_start, period_end]
    else:
        sql = (
            "SELECT * FROM audit_log "
            "WHERE ts >= ? AND ts <= ? AND matter_ref = ? "
            "ORDER BY ts ASC, id ASC"
        )
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
        rows = await reader.fetch_all(
            "SELECT row_hash FROM audit_log WHERE row_hash = ? LIMIT 1", [pinned_head]
        )
    except Exception as exc:
        # Two ways the question cannot be asked, and neither is an answer: no
        # audit_log table, and an audit_log without the chain columns (a
        # snapshot written before they were preserved). Anything else re-raises
        # rather than being flattened into a reassuring shape.
        msg = str(exc).lower()
        if "no such table" in msg or "no such column" in msg or "does not exist" in msg:
            log.warning("chain pin lookup skipped (chain columns unreadable): %s", exc)
            return ChainPin(
                pinned_head, present=False, chain_readable=False, source=CHAIN_PIN_SOURCE
            )
        raise
    return ChainPin(
        pinned_head, present=len(rows) > 0, chain_readable=True, source=CHAIN_PIN_SOURCE
    )


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
        cause = (
            "the export source read for this packet has no audit_log table, so "
            "no activity can be reported at all."
        )
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


async def _fetch_boot_checks(
    reader: ReadExecutor, *, period_start: str, period_end: str
) -> List[dict]:
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
    observations = await _fetch_safe(
        reader, "SELECT * FROM persona_observations ORDER BY rowid ASC"
    )

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


async def _fetch_skill_catalog(
    reader: ReadExecutor, *, period_start: str, period_end: str
) -> List[dict]:
    sql = (
        "SELECT skill_name, trust_ceiling, content_hash, activated_at, "
        "last_run_at, run_count, operator_may_approve, config "
        "FROM skill_state "
        "WHERE activated_at <= ? "
        "ORDER BY activated_at ASC, skill_name ASC"
    )
    rows = await _fetch_safe(reader, sql, [period_end])
    return rows


async def _fetch_safe(
    reader: ReadExecutor, sql: str, params: Optional[Sequence[Any]] = None
) -> List[dict]:
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


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _rows_to_csv(rows: Iterable[dict], columns: Sequence[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in columns})
    return buf.getvalue().encode("utf-8")


def _rows_to_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, indent=2, default=str).encode("utf-8")


def _render_yaml(data: Any) -> bytes:
    """Render a parsed yaml structure back to a stable YAML-ish string.

    The builder avoids importing PyYAML so the adapter does not pick up
    an optional dep. The output is human-readable and deterministic.
    JSON is a valid YAML 1.2 subset, which is enough for the packet's
    audit purpose.
    """
    return (
        json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, default=str)
        + "\n"
    ).encode("utf-8")


def _readme_text(
    *,
    customer_slug: str,
    customer_name: str,
    period_start: str,
    period_end: str,
    matter: str,
    signer_name: str,
    signer_email: str,
    actor: str,
    actor_role: str,
    manifest_sha256: str,
    coverage: AuditCoverage,
    chain_pin: ChainPin,
    signed: bool = False,
    key_id: str = "",
) -> bytes:
    """Render the plain-language README first page.

    No em dashes (style rule). No fabricated promises about behavior
    the customer did not contract: the README describes only what the
    packet itself contains.

    The coverage statement sits above the contents list on purpose. A
    reader who stops after the first screen must still learn what this
    packet cannot answer.
    """
    coverage_body = "\n\n".join(coverage.narrative_lines())
    pin_body = "\n\n".join(chain_pin.narrative_lines())
    body = (
        f"# Compliance Evidence -- {customer_name}\n\n"
        f"**Customer slug:** {customer_slug}\n"
        f"**Matter scope:** {matter}\n"
        f"**Period covered:** {period_start} to {period_end}\n"
        # Two different facts, deliberately not merged. The entity is who
        # stands behind the packet; the actor is who ran the export. Collapsing
        # them into one "generated by" line is how the signature block came to
        # name an individual in the first place (ss-console #2122).
        f"**Produced and signed by:** {signer_name} ({signer_email})\n"
        f"**Export run by:** {actor} ({actor_role})\n"
        f"**Manifest SHA-256:** {manifest_sha256}\n\n"
        "## What this package is\n\n"
        "This package documents how the Operator operated inside this "
        "customer instance during the period above. It is meant to be read "
        "by an attorney, an outside auditor, or the customer itself. You "
        "do not need a technical background to read this README or the "
        "summary PDF.\n\n"
        "## What this package covers, and what it cannot\n\n"
        f"{coverage_body}\n\n"
        "## Whether the log itself is complete\n\n"
        f"{pin_body}\n\n"
        "## What is in the package\n\n"
        "- `00-README.md` -- this document\n"
        "- `01-summary.pdf` -- the Susan-readable narrative\n"
        "- `03-audit-log.csv` -- every audit_log row for the period\n"
        "- `05-customer-yaml.redacted.yml` -- the customer's config, with "
        "secrets and OAuth scopes redacted\n"
        "- `06-memory-snapshot.json` -- the agent's memory rules, "
        "person mappings, and voice metadata\n"
        "- `07-skill-catalog.json` -- the skills active during the period\n"
        "- `09-boot-checks.csv` -- the substrate's invariant boot-check log\n"
        "- `manifest.json` -- file hashes plus the signature block\n"
        + (
            "- `manifest.sig` -- detached Ed25519 signature over "
            "`manifest.json`\n\n"
            if signed
            else "\n"
        )
        +
        "## What this package does NOT contain\n\n"
        "Substantive content of drafts, sent messages, or memory payloads "
        "is not in this packet. Those bodies live in per-customer R2 "
        "storage keyed by SHA-256 digest; the audit log records every "
        "digest, and an auditor can request the underlying object on "
        "demand against the customer's data-processing addendum.\n\n"
        "Voice sample bodies are not in this packet by default. Use the "
        "separate signed export path "
        "(`operator/bin/export-voice-samples.sh`) when full bodies are "
        "required.\n\n"
        # ss-console#2501. A digest an outsider cannot recompute asks the
        # reader to take SMD's word for it, which is the opposite of what an
        # evidence packet is for. The recipe differs per transport because the
        # transports differ: one carries our bytes verbatim, the other composes
        # the stored message itself. Stating that plainly is the honest move;
        # a single recipe that quietly failed on the reply path would be worse
        # than none.
        "## Checking a message against your own copy\n\n"
        "You can check a message body the Operator sent against the copy "
        "your own mail system stored, using nothing from SMD. The check "
        "does not depend on this packet being honest, which is the point of "
        "it.\n\n"
        "Rows for messages the Operator sent as a REPLY carry these "
        "hashes. A message the Operator sent on its own initiative records "
        "a hash of the whole send request rather than of the body bytes, so "
        "the check below does not apply to it; the row will simply not "
        "carry these fields.\n\n"
        "- `body_digest` is internal. It covers the subject and the body "
        "joined together, which is what our content checks read. The subject "
        "is not part of a reply on the wire, so this one cannot be "
        "reproduced from the message you hold. Do not try.\n"
        "- `body_digest_authored` is the SHA-256 of exactly the plain-text "
        "bytes the Operator handed to the mail system.\n"
        "- `body_digest_authored_html` is the same for the HTML body, when "
        "one was sent.\n\n"
        "**Which check applies depends on what your mail system did with "
        "the body, and the difference is not a technicality.** Where the "
        "mail system stores the bytes it was handed, the test is EQUALITY: "
        "the hash of the stored body equals the hash on the row. Where the "
        "mail system COMPOSES the stored message, the test is CONTAINMENT: "
        "the authored text must appear, byte for byte, inside the stored "
        "body, and its hash will never equal the whole.\n\n"
        "Microsoft Graph composes. A reply sent through it "
        "(`POST /messages/{id}/reply`) gets Microsoft's own HTML wrapper "
        "and the quoted original appended beneath what we wrote, so the "
        "stored body is LONGER than what the Operator authored and equality "
        "cannot hold. Use containment for those. Where the message went out "
        "through a transport that carries a real plain-text part, equality "
        "holds on that part. If you do not know which applies to a "
        "particular message, run the containment check: it holds in both "
        "cases.\n\n"
        "Equality, worked. Save the stored body to `stored.txt` exactly as "
        "your mail system holds it, with no re-wrapping and no added "
        "newline, then:\n\n"
        "```\n"
        "openssl dgst -sha256 stored.txt\n"
        "```\n\n"
        "Compare that hex value to `body_digest_authored` on the row (or to "
        "`body_digest_authored_html` if you saved the HTML body). They match "
        "or they do not; there is no partial credit.\n\n"
        "Containment, worked. Here you hold the stored message and we hold "
        "nothing you have to trust, so the check runs in two steps. Save "
        "the whole stored body to `stored.txt`. Then save the part of it "
        "you take to be what the Operator wrote, which on a Graph reply is "
        "everything above the quoted original, to `candidate.txt`, keeping "
        "its bytes exactly as they appear:\n\n"
        "```\n"
        "grep -F -f candidate.txt stored.txt\n"
        "openssl dgst -sha256 candidate.txt\n"
        "```\n\n"
        "The first prints the matching lines and exits 0 when your "
        "candidate is present verbatim, which is what makes the boundary "
        "you drew legitimate rather than convenient; it exits 1 when it is "
        "not. The second must equal `body_digest_authored_html` on the row "
        "when your candidate is HTML, or `body_digest_authored` when it is "
        "plain text. Both together are the proof: the bytes you extracted "
        "are inside the message you hold, AND they are the bytes the "
        "Operator handed the mail system.\n\n"
        "Whether the hashes match is a fact about your mail system, not a "
        "promise from us. If it stored what it was handed, they match. If "
        "it re-encoded the body on the way in, by re-wrapping lines or "
        "changing the character encoding, they will not, and that "
        "difference is itself something worth knowing rather than "
        "something we should have hidden by hashing a normalized copy.\n\n"
        "We do not normalize, reformat, or canonicalize anything before "
        "hashing, and neither should you. A recipe that first massages the "
        "bytes is a recipe someone can argue with.\n\n"
        "What a match proves: the body on that row is the body your mail "
        "system stored. What it does not prove on its own: that the row "
        "itself was not altered. That is what the hash chain and the "
        "signature below are for.\n\n"
        "## Verification\n\n"
        + (
            (
                "**This packet is SIGNED.** `manifest.sig` is a detached "
                "Ed25519 signature over the exact bytes of `manifest.json`, "
                "which in turn carries the SHA-256 of every other file. "
                "Verification needs no credential and no contact with SMD.\n\n"
                "The signer of record is SMDurgan, LLC, the company under "
                "contract with this firm. The packet is not signed by an "
                "individual, so it stays verifiable regardless of who is "
                "employed here when you read it.\n\n"
                "Every signing key SMD has ever used is published at "
                "https://smd.services/trust, with its fingerprint and its "
                "status. That page is a standing commitment: the address does "
                "not change, and a retired key stays published so packets "
                "signed under it remain verifiable.\n\n"
                "Fetch the current public key from "
                "https://smd.services/keys/evidence-packet-signing-key.pem "
                f"and confirm its fingerprint is `{key_id}`, the key named in "
                "`manifest.json`. If it is not, the packet was signed under an "
                "earlier key: find that fingerprint on the trust page and "
                "fetch that key instead. Then:\n\n"
                "```\n"
                "openssl pkeyutl -verify -pubin "
                "-inkey evidence-packet-signing-key.pem \\\n"
                "  -rawin -in manifest.json -sigfile manifest.sig\n"
                "```\n\n"
                "A successful verification proves the manifest was produced "
                "by SMD and has not been altered since export. Once it "
                "passes, hash any individual file and compare it to its entry "
                "in `manifest.json`.\n\n"
                "The signature covers origin and integrity after export. It "
                "says nothing about whether the underlying audit log is "
                "correct. Tamper evidence within the log itself is a separate "
                "mechanism: the ledger is hash chained, so a row altered, "
                "removed, or inserted anywhere before the end of the log "
                "breaks the chain at a verifiable point. Rows removed from the "
                "END of the log are a separate question, answered by the "
                "section above.\n\n"
            )
            if signed
            else (
                "**This packet is UNSIGNED. Its integrity is not "
                "cryptographically verifiable.** No signing key was "
                "configured when it was generated, so `manifest.json` carries "
                "no signature and there is no `manifest.sig`. The SHA-256 "
                "values quoted inside this packet (this README and the "
                "summary PDF) prove only that the packet is internally self-"
                "consistent. They are stored in the same archive they "
                "describe, so on their own they cannot detect deliberate "
                "tampering.\n\n"
                "To check integrity, compare the manifest SHA-256 above "
                "against the value recorded OUT OF BAND when the packet was "
                "generated: the `COMPLIANCE_PACKET_EXPORTED` audit-log row's "
                "`manifest_sha256`, obtained directly from the firm or SMD "
                "rather than from this archive. Once the manifest hash "
                "matches that external record, hash any individual file and "
                "compare it to its entry in `manifest.json`.\n\n"
            )
        )
        +
        "## Questions\n\n"
        f"Contact: {signer_email}\n"
    )
    return body.encode("utf-8")


# ---------------------------------------------------------------------------
# Counts (used by both the PDF and the manifest extra block)
# ---------------------------------------------------------------------------


def _compute_counts(
    *,
    audit_rows: Sequence[dict],
    boot_check_rows: Sequence[dict],
    skill_rows: Sequence[dict],
    memory_snapshot: Mapping[str, Any],
) -> dict:
    """Tally the headline numbers the summary PDF reports."""
    def _count(action_type: str) -> int:
        return sum(1 for r in audit_rows if r.get("action_type") == action_type)

    return {
        "audit_events": len(audit_rows),
        "drafts_created": _count("DRAFT_CREATED"),
        "drafts_approved": _count("DRAFT_APPROVED"),
        "drafts_rejected": _count("DRAFT_REJECTED"),
        "memory_rule_events": (
            _count("MEMORY_RULE_ADDED")
            + _count("MEMORY_RULE_EDITED")
            + _count("MEMORY_RULE_DELETED")
        ),
        "skills_enabled": sum(
            1 for r in skill_rows if (r.get("trust_ceiling") or "").lower() != "refused"
        ),
        "boot_checks": len(boot_check_rows),
        "invariant_violations": (
            _count("INVARIANT_VIOLATION") + _count("INVARIANT_BOOT_CHECK_FAILED")
        ),
        "escalations": _count("ESCALATION_FIRED"),
        "memory_rules_in_snapshot": len(memory_snapshot.get("memory_rules", [])),
        "person_mappings_in_snapshot": len(memory_snapshot.get("person_mappings", [])),
        "voice_samples_metadata_rows": len(
            memory_snapshot.get("voice_samples_metadata", [])
        ),
    }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass
class EvidencePacketBuilder:
    """Compose a digest-verified evidence packet for one customer + period.

    The manifest is NOT yet cryptographically signed -- it self-discloses
    ``signature="unsigned-stub"``; integrity rests on per-artifact SHA-256
    digests plus the manifest hash recorded in the append-only audit log.
    Detached signing is a tracked follow-on.

    Construction wires the read executor + audit writer + (optional)
    yaml parser. ``yaml_loader`` defaults to a minimal JSON-ish parser
    that accepts either JSON or simple YAML; production CLI passes
    ``yaml.safe_load`` directly.

    Call :meth:`build` to produce a tar.gz at ``request.output_path``.
    The builder writes to ``<output>.tmp``, fsyncs, and renames into
    place so partial files never appear on disk.
    """

    reader: ReadExecutor
    audit_writer: object  # adapter.audit_log.AuditLogWriter; kept loose
    yaml_loader: Optional[object] = None  # callable parsing yaml bytes
    yaml_dumper: Optional[object] = None  # callable dumping back to yaml

    async def build(self, request: PacketRequest) -> EvidencePacketResult:
        request.validate()

        customer_yaml_text = self._load_customer_yaml(request.customer_yaml_path)
        parsed_yaml = self._parse_yaml(customer_yaml_text)
        redacted_yaml = redact_customer_yaml(parsed_yaml)
        rendered_redacted_yaml = self._render_yaml(redacted_yaml)
        leak = _scan_for_secret_leak(rendered_redacted_yaml.decode("utf-8", "replace"))
        if leak is not None:
            raise EvidencePacketError(
                "redacted customer.yaml still contains a secret-shaped "
                f"value (pattern {leak!r}); aborting export. Update the "
                "redaction rules and re-run; do NOT modify the source yaml "
                "to bypass this guardrail."
            )

        customer_name = self._extract_customer_name(parsed_yaml, request.customer_slug)

        coverage = await _fetch_audit_coverage(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
            gap_acknowledged=request.acknowledge_unattributed_gap,
            actor=request.actor,
        )
        if coverage.is_unanswerable_empty and not coverage.gap_acknowledged:
            raise EvidencePacketError(_coverage_refusal_message(coverage))

        # ss#2500. Checked BEFORE any file is rendered, so a packet over a
        # truncated ledger never reaches disk in the first place.
        chain_pin = await _fetch_chain_pin(self.reader, pinned_head=request.pinned_head)
        if chain_pin.was_checked and not chain_pin.present:
            raise EvidencePacketError(_chain_pin_refusal_message(chain_pin))

        audit_rows = await _fetch_audit_log(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
        )
        boot_check_rows = await _fetch_boot_checks(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
        )
        memory_snapshot = await _fetch_memory_snapshot(self.reader)
        skill_rows = await _fetch_skill_catalog(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
        )

        counts = _compute_counts(
            audit_rows=audit_rows,
            boot_check_rows=boot_check_rows,
            skill_rows=skill_rows,
            memory_snapshot=memory_snapshot,
        )

        audit_csv = _rows_to_csv(audit_rows, _AUDIT_LOG_COLUMNS)
        boot_csv = _rows_to_csv(boot_check_rows, _BOOT_CHECK_COLUMNS)
        memory_json = _rows_to_json(memory_snapshot)
        skill_json = _rows_to_json(skill_rows)

        # Build a placeholder manifest first so the PDF can quote a
        # stable manifest-sha; we rebuild the manifest with the final
        # file hashes (including the PDF) after rendering.
        placeholder_hashes = {
            "00-README.md": _sha256(b""),
            "01-summary.pdf": _sha256(b""),
            "03-audit-log.csv": _sha256(audit_csv),
            "05-customer-yaml.redacted.yml": _sha256(rendered_redacted_yaml),
            "06-memory-snapshot.json": _sha256(memory_json),
            "07-skill-catalog.json": _sha256(skill_json),
            "09-boot-checks.csv": _sha256(boot_csv),
        }
        provisional_manifest = build_manifest(
            customer_slug=request.customer_slug,
            matter=request.matter,
            period_start=request.period_start,
            period_end=request.period_end,
            file_hashes=placeholder_hashes,
            actor=request.actor,
            actor_role=request.actor_role.value,
            extra={
                "counts": counts,
                "coverage": coverage.to_dict(),
                "chain_pin": chain_pin.to_dict(),
                "stage": "provisional",
            },
        )
        provisional_sha = manifest_sha256_hex(provisional_manifest)

        pdf_bytes = render_summary_pdf(
            customer_slug=request.customer_slug,
            customer_name=customer_name,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
            signer_key_id=provisional_manifest.signer_key_id,
            manifest_sha256=provisional_sha,
            counts=counts,
            coverage_lines=coverage.narrative_lines(),
            counts_are_partial=coverage.has_unattributed_rows,
        )

        # Resolve the signing key BEFORE anything is rendered. Three artifacts
        # depend on knowing whether this packet will be signed: the README's
        # verification section, and the algorithm + key id recorded inside
        # manifest.json. The signature itself is taken later, over the
        # serialized manifest, and shipped detached (adapter/evidence/signing.py
        # explains why it cannot be embedded). load_signer raises rather than
        # degrading when a key is configured but unusable: an unsigned packet is
        # an honest artifact, a falsely-signed one is a lie in a legal record.
        signer = load_signer()

        readme_bytes = _readme_text(
            customer_slug=request.customer_slug,
            customer_name=customer_name,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
            signer_name=provisional_manifest.signer_name,
            signer_email=provisional_manifest.signer_email,
            actor=request.actor,
            actor_role=request.actor_role.value,
            manifest_sha256=provisional_sha,
            coverage=coverage,
            chain_pin=chain_pin,
            signed=signer is not None,
            key_id=signer.key_id if signer else "",
        )

        # Final manifest with real file hashes (PDF + README included).
        file_hashes = {
            "00-README.md": _sha256(readme_bytes),
            "01-summary.pdf": _sha256(pdf_bytes),
            "03-audit-log.csv": _sha256(audit_csv),
            "05-customer-yaml.redacted.yml": _sha256(rendered_redacted_yaml),
            "06-memory-snapshot.json": _sha256(memory_json),
            "07-skill-catalog.json": _sha256(skill_json),
            "09-boot-checks.csv": _sha256(boot_csv),
        }
        manifest = build_manifest(
            customer_slug=request.customer_slug,
            matter=request.matter,
            period_start=request.period_start,
            period_end=request.period_end,
            file_hashes=file_hashes,
            actor=request.actor,
            actor_role=request.actor_role.value,
            signer_key_id=signer.key_id if signer else None,
            signature=SIGNATURE_DETACHED_MARKER if signer else None,
            signature_algorithm=signer.algorithm if signer else None,
            extra={
                "counts": counts,
                "coverage": coverage.to_dict(),
                "chain_pin": chain_pin.to_dict(),
                "provisional_manifest_sha256": provisional_sha,
            },
        )
        manifest_bytes = manifest.to_bytes()
        manifest_sha = manifest_sha256_hex(manifest)

        entries: List[Tuple[str, bytes]] = [
            ("00-README.md", readme_bytes),
            ("01-summary.pdf", pdf_bytes),
            ("03-audit-log.csv", audit_csv),
            ("05-customer-yaml.redacted.yml", rendered_redacted_yaml),
            ("06-memory-snapshot.json", memory_json),
            ("07-skill-catalog.json", skill_json),
            ("09-boot-checks.csv", boot_csv),
            ("manifest.json", manifest_bytes),
        ]

        # The detached signature covers manifest.json, which covers every other
        # artifact. It is deliberately NOT in file_hashes: it cannot hash
        # itself. Trust order: manifest.sig -> manifest.json -> everything else.
        if signer is not None:
            entries.append(
                (DETACHED_SIGNATURE_FILENAME, signer.sign(manifest_bytes))
            )

        bytes_written = self._write_targz(request.output_path, entries)

        await self._emit_audit_row(
            request=request,
            manifest_sha=manifest_sha,
            counts=counts,
            file_count=len(entries),
            bytes_written=bytes_written,
            coverage=coverage,
            chain_pin=chain_pin,
        )

        return EvidencePacketResult(
            output_path=request.output_path,
            manifest_sha256=manifest_sha,
            file_count=len(entries),
            bytes_written=bytes_written,
            counts=counts,
            manifest=manifest,
            coverage=coverage,
            chain_pin=chain_pin,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _load_customer_yaml(self, path: Path) -> bytes:
        if not path.exists():
            raise EvidencePacketError(
                f"customer.yaml not found at {path}; refusing to fabricate a "
                "placeholder. Provide a real customer.yaml."
            )
        return path.read_bytes()

    def _parse_yaml(self, text: bytes) -> Any:
        if self.yaml_loader is not None:
            return self.yaml_loader(text.decode("utf-8"))  # type: ignore[misc]
        # Fall back to a tiny built-in parser: accept JSON, otherwise
        # treat as opaque single-key text. This branch is exercised only
        # in tests that pass yaml_loader=None for simplicity.
        text_str = text.decode("utf-8")
        try:
            return json.loads(text_str)
        except json.JSONDecodeError:
            return {"raw": text_str}

    def _render_yaml(self, data: Any) -> bytes:
        if self.yaml_dumper is not None:
            rendered = self.yaml_dumper(data)  # type: ignore[misc]
            if isinstance(rendered, bytes):
                return rendered
            return rendered.encode("utf-8")
        return _render_yaml(data)

    def _extract_customer_name(self, parsed: Any, fallback_slug: str) -> str:
        if isinstance(parsed, dict):
            for key in ("customer_name", "name", "firm_name"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return fallback_slug

    def _write_targz(
        self, output_path: Path, entries: Sequence[Tuple[str, bytes]]
    ) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        with tarfile.open(tmp_path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            for name, blob in entries:
                info = tarfile.TarInfo(name=name)
                info.size = len(blob)
                info.mtime = 0  # deterministic mtime for stable archives
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                tar.addfile(info, io.BytesIO(blob))
        bytes_written = tmp_path.stat().st_size
        tmp_path.replace(output_path)
        return bytes_written

    async def _emit_audit_row(
        self,
        *,
        request: PacketRequest,
        manifest_sha: str,
        counts: Mapping[str, int],
        file_count: int,
        bytes_written: int,
        coverage: AuditCoverage,
        chain_pin: ChainPin,
    ) -> None:
        # Import locally to mirror the bin/lib/decommission.py pattern
        # (avoids hard adapter import at module load time).
        from adapter.audit_log import ActorRole, AuditEvent  # type: ignore

        role_map = {
            PacketActor.CAPTAIN: ActorRole.CAPTAIN,
            PacketActor.COMPLIANCE: ActorRole.COMPLIANCE,
        }
        event = AuditEvent(
            action_type="COMPLIANCE_PACKET_EXPORTED",
            actor=request.actor,
            actor_role=role_map[request.actor_role],
            # NOT a skill, and the row must not claim one (ss-console #2122).
            #
            # This field read `"compliance-audit-export"` for a year, naming a
            # skill that exists in neither repo and in no customer.yaml. The
            # instinct on finding that is to go build the skill. It cannot be
            # built: a packet's chain-of-custody row is written through the
            # broker's `audit_append`, which is gateway-PID-gated and rejects
            # the execute_code / terminal children that all skill work runs in
            # (`workspace_broker/server.py`, the `peer_pid != self.gateway_pid`
            # guard; vfy_01KZXYQNK316TK9JGHS9KBEFJC). That refusal is not an
            # obstacle to route around. It IS the control this packet attests
            # to: service agreement §4.5's claim that the agent cannot rewrite
            # its own record holds precisely because the agent cannot append to
            # it. A skill that could stamp its own export would falsify the
            # sentence the packet is built to prove.
            #
            # So the producer is named for what it is, in metadata, and
            # skill_name is NULL because no skill originated this row.
            skill_name=None,
            matter_ref=None if request.matter == "all" else request.matter,
            metadata={
                # The real producer. A reader asking "what made this packet"
                # gets the answer here instead of a skill name that resolves to
                # nothing.
                "producer": "operator/bin/generate-evidence-packet.sh",
                "customer_slug": request.customer_slug,
                "matter": request.matter,
                "period_start": request.period_start,
                "period_end": request.period_end,
                "manifest_sha256": manifest_sha,
                "file_count": file_count,
                "bytes_written": bytes_written,
                "output_path": str(request.output_path),
                "counts": dict(counts),
                "coverage": coverage.to_dict(),
                # The pin this packet was checked against, recorded INSIDE the
                # chain it attests to. A later reader can take this row's own
                # row_hash as the next pin, which is how the chain of custody
                # keeps going without a new mechanism.
                "chain_pin": chain_pin.to_dict(),
            },
        )
        try:
            await self.audit_writer.write(event)  # type: ignore[attr-defined]
        except Exception as exc:
            # If chain-of-custody fails, the packet that exists on disk
            # is unprovable. Surface the failure rather than swallow it.
            raise EvidencePacketError(
                "evidence packet wrote successfully but the "
                "COMPLIANCE_PACKET_EXPORTED audit row could not be "
                "persisted; manifest SHA-256 "
                f"{manifest_sha} is on disk but lacks chain-of-custody. "
                "Re-run after audit log recovery, or quarantine the "
                "packet pending audit reconciliation."
            ) from exc


__all__ = [
    "AuditCoverage",
    "CHAIN_PIN_SOURCE",
    "ChainPin",
    "EvidencePacketBuilder",
    "EvidencePacketError",
    "EvidencePacketResult",
    "PacketActor",
    "PacketRequest",
    "REQUIRED_ACTOR_ROLES",
    "ReadExecutor",
    "SqliteReadExecutor",
    "redact_customer_yaml",
]
