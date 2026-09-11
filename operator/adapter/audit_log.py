"""Audit log writer — synchronous persistence layer for issue #891.

Every safety-relevant action the agent takes (draft created, trust ceiling
promoted, connector bound, invariant violation, etc.) writes a row into the
per-customer D1 audit_log table before the action's effect lands. The write
is synchronous — there is no queue, no batch, no fire-and-forget. Losing an
audit event is treated as a safety-substrate failure on par with bypassing
trust-ceiling enforcement.

Design notes
------------

* The audit_log table lives in the per-customer D1 database
  (`hermes-{slug}-d1`), one binding per customer Machine per ADR 0008 +
  ADR 0009. There is no row-level customer column because cross-customer
  queries are forbidden. The DB binding *is* the customer scope.

* `ts` is ISO 8601 UTC with millisecond precision so the dashboard's
  recent-activity feed can order events that arrive in the same second.

* `id` is a ULID generated locally — sortable by time, monotonic per
  process, no external service required. The full implementation is in
  `_ulid()` below; it follows the spec at https://github.com/ulid/spec.

* Substantive content (the body of a draft email, the diff of a memory
  rule edit, etc.) never lands in this table. The writer takes a payload
  bytes object and computes its SHA-256 digest. The bytes themselves are
  the caller's responsibility to persist to R2 per r2-vectorize-naming.md.

* The executor interface is pluggable: production uses an `HttpD1Executor`
  that calls the Cloudflare D1 HTTP API; tests use a `SqliteExecutor`
  pointed at a tmp_path sqlite database. The interface is one method:
  `execute(sql: str, params: list) -> None`.

* The writer is async because the production HTTP executor uses httpx
  AsyncClient. Tests with the sqlite executor still run inside an asyncio
  event loop; sqlite calls block, which is acceptable in test scope.

Performance target
------------------

AC: <10ms p99 write. The sqlite executor consistently lands under 1ms per
write on dev hardware. The HTTP executor's latency is bounded by network
round-trip to D1 (typically 5-8ms within a Cloudflare datacenter). The
test in `tests/test_audit_log.py::test_write_under_10ms_p99` exercises
the sqlite path and asserts the budget.

Failure modes
-------------

* D1 unavailable: `AuditWriteError` raised. The caller must NOT swallow
  this — an unloggable action must not execute. The substrate invariant
  is "every action that touches state has an audit row." Skipping audit
  to make the agent more available violates the safety floor.

* Invalid action_type: `ValueError` raised before the SQL executes. The
  accepted set lives in `ACCEPTED_ACTION_TYPES` and matches d1-schema.md
  §1 exactly. Adding a new action type means updating both this constant
  and the spec — pull both forward in the same PR.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Protocol

log = logging.getLogger("aie.audit_log")


# ---------------------------------------------------------------------------
# Accepted action_type values
#
# Source of truth: docs/specs/operator/d1-schema.md §1 "Accepted action_type
# values". Any addition or removal here must match the spec and any
# fabrication-filter / compliance-evidence-packet consumers.
# ---------------------------------------------------------------------------

ACCEPTED_ACTION_TYPES = frozenset(
    {
        # Draft lifecycle
        "DRAFT_CREATED",
        "DRAFT_APPROVED",
        "DRAFT_REJECTED",
        "DRAFT_EXPIRED",
        # Memory rules
        "MEMORY_RULE_ADDED",
        "MEMORY_RULE_EDITED",
        "MEMORY_RULE_DELETED",
        # Trust ceiling
        "TRUST_PROMOTED",
        "TRUST_DEMOTED",
        # Skill activation
        "SKILL_ENABLED",
        "SKILL_DISABLED",
        # Routine scheduling (#2498). DELIBERATELY NOT the two above.
        # SKILL_ENABLED is a skill-catalog mutation — whether the Operator is
        # ALLOWED to do a thing. These are whether it is SCHEDULED to. A seat
        # can have every skill enabled and initiate nothing (ashton-price since
        # #2332), and a ledger that conflated the two would report that seat as
        # fully armed. Written by the overlay's audit plugin at registration
        # from the bootstrap cron-reconcile spool, on the DELTA only.
        "ROUTINE_ENABLED",
        "ROUTINE_DISABLED",
        # Agent lifecycle
        "AGENT_STOPPED",
        "AGENT_RESUMED",
        # Connector lifecycle
        "CONNECTOR_BOUND",
        "CONNECTOR_UNBOUND",
        "CONNECTOR_AUTH_EXPIRED",
        "CONNECTOR_AUTH_RESTORED",
        "CONNECTOR_TOKEN_REFRESHED",
        "CONNECTOR_HEALTH_PROBE_FAILED",
        # Scope changes
        "SCOPE_CHANGED",
        # Safety substrate
        "INVARIANT_VIOLATION",
        "INVARIANT_BOOT_CHECK_FAILED",
        # Inbound trust boundary (ADR 0027) — one row per untrusted inbound
        # item received, carrying the provenance envelope (source, trust_class,
        # verification, content_digest) in metadata; never the content bytes.
        "INBOUND_RECEIVED",
        # RBAC and compliance
        "RBAC_EVENT",
        "COMPLIANCE_PACKET_EXPORTED",
        # Voice gate
        "VOICE_GATE_PASSED",
        "VOICE_GATE_NEAR_PASS",
        "VOICE_GATE_FAILED",
        # Fabrication and escalation
        "FABRICATION_FILTER_TRIGGERED",
        "IDENTIFIER_UNVERIFIED",  # A1 report-only identifier gate (tier3, non-blocking)
        "ESCALATION_FIRED",
        "ESCALATION_ACKNOWLEDGED",
        # Decommission lifecycle. INITIATED / DRAIN_COMPLETE / FINAL mark the
        # pipeline boundaries; STEP_BEGIN / STEP_COMPLETE / STEP_FAILED mark
        # each numbered step. Before the 2026-06-12 code review every step
        # reused INITIATED + DRAIN_COMPLETE (and failures wrote INITIATED),
        # collapsing the compliance trail into indistinguishable rows.
        "DECOMMISSION_INITIATED",
        "DECOMMISSION_DRAIN_COMPLETE",
        "DECOMMISSION_STEP_BEGIN",
        "DECOMMISSION_STEP_COMPLETE",
        "DECOMMISSION_STEP_FAILED",
        "DECOMMISSION_FINAL",
        # Honcho overlay (ADR 0016 rewrite, 2026-05-24) — mirror, don't gate.
        # The hermes-smd-memory-mirror plugin emits this on Captain dismissal
        # in the admin portal; the paired physical DELETE against Honcho's
        # API is what works around bug #658 (temporal awareness). There is
        # no observation or promotion event — Honcho's writes are unmodified.
        "HONCHO_CONCLUSION_DISMISSED",
        # Skill Curator overlay (ADR 0017 rewrite, 2026-05-24) — trust native.
        # The hermes-smd-audit plugin emits AGENT_SKILL_CREATED on
        # post_tool_call for `skill_manage` create/write_file; AGENT_SKILL_REMOVED
        # on Captain remove-action in the admin portal. There is no promotion
        # event — `skill_manage` writes land directly in the customer's
        # per-profile skill catalog (Hermes-native).
        "AGENT_SKILL_CREATED",
        "AGENT_SKILL_REMOVED",
        # customer.yaml sync (ADR 0019) — emitted by the customer-sync
        # sidecar when the per-customer Machine's R2-source customer.yaml
        # changes. Non-structural changes apply with SIGHUP; structural
        # changes log _DEFERRED for Captain re-provision.
        "CUSTOMER_YAML_SYNCED",
        "CUSTOMER_YAML_STRUCTURAL_CHANGE_DEFERRED",
        # Delegated subagent observability (ADR 0021 Stream C). Skills that
        # delegate parallel research (demand-letter-draft and the rest
        # of the C.x set) emit:
        #   SUBAGENT_STOPPED      — one row per child subagent completion,
        #     emitted by the overlay's hermes-smd-audit plugin on the
        #     `subagent_stop` hook. Carries child_role, child_status,
        #     duration_ms in metadata.
        #   SUBAGENT_INCOMPLETE   — emitted by the PARENT skill before
        #     refusing to assemble the final draft, when any subagent's
        #     return fails the assembly-time schema contract (missing or
        #     empty required keys). The Devil's Advocate critique safety
        #     constraint: the approver never sees a quietly
        #     incomplete draft. Carries subagent_role, missing_key,
        #     matter_ref in metadata.
        "SUBAGENT_STOPPED",
        "SUBAGENT_INCOMPLETE",
        # No-agent cron suppression (ADR 0021 Stream B) — emitted by
        # `pre_run.py` BEFORE printing `{"wakeAgent": false}` to the gateway
        # scheduler. The mirror-don't-gate principle (ADR 0016) extended to
        # the cron-skip path: the decision-not-to-wake MUST be visible.
        # Audit-write failure forces the script to fall back to
        # `{"wakeAgent": true}` so the silent path is never structurally
        # indistinguishable from a silently-broken pre_run.py.
        #
        # Standard payload (via metadata):
        #   - pre_run_inputs_digest: sha-256 of the polling inputs that fed
        #     the decision (so an unexpected suppress can be traced)
        #   - decision_basis: short string code, e.g.
        #     "delta_under_threshold", "no_period_boundary", "config_missing"
        #   - next_scheduled_at: ISO 8601 UTC of the next tick
        "SUPPRESSED_WAKE",
        # The WAKE half of the same gate (ss-console #2253). Until this type
        # existed, a gated cron logged why it did NOT act and logged nothing at
        # all when it did — so the one tick that mattered was the one tick with
        # no row. On 2026-08-10 the deadline escalator woke with its connector
        # down and sent an alert stating a date it could not read; the ledger
        # held no record that the gate had fired, and the fabrication was found
        # only by reading the mailbox. Written best-effort by `pre_run.py` on
        # the real-decision wake path, BEFORE the wake line is printed.
        #
        # BEST-EFFORT IS THE POINT, and it is the opposite discipline from
        # SUPPRESSED_WAKE above: there, an audit failure must force a wake,
        # because a silent suppress is indistinguishable from a broken gate.
        # Here the wake is already happening, so an audit failure must never
        # suppress or delay it — the row is observability, never a gate.
        #
        # Standard payload (via metadata), deliberately parallel to
        # SUPPRESSED_WAKE so a reader can diff the two on the same fields:
        #   - pre_run_inputs_digest: sha-256 of the polling inputs that fed
        #     the decision
        #   - decision_basis: the same short code the wake line carries, so a
        #     woken turn's stated basis can be checked against the gate's
        #   - next_scheduled_at: ISO 8601 UTC of the next tick
        #   - plans_total / plans_emitted / plans_truncated: how many per-item
        #     plans the gate computed vs handed over, when plans exist
        "EMITTED_WAKE",
        # Reply channel (ADR 0055) — emitted by the overlay's hermes-smd-reply
        # plugin when the Operator (an employee) answers a colleague who emailed
        # its inbox. The reply is recipient-locked to the verified inbound sender
        # and authorized by the organization roster (scope.inbound_allow_from).
        #   REPLY_SENT   — the governed draft was sent back to the rostered sender
        #     (metadata: recipient, in_reply_to, inbox_id, sent_message_id,
        #     body_digest — never the body).
        #   REPLY_HELD   — the reply was held to draft, not sent (metadata: reason
        #     — sender_not_on_roster / recipient_mismatch / content_sensitive /
        #     rate_limited / no_inbox_id / empty_body — recipient, message_id).
        #   REPLY_FAILED — the send was attempted but errored (metadata: reason).
        # REPLY_HELD gained reason=matter_mismatch with ss#2167: the reply cited
        # a matter the recipient is provably not a party to (metadata also
        # carries matters, detail).
        "REPLY_SENT",
        "REPLY_HELD",
        "REPLY_FAILED",
        # MATTER_UNRESOLVED (ss#2167) — the matter gate ran on a reply and could
        # neither confirm nor deny membership, because the cited matter's party
        # list was never read this turn (metadata: recipient, message_id,
        # matters, detail). Recorded, NOT held: get_matter fires on 8 of 86
        # reply turns, so holding on unresolved would withhold correct client
        # replies at an unmeasured rate. These rows are that measurement, and
        # they are what a decision to start holding would rest on.
        "MATTER_UNRESOLVED",
        # ------------------------------------------------------------------
        # 2026-08-02 vocabulary reconciliation (#2122). Every type below has a
        # LIVE producer and live rows on both seats' ledgers, yet was absent
        # from this vocabulary — so ?action= filters silently no-opped and the
        # compliance roll-up could not name the bulk of the ledger (TOOL_CALL_
        # COMPLETED alone is ~69% of pilot rows). Producers named per type;
        # the TS mirror (src/lib/portal/operator/audit.ts) and the producers
        # manifest extend in lockstep — the parity test enforces it.
        #
        # Per-tool + per-turn audit (overlay hermes-smd-audit, ss#842/#981):
        "TOOL_CALL_COMPLETED",
        "LLM_TURN_COMPLETED",
        # Webhook routing + suppression (overlay hermes-smd-webhook-router /
        # webhook_gate.py, ADR 0021 Stream E):
        "WEBHOOK_ROUTED",
        "WEBHOOK_SUPPRESSED",
        # Mediated connector broker rows (overlay shared/broker_audit.py):
        "BROKER_DECISION_ALLOWED",
        "BROKER_EXECUTED",
        # Live config apply (overlay config_applier, ADR 0044 WS3):
        "CONFIG_WRITE",
        # Confirm-send seam (overlay hermes-smd-trust):
        "CONFIRM_SEND_DISPATCHED",
        "CONFIRM_SEND_FAILED",
        # Authored-format spec gate (overlay shared/spec_gate.py, overlay#207):
        "SPEC_GATE_TRIGGERED",
        # Report-only voice gate (overlay hermes-smd-trust/voice_gate.py) —
        # distinct from the VOICE_GATE_PASSED/NEAR_PASS/FAILED triple above,
        # which nothing currently emits:
        "VOICE_GATE_TRIGGERED",
        # Client-correction capture appended broker-side (ss#2091,
        # operator/workspace_broker/corrections.py):
        "CORRECTION_PROPOSED",
        # Conversational establishment, appended broker-side (ADR 0085;
        # operator/workspace_broker/establishment.py). The two ESTABLISHMENT
        # types have been written to client ledgers since ss#2161 and were never
        # declared here, so the portal rendered them as nothing and the silence
        # was indistinguishable from a deliberate suppression (ss#2316's third
        # state). RULE_PROPOSED is the ss#2529 propose-read-back-confirm path.
        "RULE_PROPOSED",
        "ESTABLISHMENT_SUBMITTED",
        "ESTABLISHMENT_RESULT",
        # ss#2546: the loop closing round the two silences #2529 left. A rule a
        # non-admin states is now EMAILED to the administrators the firm named
        # for request traffic (RULE_REQUEST_NOTIFIED, written by the seat's
        # establishment hook when that email is away); an administrator's "no"
        # is a decision with a row (RULE_DECLINED, broker-side); and a rule
        # nobody answered inside seven days lapses and the person who asked is
        # told (RULE_LAPSED, broker-side, written when the note is away).
        "RULE_REQUEST_NOTIFIED",
        "RULE_DECLINED",
        "RULE_LAPSED",
        # ss#2536: the same broker-side channel carrying a TOOL CALL. Proposed
        # when the Operator states one act back for an admin to confirm;
        # committed after that act succeeded, naming the confirmer.
        "ACT_PROPOSED",
        "ACT_COMMITTED",
        # ss#2614 routine 11: a chronology-package job's life on the seat, one
        # row per transition, written by the broker under its own uid on the
        # runner daemon's report (operator/workspace_broker/medchron_verbs.py).
        # Counts, cents, ids and the delivery folder; never the envelope.
        "MEDCHRON_JOB_SUBMITTED",
        "MEDCHRON_JOB_RUNNING",
        "MEDCHRON_JOB_HELD",
        "MEDCHRON_JOB_DELIVERED",
        "MEDCHRON_JOB_FAILED",
        # ss#2546 (the operations half). The three beats of a change only SMD
        # makes -- a routine, a schedule, a channel, a memory setting, an
        # autonomy level, an on/off. Recorded when somebody at the firm asks and
        # the request is given a tag; resolved when SMD answers done, declined,
        # or withdrawn; lapsed when nobody answered inside seven days and the
        # person who asked has been told so. All three are broker-side
        # (operator/workspace_broker/establishment.py).
        #
        # DELIBERATELY NOT the RULE_* types above. A rule is a standard the firm
        # may apply itself; an operations request is a change the firm cannot
        # make, and a ledger that filed them under one name would make "who
        # decided this" unanswerable from the rows.
        "OPS_REQUEST_RECORDED",
        "OPS_REQUEST_RESOLVED",
        "OPS_REQUEST_LAPSED",
    }
)


class ActorRole(str, enum.Enum):
    """Caller's role at the time of the audited action."""

    PRINCIPAL = "principal"
    OPERATOR = "operator"
    COMPLIANCE = "compliance"
    AGENT = "agent"
    CAPTAIN = "captain"


