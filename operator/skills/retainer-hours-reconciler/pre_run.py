#!/usr/bin/env python3
"""retainer-hours-reconciler pre-run script — ADR 0021 Stream B.

Runs BEFORE the Hermes cron daemon wakes the agent. Polls each active
retainer client's time-tracking connector, computes projected EOM
utilization, and decides whether the agent needs to run.

Wake / suppress decision (per references/algorithm.md):

  - WAKE if any client's projected EOM is in the OVER_CRITICAL,
    OVER_WARNING, or UNDER_CRITICAL band.
  - WAKE unconditionally on the weekly mandatory boundary (Monday morning).
  - WAKE if any previously-critical client is still pending owner
    acknowledgment (the auto-promotion ban).
  - SUPPRESS otherwise. Before printing wakeAgent:false write a
    SUPPRESSED_WAKE audit row; audit-write failure falls back to wake.

The decide() function is pure — no I/O, unit-tested directly with fake
inputs. run_once() wires real connectors + audit writer + stdout.

Exit codes:
    0 — decision emitted (wake or suppress)
    2 — fatal startup error (config missing). Caller must treat as
        wake-and-investigate.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence


# ---------------------------------------------------------------------------
# Connector protocol — future Harvest / Toggl / Float adapters implement this.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientUtilization:
    """One client's month-to-date hours + contracted retainer cap."""

    client_slug: str
    actual_mtd_hours: float
    contracted_monthly_hours: float
    mtd_days_elapsed: int
    calendar_days_in_month: int
    previously_critical_pending_ack: bool = False


class RetainerHoursConnector(Protocol):
    """Adapter interface time-tracking connectors must satisfy.

    Real implementations land in `operator/connectors/harvest/`,
    `toggl/`, `float/`. Each adapter reads the customer's OAuth tokens,
    calls the platform API, and returns one ClientUtilization per active
    retainer client.
    """

    def pull_utilizations(self) -> Sequence[ClientUtilization]:
        """Return one ClientUtilization per active retainer client."""
        ...


# ---------------------------------------------------------------------------
# Bucket thresholds — match references/algorithm.md. Defaults below; per-
# customer overrides come from customer.yaml.retainer_thresholds in a
# future PR.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BucketThresholds:
    over_critical_floor: float = 1.10  # >= 110% projected
    over_warning_floor: float = 0.95  # 95-110% projected
    balanced_floor: float = 0.65  # 65-95% projected
    under_warning_floor: float = 0.40  # 40-65% projected
    # below under_warning_floor → UNDER_CRITICAL
    low_confidence_min_days: int = 5  # < 5 mtd days → projection low confidence


# Critical bands that demand owner attention before month-end.
CRITICAL_WAKE_BUCKETS = frozenset({"OVER_CRITICAL", "OVER_WARNING", "UNDER_CRITICAL"})


@dataclass(frozen=True)
class BucketAssignment:
    client_slug: str
    bucket: str  # "OVER_CRITICAL" | "OVER_WARNING" | "BALANCED" | "UNDER_WARNING" | "UNDER_CRITICAL"
    projected_eom_pct: float
    low_confidence: bool


def _project_eom_pct(u: ClientUtilization) -> float:
    if u.contracted_monthly_hours <= 0 or u.mtd_days_elapsed <= 0:
        return 0.0
    projected_hours = u.actual_mtd_hours * (u.calendar_days_in_month / u.mtd_days_elapsed)
    return projected_hours / u.contracted_monthly_hours


def _assign_bucket(u: ClientUtilization, thresholds: BucketThresholds) -> BucketAssignment:
    pct = _project_eom_pct(u)
    low_conf = u.mtd_days_elapsed < thresholds.low_confidence_min_days
    if pct >= thresholds.over_critical_floor:
        bucket = "OVER_CRITICAL"
    elif pct >= thresholds.over_warning_floor:
        bucket = "OVER_WARNING"
    elif pct >= thresholds.balanced_floor:
        bucket = "BALANCED"
    elif pct >= thresholds.under_warning_floor:
        bucket = "UNDER_WARNING"
    else:
        bucket = "UNDER_CRITICAL"
    return BucketAssignment(
        client_slug=u.client_slug,
        bucket=bucket,
        projected_eom_pct=pct,
        low_confidence=low_conf,
    )


