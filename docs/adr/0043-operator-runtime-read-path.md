---
title: Operator Runtime Read Path — Mirror Summaries + Live Per-Customer Reads (A+B)
date: 2026-06-08
status: accepted
captain: Scott Durgan
related-spec: docs/design/operator/01-admin-portal.md
related-adr: docs/adr/0007-per-customer-machine-isolation.md, docs/adr/0009-cross-machine-query-prohibition.md
---

# ADR 0043 — Operator Runtime Read Path

**Status:** Accepted (Captain decision, 2026-06-08). The shared component both Operator portals depend on; built first. **Amended by [ADR 0052](0052-operator-portal-management-console-not-data-surface.md) (2026-06-20):** the read path serves the **governance record** (the operator's own actions, as metadata with opaque object references), not client-data surfaces. There is no "matter timeline" / case-content drill-in; the `matter` read-kind and the per-vertical `matterRef` are removed in favor of grouping the governance log by opaque ref (ADR 0052 §2/§6). "matters/workstreams" below should be read in that light.

## Context

Each client's operator keeps its runtime state — audit log, activity, matters/workstreams, memory observations — on that
operator's own isolated per-customer Machine D1 ([ADR 0007](0007-per-customer-machine-isolation.md)). **[Amended 2026-11-09: "Machine D1" and "runtime D1" in this record mean Machine-local SQLite on the seat's Fly volume. The per-customer Cloudflare D1 was never built ([ADR 0062](0062-operator-cost-plane.md), Context). The isolation rule is unchanged.]** That isolation is a
security guarantee: the console cannot query a Machine's D1 directly, and cross-Machine queries are prohibited
([ADR 0009](0009-cross-machine-query-prohibition.md), invariant #7).

Both portals must nonetheless display this runtime state:

- The **admin fleet view** needs summary state across _all_ operators (health, last-activity, alert signals, cost
  basis) — and it must stay answerable even when an individual Machine is briefly down.
- A **per-operator drill-in** (admin or client) needs deep, fresh detail for _one_ operator (the full audit log, a
  specific draft/activity item, a matter's timeline).

These two needs have opposite characteristics (breadth + downtime-tolerance vs depth + freshness), so one mechanism
serves neither well.

## Decision

**Use both mechanisms, each for what it fits — "A+B".**

### B — Mirror summaries to a console-side per-customer store (fleet + rollup)

Each Machine pushes a small set of **read-relevant summary rows** to a console-side store keyed by customer
(generalizing the existing `fleet_status` heartbeat): health, last-activity timestamps, open-alert signals, and the
cost-rollup inputs. The console reads these directly for the fleet roster, alert feed, and cost views.

- Survives a Machine being down — the fleet view is always answerable.
- No per-request latency for the highest-traffic view.
- Carries a bounded staleness window (push cadence); acceptable for summary/rollup data.
- The console-side store remains **per-customer keyed**; fleet rollups read many per-customer rows but never join two
  Machines' _runtime_ D1.

### A — Live read-only endpoint on the Machine (deep drill-in)

Each Machine exposes a **thin, authenticated, read-only** endpoint that the console calls **per customer, on demand**
for deep detail (full audit log pages, a specific draft, a matter detail). The call is scoped to exactly one customer,
authenticated console→Machine, and audited.

- Always fresh; no duplication of the full runtime state.
- Tolerates the Machine being up (drill-in is an interactive action on a live operator).
- One customer per call — never a cross-customer surface.

### Invariants

- Every runtime read (A or B) is **scoped to a single customer**; no surface ever joins across customers' runtime state.
  The only cross-customer surfaces (fleet roster/alerts/cost) read the per-customer **summary** store, one row per
  customer.
- Reads are **read-only**; the read path never mutates a Machine's runtime state.
- Reads are audited at the console (who looked at what), distinct from the operator's own audit log.

The endpoint/auth shape for A (e.g. per-customer console→Machine credential, network posture) and the push transport
for B are an implementation detail to be specced with the build; this ADR fixes the A+B split and the invariants.

## Alternatives considered

- **A only (live reads for everything).** Rejected — the fleet view breaks whenever any Machine is down, and every fleet
  load pays N per-Machine round trips. A cross-fleet dashboard must not depend on every Machine being reachable.
- **B only (mirror everything).** Rejected — duplicating the full runtime state (entire audit logs, matter detail) to a
  console-side store is heavy, carries a staleness window on data that must be fresh on drill-in, and risks the mirror
  drifting from the immutable audit source of truth.
- **Console queries the Machine D1 directly.** Rejected outright — violates [ADR 0009](0009-cross-machine-query-prohibition.md).

## Consequences

**Positive.**

- The fleet view is fast and downtime-tolerant; drill-ins are fresh; isolation is preserved in both.
- Generalizes the existing `fleet_status` pattern rather than inventing a parallel one.
- One shared component unblocks both portals' live data; specced once.

**Negative / accepted.**

- Two mechanisms to build and keep coherent (a summary that is "close enough" plus a live detail path).
- The summary store's staleness window must be chosen and surfaced where it matters (e.g. "health as of N seconds ago").
- The Machine gains a small authenticated read surface (A); its auth posture must be designed carefully so it cannot
  become a cross-customer or write vector.

## Verification

1. The fleet roster/alerts/cost render entirely from the console-side per-customer summary store and remain answerable
   when a target Machine is unreachable.
2. A per-operator drill-in fetches deep detail live from exactly one Machine, scoped to one customer, and fails closed
   (empty state) if that Machine is unreachable — without affecting other operators' surfaces.
3. No read path issues a query spanning two customers' runtime D1.
4. Console-side reads are audited; the Machine read endpoint is read-only and rejects any mutation.

## References

- [Admin portal design](../design/operator/01-admin-portal.md) §7 (runtime read path)
- [Foundations](../design/operator/00-foundations.md) §6 (data substrate)
- [ADR 0007](0007-per-customer-machine-isolation.md), [ADR 0009](0009-cross-machine-query-prohibition.md) — the isolation this preserves