# ---------------------------------------------------------------------------
# ULID generation
#
# A ULID is a 26-char Crockford-base32 string: 10 chars timestamp (ms since
# epoch) + 16 chars randomness. Sortable. No dashes. The implementation here
# is intentionally minimal — no external dep — and is consistent with the
# `id TEXT PRIMARY KEY` shape declared in migration 0001.
# ---------------------------------------------------------------------------

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        out.append(_CROCKFORD[rem])
    return "".join(reversed(out))


def _ulid(now_ms: Optional[int] = None) -> str:
    """Return a 26-char ULID. now_ms is injectable for deterministic tests."""
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    rand = secrets.randbits(80)
    return _encode_crockford(ts, 10) + _encode_crockford(rand, 16)


def _iso_utc(now: Optional[datetime] = None) -> str:
    """ISO 8601 UTC with millisecond precision and explicit Z suffix."""
    dt = now if now is not None else datetime.now(timezone.utc)
    # strip to milliseconds; trailing Z marks UTC explicitly
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _sha256(payload: Optional[bytes]) -> Optional[str]:
    if payload is None:
        return None
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Executor interface
#
# The writer talks to D1 through a thin Protocol so tests can swap in a
# sqlite-backed executor without touching network code. Production uses
# `HttpD1Executor` (Cloudflare D1 HTTP API). Tests use `SqliteExecutor`
# from tests/conftest.py or equivalent.
# ---------------------------------------------------------------------------