# ---------------------------------------------------------------------------
# Decision engine — pure function, no I/O. Unit-tested directly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientPlan:
    """One client the gate is waking about, as the gate saw it (#2253).

    Two of the three wake bases produce plans, and they carry unequal facts, so
    each plan states its own ``kind`` and every plan serializes the same keys —
    a plan dict has to make sense to the woken turn without the turn first
    deducing which basis produced it.

      - ``critical_band`` — the gate ran the bucket assignment, so ``bucket``,
        ``projected_eom_pct`` and ``low_confidence`` are populated.
      - ``pending_ack`` — the gate returned BEFORE assigning buckets (that
        branch precedes it in ``decide``), so the bucket fields are None. Null
        here means "the gate computed no bucket for this client on this tick",
        never "this client is fine": the turn reads the figure itself.

    ``low_confidence`` rides along because a projection from a handful of
    elapsed days is not the same claim as one from three weeks, and a turn that
    is handed the percentage without the flag will state both with the same
    confidence.
    """

    client_slug: str
    kind: str  # "critical_band" | "pending_ack"
    bucket: str | None = None
    projected_eom_pct: float | None = None
    low_confidence: bool | None = None


@dataclass(frozen=True)
class WakeDecision:
    wake: bool
    decision_basis: str
    pre_run_inputs_digest: bytes
    plans: tuple[ClientPlan, ...] = ()
    extra_metadata: dict = field(default_factory=dict)


def _is_weekly_boundary(now: datetime) -> bool:
    """Monday morning UTC = mandatory weekly cadence boundary.

    Monday = weekday() returns 0. Hour check intentionally absent — the
    cron schedule controls when the tick fires; if we are on Monday at
    all we are on the boundary.
    """
    return now.weekday() == 0


def decide(
    utilizations: Sequence[ClientUtilization],
    thresholds: BucketThresholds,
    *,
    raw_inputs_for_digest: bytes,
    now: datetime,
) -> WakeDecision:
    """Pure decision: does anything warrant waking the agent?

    Order of evaluation:
      1. Weekly mandatory boundary (Monday) → WAKE
      2. Any previously-critical pending ack → WAKE
      3. Any client in CRITICAL_WAKE_BUCKETS → WAKE
      4. Otherwise → SUPPRESS
    """
    if _is_weekly_boundary(now):
        # Deliberately NO plans (#2253). This wake is a cadence, not a finding:
        # nothing about any particular client triggered it, so there is no
        # per-item fact to hand over and an empty-but-present plan list would
        # falsely read as "the gate found nothing". The turn enumerates the full
        # roster here — that IS the job of the weekly report. The basis is what
        # tells it so, and `weekly_mandatory_boundary` is distinguishable from
        # every fail-open basis, which all end in `_fail_open`.
        return WakeDecision(
            wake=True,
            decision_basis="weekly_mandatory_boundary",
            pre_run_inputs_digest=raw_inputs_for_digest,
            extra_metadata={"client_count": len(utilizations)},
        )

    pending = [u.client_slug for u in utilizations if u.previously_critical_pending_ack]
    if pending:
        return WakeDecision(
            wake=True,
            decision_basis="previously_critical_pending_ack",
            pre_run_inputs_digest=raw_inputs_for_digest,
            plans=tuple(ClientPlan(client_slug=slug, kind="pending_ack") for slug in pending),
            extra_metadata={"pending_ack_clients": pending},
        )

    assignments = [_assign_bucket(u, thresholds) for u in utilizations]
    critical = [a for a in assignments if a.bucket in CRITICAL_WAKE_BUCKETS]
    if critical:
        return WakeDecision(
            wake=True,
            decision_basis="client_in_critical_band",
            pre_run_inputs_digest=raw_inputs_for_digest,
            plans=tuple(
                ClientPlan(
                    client_slug=a.client_slug,
                    kind="critical_band",
                    bucket=a.bucket,
                    projected_eom_pct=round(a.projected_eom_pct, 3),
                    low_confidence=a.low_confidence,
                )
                for a in critical
            ),
            extra_metadata={
                "critical_clients": [
                    {
                        "client_slug": a.client_slug,
                        "bucket": a.bucket,
                        "projected_eom_pct": round(a.projected_eom_pct, 3),
                    }
                    for a in critical
                ],
            },
        )

    return WakeDecision(
        wake=False,
        decision_basis="all_clients_in_balanced_or_under_warning",
        pre_run_inputs_digest=raw_inputs_for_digest,
        extra_metadata={
            "client_count": len(utilizations),
            "buckets": {a.bucket: 1 for a in assignments},
        },
    )


# ---------------------------------------------------------------------------
# Runtime entrypoint — wires connectors + audit writer + stdout.
# ---------------------------------------------------------------------------


