#!/usr/bin/env python3
"""paid-media-anomaly-watcher pre-run script — ADR 0021 Stream B.

Runs BEFORE the Hermes cron daemon wakes the agent. Polls each enabled
paid-media platform, computes Δ vs. 7-day baselines, and decides whether
the agent needs to run.

Wake / suppress decision:

  - If `_compute_anomalies()` returns a non-empty list → emit
    `{"wakeAgent": true}` to stdout. The agent wakes with the full
    procedure (read platforms, run rubric, post Slack digest, etc.).

  - If the list is empty → write a `SUPPRESSED_WAKE` audit row, THEN
    emit `{"wakeAgent": false}`. The dashboard's watcher-health view
    surfaces these rows as the "agent ran quietly" signal; a scheduled
    tick with no audit row is the alarm signal (mirror-don't-gate per
    ADR 0016 extended to the cron-skip path).

  - If the audit write raises → fall back to `{"wakeAgent": true}`. A
    silent suppress without a trail is structurally indistinguishable
    from a silently-broken pre_run.py, so we always wake on audit
    failure. See ADR 0021 §"Two safety constraints".

The `_compute_anomalies(connectors)` function is the seam future paid-media
connector adapters slot into. The Meta / Google / LinkedIn adapters don't
exist yet in `operator/connectors/`; when they ship, the per-platform
arithmetic plugs in behind the `PaidMediaConnector` protocol below. The
wrapper machinery — anomaly aggregation, threshold comparison, audit
emission, fallback-to-wake — works today against any connector that
satisfies the protocol, and is unit-tested with a fake connector.

Exit codes:
    0 — decision emitted (whether wake or suppress)
    2 — fatal startup error (config missing, env var unset). The caller
        cron daemon must treat this as wake-and-investigate.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence


# ---------------------------------------------------------------------------
# Connector protocol — future Meta/Google/LinkedIn adapters implement this.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CampaignMetrics:
    """Per-campaign daily-aggregated metrics returned by a connector."""

    campaign_id: str
    platform: str
    cpl: float  # cost per lead, USD
    frequency: float  # ad exposure frequency
    ctr: float  # click-through rate, fraction (0-1)
    spend: float  # USD
    conversions: int


@dataclass(frozen=True)
class BaselineMetrics:
    """7-day rolling baseline returned alongside daily metrics."""

    cpl_avg: float
    frequency_avg: float
    ctr_avg: float
    spend_avg: float
    conversions_avg: float


@dataclass(frozen=True)
class CampaignSnapshot:
    """One campaign's daily metrics + its 7-day baseline."""

    daily: CampaignMetrics
    baseline: BaselineMetrics


class PaidMediaConnector(Protocol):
    """Adapter interface paid-media platform connectors must satisfy.

    Real implementations land in `operator/connectors/meta_ads/`,
    `google_ads/`, `linkedin_ads/`. Each connector reads the customer's
    OAuth tokens, calls the platform API, returns aggregated daily
    metrics + the rolling baseline.
    """

    def pull_snapshots(self) -> Sequence[CampaignSnapshot]:
        """Return one CampaignSnapshot per active campaign."""
        ...


# ---------------------------------------------------------------------------
# Anomaly thresholds — tuned per customer.yaml.anomaly_thresholds in a
# future PR. Defaults below match the rubric in
# `references/categorization-rubric.md`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyThresholds:
    cpl_spike_multiplier: float = 2.0  # CPL > 2× baseline → spike
    frequency_ceiling: float = 5.0  # frequency > 5 → saturation
    ctr_collapse_ratio: float = 0.6  # CTR < 60% of baseline → collapse
    conversion_drop_ratio: float = 0.7  # conversions < 70% of baseline → drop


@dataclass(frozen=True)
class Anomaly:
    """One detected anomaly. Carried in the audit row as metadata only
    when wakeAgent: true; not surfaced when suppressing."""

    campaign_id: str
    platform: str
    kind: str  # "cpl_spike" | "frequency_saturation" | "ctr_collapse" | "conversion_drop"
    severity: str  # "CRITICAL" | "WARN"
    detail: str