class Executor(Protocol):
    async def execute(self, sql: str, params: list) -> None: ...


class AuditWriteError(RuntimeError):
    """Raised when the audit log cannot be written.

    The caller must NOT swallow this. Per safety substrate, an unloggable
    action must not execute. If the audit row cannot persist, the
    pending action must abort.
    """


@dataclass(frozen=True)
class AuditEvent:
    """Strongly-typed event payload accepted by `AuditLogWriter.write()`.

    Required:
        action_type — one of ACCEPTED_ACTION_TYPES
        actor       — 'agent' | 'captain' | person_mappings.id

    Optional:
        actor_role     — ActorRole enum (or string for forward-compat)
        skill_name     — name of the skill that originated the action
        matter_ref     — opaque per-vertical reference (matter id, lead id)
        input_payload  — bytes to digest; never stored
        output_payload — bytes to digest; never stored
        diff_payload   — bytes to digest; never stored
        trust_ceiling  — value of the skill's trust_ceiling at action time
        metadata       — JSON-serializable dict; merged then json.dumps()ed
    """

    action_type: str
    actor: str
    actor_role: Optional[ActorRole] = None
    skill_name: Optional[str] = None
    matter_ref: Optional[str] = None
    input_payload: Optional[bytes] = None
    output_payload: Optional[bytes] = None
    diff_payload: Optional[bytes] = None
    trust_ceiling: Optional[str] = None
    metadata: Optional[dict] = field(default=None)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