def _next_scheduled_at(now: datetime, schedule_hours: int = 24) -> str:
    """ISO 8601 UTC for the next cron tick (default daily — retainer-hours
    is wired to a daily cadence with the Monday mandatory boundary handled
    in decide())."""
    return (now + timedelta(hours=schedule_hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# At most this many plans are serialized onto the wake line. A prompt-injected
# block is not free, and an agency with a hundred flagged clients does not need
# all hundred to start work. The cap ALWAYS announces itself (plans_total /
# plans_emitted / plans_truncated) — a truncated list that reads as complete is
# a check that cannot fail.
_MAX_SERIALIZED_PLANS = 50


def _emit_wake(decision: "WakeDecision | None" = None, *, basis: str | None = None) -> int:
    """Print the wake gate line — WITH the facts the gate already computed (#2253).

    Hermes reads only ``wakeAgent`` from the last stdout line and then injects
    the whole stdout verbatim into the woken agent's prompt (the "Script Output"
    block). Emitting a bare ``{"wakeAgent": true}`` therefore threw away every
    per-item fact ``decide`` had in hand: which client, which utilization band,
    what projected end-of-month percentage, and whether that projection was
    low-confidence. On 2026-08-10 the sibling escalator woke fact-free with its
    connector down and stated a specific date in the same alert that said it
    could not read dates (#2253, fixed there by PR #2259). A gate that hands
    over a bare boolean is asking the turn to supply the facts from somewhere,
    and with no source reachable "somewhere" was invention.

    Three shapes reach the woken turn, and they are distinguishable on the wire:

      - ``decision_basis`` + ``plans`` — a finding. Those clients are the set.
      - ``decision_basis: weekly_mandatory_boundary`` and no plans — the cadence
        wake. Absent plans here mean "no per-item finding exists", not
        "the gate saw nothing"; the turn enumerates the roster, which is the
        weekly report's job.
      - a ``*_fail_open`` basis and no plans — the gate woke BLIND. The turn
        must enumerate through the connectors and must not read the absent plan
        list as an empty one.
    """
    payload: dict = {"wakeAgent": True}
    resolved_basis = decision.decision_basis if decision is not None else basis
    if resolved_basis:
        payload["decision_basis"] = resolved_basis
    if decision is not None and decision.plans:
        emitted = decision.plans[:_MAX_SERIALIZED_PLANS]
        payload["plans"] = [
            {
                "client_slug": p.client_slug,
                "kind": p.kind,
                "bucket": p.bucket,
                "projected_eom_pct": p.projected_eom_pct,
                "low_confidence": p.low_confidence,
            }
            for p in emitted
        ]
        payload["plans_total"] = len(decision.plans)
        payload["plans_emitted"] = len(emitted)
        payload["plans_truncated"] = len(emitted) < len(decision.plans)
    print(json.dumps(payload))
    return 0


def _plan_counts(decision: "WakeDecision") -> dict:
    """The cap's own accounting, computed the one way ``_emit_wake`` computes it.

    Duplicating the slice in the audit path would let the row and the wake line
    disagree about how much was handed over — a discrepancy nobody would look
    for, in the one record kept to catch discrepancies.
    """
    if not decision.plans:
        return {}
    emitted = len(decision.plans[:_MAX_SERIALIZED_PLANS])
    return {
        "plans_total": len(decision.plans),
        "plans_emitted": emitted,
        "plans_truncated": emitted < len(decision.plans),
    }


async def _try_write_emitted_wake(
    audit_writer_factory,
    decision: "WakeDecision",
    *,
    skill_name: str,
    now: datetime,
) -> None:
    """Best-effort EMITTED_WAKE row for a real-decision wake (#2253).

    The suppress path logged its reasoning and the wake path logged nothing, so
    the ledger held a record of every tick the gate stayed quiet and no record
    of the ticks it fired. On 2026-08-10 the sibling escalator woke with its
    connector down and sent an alert stating a date it could not read; the only
    way anyone found it was reading the mailbox.

    BEST-EFFORT IS THE CONTRACT, and it inverts the suppress path's on purpose.
    Below, an audit failure escalates to a wake, because a silent suppress is
    indistinguishable from a broken gate. Here the wake is already the decision,
    so every failure — no writer wired, socket down, broker refusal, a writer
    object too old to have the method — is swallowed. A wake that a failed audit
    write could suppress or delay would be a gate made of observability.

    It is not free, and the cost is stated rather than assumed away: whatever
    writer is wired blocks the wake for its own timeout (the sibling skills'
    broker-socket writer caps at `_HEARTBEAT_TIMEOUT_SECONDS`). Bounded by that
    writer's ceiling, and never a change of decision.

    The cadence wake (`weekly_mandatory_boundary`) is a real decision and gets a
    row like any other; only the fail-open paths are excluded, because
    `no_audit_writer_fail_open` fires when there is no writer to call and
    `suppress_heartbeat_failed_fail_open` fires when a write just failed.
    """
    try:
        writer = audit_writer_factory()
        if writer is None:
            return
        await writer.write_emitted_wake(
            skill_name=skill_name,
            pre_run_inputs=decision.pre_run_inputs_digest,
            decision_basis=decision.decision_basis,
            next_scheduled_at=_next_scheduled_at(now),
            extra_metadata={**decision.extra_metadata, **_plan_counts(decision)},
        )
    except Exception:  # noqa: BLE001 — observability never gates the wake
        pass


def _emit_suppress() -> int:
    print(json.dumps({"wakeAgent": False}))
    return 0


def _utilization_to_dict(u: ClientUtilization) -> dict:
    return {
        "client_slug": u.client_slug,
        "actual_mtd_hours": u.actual_mtd_hours,
        "contracted_monthly_hours": u.contracted_monthly_hours,
        "mtd_days_elapsed": u.mtd_days_elapsed,
        "calendar_days_in_month": u.calendar_days_in_month,
        "previously_critical_pending_ack": u.previously_critical_pending_ack,
    }


async def run_once(
    connectors: Sequence[RetainerHoursConnector],
    thresholds: BucketThresholds,
    audit_writer_factory,  # () -> SuppressedWakeWriter | None
    *,
    skill_name: str = "retainer-hours-reconciler",
    now: datetime | None = None,
) -> int:
    """Driver. Returns the exit code; emits stdout JSON as a side effect.

    `audit_writer_factory` is called only when we would suppress. The
    factory may return None to signal "no audit writer wired (dev mode)"
    — in which case suppression falls back to wake (mirror-don't-gate).
    """
    now = now or datetime.now(timezone.utc)
    utilizations: list[ClientUtilization] = []
    raw_input_blob: bytes = b""
    for connector in connectors:
        utils = list(connector.pull_utilizations())
        utilizations.extend(utils)
        raw_input_blob += json.dumps([_utilization_to_dict(u) for u in utils], sort_keys=True).encode("utf-8")

    decision = decide(
        utilizations,
        thresholds,
        raw_inputs_for_digest=raw_input_blob,
        now=now,
    )
    if decision.wake:
        # The row goes in BEFORE the wake line, and cannot stop it (#2253).
        await _try_write_emitted_wake(audit_writer_factory, decision, skill_name=skill_name, now=now)
        return _emit_wake(decision)

    writer = audit_writer_factory()
    if writer is None:
        # Mirror-don't-gate: no writer = no trail = always wake.
        return _emit_wake(basis="no_audit_writer_fail_open")
    try:
        await writer.write_suppressed_wake(
            skill_name=skill_name,
            pre_run_inputs=decision.pre_run_inputs_digest,
            decision_basis=decision.decision_basis,
            next_scheduled_at=_next_scheduled_at(now),
            extra_metadata=decision.extra_metadata,
        )
    except Exception:  # noqa: BLE001 — any audit failure → wake
        return _emit_wake(basis="suppress_heartbeat_failed_fail_open")
    return _emit_suppress()


# ---------------------------------------------------------------------------
# CLI bootstrap. The cron daemon invokes this directly. Production wiring
# resolves connectors from customer.yaml.time_tracker binding and the audit
# writer from env vars (ADR 0008 d1_env.namespaced_executor_from_env). For
# now, the CLI returns wakeAgent: true with a clear startup-error code if it
# cannot find the connector adapters — the agent wakes, the failure becomes
# visible.
# ---------------------------------------------------------------------------


def main() -> int:
    customer_slug = os.environ.get("CUSTOMER_SLUG")
    if not customer_slug:
        sys.stderr.write("[pre_run] CUSTOMER_SLUG unset; falling back to wake\n")
        return _emit_wake(basis="customer_slug_unset_fail_open")

    # TODO(connector-adapters): wire real Harvest / Toggl / Float connectors
    # when their adapters ship. For now, the production cron-daemon
    # invocation does not have connectors and we fall through to wake.
    # Tests exercise run_once() directly with mock connectors.
    # The basis names the ACTUAL condition rather than borrowing the escalator's
    # `pre_run_crashed_fail_open`: nothing crashed here, the connectors were
    # never wired. A basis that misdescribes the failure is the same class of
    # invented fact #2253 is about, just committed by the gate instead of the
    # turn.
    sys.stderr.write(
        "[pre_run] retainer-hours connector adapters not yet shipped; "
        "falling back to wake (see ADR 0021 Stream B follow-on)\n"
    )
    return _emit_wake(basis="connectors_not_wired_fail_open")


if __name__ == "__main__":
    sys.exit(main())