def _compute_anomalies(
    snapshots: Sequence[CampaignSnapshot],
    thresholds: AnomalyThresholds,
) -> list[Anomaly]:
    """Apply the threshold rubric to each campaign's daily-vs-baseline data.

    Returns the list of anomalies that warrant agent attention. An empty
    list means "no anomalies, suppress the wake." Multi-platform support
    is implicit — each snapshot carries its platform; the rubric is
    platform-agnostic (CPL is CPL).
    """
    anomalies: list[Anomaly] = []
    for snap in snapshots:
        d, b = snap.daily, snap.baseline
        if b.cpl_avg > 0 and d.cpl > thresholds.cpl_spike_multiplier * b.cpl_avg:
            anomalies.append(
                Anomaly(
                    d.campaign_id,
                    d.platform,
                    "cpl_spike",
                    "CRITICAL",
                    f"CPL ${d.cpl:.2f} > {thresholds.cpl_spike_multiplier}× baseline ${b.cpl_avg:.2f}",
                )
            )
        if d.frequency > thresholds.frequency_ceiling:
            anomalies.append(
                Anomaly(
                    d.campaign_id,
                    d.platform,
                    "frequency_saturation",
                    "WARN",
                    f"frequency {d.frequency:.2f} > ceiling {thresholds.frequency_ceiling}",
                )
            )
        if b.ctr_avg > 0 and d.ctr < thresholds.ctr_collapse_ratio * b.ctr_avg:
            anomalies.append(
                Anomaly(
                    d.campaign_id,
                    d.platform,
                    "ctr_collapse",
                    "WARN",
                    f"CTR {d.ctr:.3f} < {thresholds.ctr_collapse_ratio:.0%} of baseline {b.ctr_avg:.3f}",
                )
            )
        if b.conversions_avg > 0 and d.conversions < thresholds.conversion_drop_ratio * b.conversions_avg:
            anomalies.append(
                Anomaly(
                    d.campaign_id,
                    d.platform,
                    "conversion_drop",
                    "CRITICAL",
                    f"conversions {d.conversions} < "
                    f"{thresholds.conversion_drop_ratio:.0%} of baseline "
                    f"{b.conversions_avg:.1f}",
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# Decision engine — pure function, no I/O. Unit-tested directly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyPlan:
    """One firing anomaly as the gate saw it, carried to the woken turn (#2253).

    ``detail`` is the comparison the rubric actually made, VERBATIM — the
    observed value against the baseline it was measured against. The kind and
    severity alone say an anomaly exists; only the detail says what it was, and
    a digest-writing turn that is handed the label without the numbers has to
    source the numbers from somewhere. With the connector down, "somewhere"
    is invention (the failure this whole change closes).
    """

    campaign_id: str
    platform: str
    kind: str
    severity: str
    detail: str


@dataclass(frozen=True)
class WakeDecision:
    wake: bool  # True → agent wakes; False → suppressed
    decision_basis: str
    pre_run_inputs_digest: bytes  # raw bytes the digest will be computed from
    anomaly_count: int
    extra_metadata: dict
    plans: tuple[AnomalyPlan, ...] = ()


def decide(
    snapshots: Sequence[CampaignSnapshot],
    thresholds: AnomalyThresholds,
    *,
    raw_inputs_for_digest: bytes,
) -> WakeDecision:
    """Pure decision: do the anomalies (if any) clear the threshold?

    `raw_inputs_for_digest` is the raw bytes the connector returned;
    digesting it gives the audit-row provenance hash. The caller is free
    to choose what bytes to feed in (typically the serialized snapshot
    list); the digest stability is what matters, not the exact format.
    """
    anomalies = _compute_anomalies(snapshots, thresholds)
    if anomalies:
        return WakeDecision(
            wake=True,
            decision_basis="anomaly_above_threshold",
            pre_run_inputs_digest=raw_inputs_for_digest,
            anomaly_count=len(anomalies),
            extra_metadata={
                "anomalies": [
                    {
                        "campaign_id": a.campaign_id,
                        "platform": a.platform,
                        "kind": a.kind,
                        "severity": a.severity,
                    }
                    for a in anomalies
                ],
            },
            plans=tuple(
                AnomalyPlan(
                    campaign_id=a.campaign_id,
                    platform=a.platform,
                    kind=a.kind,
                    severity=a.severity,
                    detail=a.detail,
                )
                for a in anomalies
            ),
        )
    return WakeDecision(
        wake=False,
        decision_basis="delta_under_threshold",
        pre_run_inputs_digest=raw_inputs_for_digest,
        anomaly_count=0,
        extra_metadata={"campaigns_evaluated": len(snapshots)},
    )


# ---------------------------------------------------------------------------
# Runtime entrypoint — wires connectors + audit writer + stdout.
# ---------------------------------------------------------------------------


def _next_scheduled_at(now: datetime, schedule_hours: int = 24) -> str:
    """ISO 8601 UTC for the next cron tick (default daily)."""
    return (now + timedelta(hours=schedule_hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# At most this many plans are serialized onto the wake line. A prompt-injected
# block is not free, and an agency with a hundred firing campaigns does not need
# all hundred to start work. The cap ALWAYS announces itself (plans_total /
# plans_emitted / plans_truncated) — a truncated list that reads as complete is
# a check that cannot fail.
_MAX_SERIALIZED_PLANS = 50


def _emit_wake(decision: "WakeDecision | None" = None, *, basis: str | None = None) -> int:
    """Print the wake gate line — WITH the facts the gate already computed (#2253).

    Hermes reads only ``wakeAgent`` from the last stdout line and then injects
    the whole stdout verbatim into the woken agent's prompt (the "Script Output"
    block). Emitting a bare ``{"wakeAgent": true}`` therefore threw away every
    per-item fact ``decide`` had in hand: which campaign, on which platform,
    which anomaly kind, at what severity, and the observed-vs-baseline
    comparison that tripped the rubric. On 2026-08-10 the sibling escalator woke
    fact-free with its connector down and stated a specific date in the same
    alert that said it could not read dates (#2253, fixed there by PR #2259).
    A gate that hands over a bare boolean is asking the turn to supply the facts
    from somewhere, and with no source reachable "somewhere" was invention.

    Fail-open callers have no decision — they pass ``basis`` so the woken turn
    knows it woke blind and must enumerate the platforms itself instead of
    treating an absent plan list as an empty one.
    """
    payload: dict = {"wakeAgent": True}
    resolved_basis = decision.decision_basis if decision is not None else basis
    if resolved_basis:
        payload["decision_basis"] = resolved_basis
    if decision is not None and decision.plans:
        emitted = decision.plans[:_MAX_SERIALIZED_PLANS]
        payload["plans"] = [
            {
                "campaign_id": p.campaign_id,
                "platform": p.platform,
                "kind": p.kind,
                "severity": p.severity,
                "detail": p.detail,
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

    Not called on the fail-open paths: `no_audit_writer_fail_open` fires because
    there is no writer to call, and `suppress_heartbeat_failed_fail_open` fires
    because a write to that writer just failed.
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


async def run_once(
    connectors: Sequence[PaidMediaConnector],
    thresholds: AnomalyThresholds,
    audit_writer_factory,  # () -> SuppressedWakeWriter (or None to skip)
    *,
    skill_name: str = "paid-media-anomaly-watcher",
    now: datetime | None = None,
) -> int:
    """Driver. Returns the exit code; emits stdout JSON as a side effect.

    `audit_writer_factory` is called only when we'd suppress, so a wake
    decision never needs an audit-log connection. The factory may return
    None to signal "no audit writer wired (e.g. dev mode)" — in which
    case suppression falls back to wake (per the safety contract).
    """
    now = now or datetime.now(timezone.utc)
    snapshots: list[CampaignSnapshot] = []
    raw_input_blob: bytes = b""
    for connector in connectors:
        snaps = list(connector.pull_snapshots())
        snapshots.extend(snaps)
        raw_input_blob += json.dumps([_snapshot_to_dict(s) for s in snaps], sort_keys=True).encode("utf-8")

    decision = decide(snapshots, thresholds, raw_inputs_for_digest=raw_input_blob)
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


def _snapshot_to_dict(s: CampaignSnapshot) -> dict:
    return {
        "campaign_id": s.daily.campaign_id,
        "platform": s.daily.platform,
        "cpl": s.daily.cpl,
        "frequency": s.daily.frequency,
        "ctr": s.daily.ctr,
        "spend": s.daily.spend,
        "conversions": s.daily.conversions,
        "baseline": {
            "cpl_avg": s.baseline.cpl_avg,
            "frequency_avg": s.baseline.frequency_avg,
            "ctr_avg": s.baseline.ctr_avg,
            "spend_avg": s.baseline.spend_avg,
            "conversions_avg": s.baseline.conversions_avg,
        },
    }


# ---------------------------------------------------------------------------
# CLI bootstrap. The cron daemon invokes this directly. Production wiring
# resolves connectors from customer.yaml and the audit writer from env vars
# (per ADR 0008 — d1_env.namespaced_executor_from_env). For now, this CLI
# returns wakeAgent: true with a clear startup-error code if it cannot find
# the connector adapters — the agent wakes, the failure becomes visible.
# ---------------------------------------------------------------------------


def main() -> int:
    customer_slug = os.environ.get("CUSTOMER_SLUG")
    if not customer_slug:
        # No customer context → can't bind the audit writer to a customer
        # D1 binding. Wake the agent (mirror-don't-gate fallback) and let
        # the agent surface the missing-env error.
        sys.stderr.write("[pre_run] CUSTOMER_SLUG unset; falling back to wake\n")
        return _emit_wake(basis="customer_slug_unset_fail_open")

    # TODO(connector-adapters): wire real connectors when
    # `operator/connectors/meta_ads/`, `google_ads/`, `linkedin_ads/`
    # ship. For now, the production cron-daemon invocation does not have
    # connectors and we fall through to wake. Tests exercise run_once()
    # directly with mock connectors.
    # The basis names the ACTUAL condition rather than borrowing the escalator's
    # `pre_run_crashed_fail_open`: nothing crashed here, the connectors were
    # never wired. A woken turn that reads a basis it can trust literally is the
    # whole point of #2253 — a basis that misdescribes the failure is the same
    # class of invented fact, just committed by the gate instead of the turn.
    sys.stderr.write(
        "[pre_run] paid-media connector adapters not yet shipped; "
        "falling back to wake (see ADR 0021 Stream B follow-on)\n"
    )
    return _emit_wake(basis="connectors_not_wired_fail_open")


if __name__ == "__main__":
    sys.exit(main())