_INSERT_SQL = (
    "INSERT INTO audit_log "
    "(id, ts, action_type, actor, actor_role, skill_name, matter_ref, "
    "input_digest, output_digest, diff_digest, trust_ceiling, metadata) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class AuditLogWriter:
    """Synchronous (single-row-per-call) audit log writer.

    Construction takes an Executor. The writer holds no other state, so a
    single instance per Machine is sufficient and concurrency-safe (the
    executor is responsible for its own concurrency model).

    A production wiring looks like:

        from operator_adapter.audit_log import AuditLogWriter
        from operator_adapter.audit_log import HttpD1Executor

        executor = HttpD1Executor(
            account_id=os.environ["CF_ACCOUNT_ID"],
            database_id=os.environ["AIE_D1_DATABASE_ID"],
            api_token=os.environ["CF_API_TOKEN"],
        )
        writer = AuditLogWriter(executor)
    """

    def __init__(
        self,
        executor: Executor,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        ulid_now_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self._executor = executor
        self._clock = clock
        self._ulid_now_ms = ulid_now_ms

    async def write(self, event: AuditEvent) -> str:
        """Insert one audit_log row. Returns the inserted ULID.

        Synchronous in the sense that the awaited call returns only after
        the INSERT has been acknowledged by D1. No batching, no queueing.
        """
        if event.action_type not in ACCEPTED_ACTION_TYPES:
            raise ValueError(
                f"action_type {event.action_type!r} not in ACCEPTED_ACTION_TYPES; "
                "update both this constant and docs/specs/operator/d1-schema.md §1"
            )

        now_dt = self._clock() if self._clock else None
        now_ms = self._ulid_now_ms() if self._ulid_now_ms else None
        ulid = _ulid(now_ms=now_ms)
        ts = _iso_utc(now_dt)

        params = [
            ulid,
            ts,
            event.action_type,
            event.actor,
            event.actor_role.value if isinstance(event.actor_role, ActorRole) else event.actor_role,
            event.skill_name,
            event.matter_ref,
            _sha256(event.input_payload),
            _sha256(event.output_payload),
            _sha256(event.diff_payload),
            event.trust_ceiling,
            json.dumps(event.metadata, sort_keys=True, separators=(",", ":")) if event.metadata else None,
        ]

        try:
            await self._executor.execute(_INSERT_SQL, params)
        except Exception as e:
            log.error(
                "audit_log INSERT failed: action_type=%s actor=%s skill=%s err=%s",
                event.action_type,
                event.actor,
                event.skill_name,
                e,
            )
            raise AuditWriteError(
                f"audit_log INSERT failed for action_type={event.action_type}; "
                "caller MUST abort the pending action (no audit row, no action)"
            ) from e

        return ulid


# ---------------------------------------------------------------------------
# HTTP D1 executor (production)
#
# Calls the Cloudflare D1 HTTP API. Lazy-imported httpx so the module can be
# imported in test environments that don't pull httpx.
# ---------------------------------------------------------------------------


class HttpD1Executor:
    """Cloudflare D1 HTTP API executor.

    Uses the `query` endpoint per the D1 HTTP API spec:
    POST /accounts/{account_id}/d1/database/{database_id}/query

    Requires a CF API token with D1:Edit permission. Account ID and database
    ID come from the per-customer Hermes Machine's bound secrets, set by
    `bin/provision-customer.sh` at provision time.
    """

    def __init__(
        self,
        *,
        account_id: str,
        database_id: str,
        api_token: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout_seconds
        self._client: Optional[object] = None

    async def execute(self, sql: str, params: list) -> None:
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError(
                "HttpD1Executor requires httpx; install operator[adapter] extras"
            ) from e

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, headers=self._headers)

        body = {"sql": sql, "params": params}
        resp: Awaitable = self._client.post(self._url, json=body)  # type: ignore[assignment]
        result = await resp
        if result.status_code != 200:
            raise RuntimeError(
                f"D1 HTTP API returned {result.status_code}: {result.text[:200]}"
            )
        payload = result.json()
        if not payload.get("success"):
            raise RuntimeError(
                f"D1 query failed: {payload.get('errors') or payload}"
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()  # type: ignore[attr-defined]
            self._client = None


# ---------------------------------------------------------------------------
# Sqlite executor (tests + local dev)
#
# Backs the writer with an in-process sqlite3 connection. Used by
# tests/test_audit_log.py and any local-dev script that wants to exercise
# the writer without a real D1.
# ---------------------------------------------------------------------------


class SqliteExecutor:
    """Sqlite3-backed executor for tests and local dev.

    The caller supplies a `sqlite3.Connection` that already has the
    audit_log schema applied (either via 0001_per_customer_schema.sql or
    a hand-built CREATE TABLE in test setup).
    """

    def __init__(self, connection) -> None:
        self._conn = connection

    async def execute(self, sql: str, params: list) -> None:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self._conn.commit()


# ---------------------------------------------------------------------------
# SuppressedWakeWriter (ADR 0021 Stream B)
#
# Thin wrapper around AuditLogWriter for cron `pre_run.py` scripts that
# decide not to wake the agent. The contract:
#
#   1. The pre_run script MUST call `write_suppressed_wake(...)` BEFORE
#      printing `{"wakeAgent": false}` to stdout.
#   2. If the audit write succeeds, the script prints `wakeAgent: false`.
#   3. If the audit write raises `AuditWriteError`, the script falls back
#      to `{"wakeAgent": true}` and lets the agent wake — that path is
#      observable (full agent run + audit trail) and the failure becomes
#      visible.
#
# The wrapper centralizes the payload shape so every pre_run.py emits the
# same metadata fields, and centralizes the always-raise-on-failure
# contract so the caller cannot accidentally swallow an audit-write error.
# ---------------------------------------------------------------------------


class SuppressedWakeWriter:
    """Helper for ADR 0021 Stream B `pre_run.py` scripts.

    Use:

        async def main() -> int:
            executor = namespaced_executor_from_env(...)
            writer = AuditLogWriter(executor)
            sww = SuppressedWakeWriter(writer)
            anomalies = compute_anomalies(...)
            if anomalies:
                print(json.dumps({"wakeAgent": True}))
                return 0
            try:
                await sww.write_suppressed_wake(
                    skill_name="paid-media-anomaly-watcher",
                    pre_run_inputs=raw_pull_bytes,
                    decision_basis="delta_under_threshold",
                    next_scheduled_at=next_tick_iso,
                )
            except AuditWriteError:
                # Mirror-don't-gate: a silent suppress without an audit
                # trail is indistinguishable from a broken pre_run.py.
                print(json.dumps({"wakeAgent": True}))
                return 0
            print(json.dumps({"wakeAgent": False}))
            return 0

    `write_suppressed_wake` never swallows executor errors. Callers MUST
    treat any raised exception as the cue to fall back to wake.

    `write_emitted_wake` (#2253) is the wake-path sibling — same payload shape,
    same reserved-key guard, opposite caller contract: its callers swallow the
    raise, because a wake must never be gated on its own audit row.
    """

    def __init__(self, writer: AuditLogWriter) -> None:
        self._writer = writer

    async def write_suppressed_wake(
        self,
        *,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        actor: str = "agent",
        extra_metadata: Optional[dict] = None,
    ) -> str:
        """Emit one SUPPRESSED_WAKE row. Returns the inserted ULID.

        - `skill_name` is the SKILL.md name (matches audit_log.skill_name column).
        - `pre_run_inputs` is the raw polling-input bytes the decision was based on;
          the writer SHA-256s it and stores the digest only (per ADR 0008 — content
          off D1, hash on D1).
        - `decision_basis` is a short string code identifying which rule fired.
        - `next_scheduled_at` is the ISO 8601 UTC timestamp of the next cron tick.
        - `actor` defaults to "agent" (the pre_run script runs in the agent's
          context). Override only for testing.
        - `extra_metadata` merges additional keys (e.g. per-platform deltas) into
          the audit row's metadata payload.

        Raises `AuditWriteError` on executor failure. Callers MUST fall back
        to wake on failure.
        """
        meta: dict = {
            "decision_basis": decision_basis,
            "next_scheduled_at": next_scheduled_at,
        }
        if extra_metadata is not None:
            for key, value in extra_metadata.items():
                if key in meta:
                    raise ValueError(
                        f"extra_metadata key {key!r} reserved by SuppressedWakeWriter"
                    )
                meta[key] = value
        event = AuditEvent(
            action_type="SUPPRESSED_WAKE",
            actor=actor,
            actor_role=ActorRole.AGENT,
            skill_name=skill_name,
            input_payload=pre_run_inputs,
            metadata=meta,
        )
        return await self._writer.write(event)

    async def write_emitted_wake(
        self,
        *,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        actor: str = "agent",
        extra_metadata: Optional[dict] = None,
    ) -> str:
        """Emit one EMITTED_WAKE row (#2253). Returns the inserted ULID.

        The wake half of the same gate. Arguments are identical to
        `write_suppressed_wake` — including the reserved-key guard — so the two
        row shapes stay diffable on the same metadata fields.

        THE CALLER CONTRACT IS INVERTED, and deliberately so. A suppress that
        cannot be audited must escalate to a wake; a WAKE that cannot be audited
        must still wake. This method still raises on executor failure (it does
        not decide policy), but its callers in `pre_run.py` swallow the raise:
        the wake line is printed either way. A wake gated on its own audit row
        would be a gate built out of observability.
        """
        meta: dict = {
            "decision_basis": decision_basis,
            "next_scheduled_at": next_scheduled_at,
        }
        if extra_metadata is not None:
            for key, value in extra_metadata.items():
                if key in meta:
                    raise ValueError(
                        f"extra_metadata key {key!r} reserved by SuppressedWakeWriter"
                    )
                meta[key] = value
        event = AuditEvent(
            action_type="EMITTED_WAKE",
            actor=actor,
            actor_role=ActorRole.AGENT,
            skill_name=skill_name,
            input_payload=pre_run_inputs,
            metadata=meta,
        )
        return await self._writer.write(event)


# ---------------------------------------------------------------------------
# Convenience: read the env-bound HTTP executor used by bootstrap.sh
# ---------------------------------------------------------------------------


def writer_from_env() -> AuditLogWriter:
    """Return an AuditLogWriter wired to the env-bound D1 HTTP executor.

    Reads `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `AIE_D1_DATABASE_ID` from the
    process env. Raises `RuntimeError` if any is missing — that is a
    bootstrap-time invariant failure and should abort container start.
    """
    missing = [
        k
        for k in ("CF_ACCOUNT_ID", "CF_API_TOKEN", "AIE_D1_DATABASE_ID")
        if not os.environ.get(k)
    ]
    if missing:
        raise RuntimeError(
            f"audit_log.writer_from_env: missing required env vars {missing}; "
            "bootstrap.sh must set these from the per-customer secret bundle"
        )
    executor = HttpD1Executor(
        account_id=os.environ["CF_ACCOUNT_ID"],
        database_id=os.environ["AIE_D1_DATABASE_ID"],
        api_token=os.environ["CF_API_TOKEN"],
    )
    return AuditLogWriter(executor)


# Public surface (`from adapter.audit_log import *`) excludes the raw
# `HttpD1Executor` and `SqliteExecutor` constructors as of issue #861's
# TOCTOU hardening: external callers MUST go through
# `adapter.d1_env.namespaced_executor_from_env(...)` so every D1 access
# is bound to a customer slug. The raw classes remain importable by
# explicit name for the in-tree writer path (per the audit-log
# immutability design in
# `hermes-smd-overlay/plugins/hermes-smd-audit/immutability.py`) and for tests,
# but they are not advertised. A future PR can mark them
# underscore-private once the in-tree consumers migrate.
__all__ = [
    "ACCEPTED_ACTION_TYPES",
    "ActorRole",
    "AuditEvent",
    "AuditLogWriter",
    "AuditWriteError",
    "Executor",
    "SuppressedWakeWriter",
    "writer_from_env",
]
