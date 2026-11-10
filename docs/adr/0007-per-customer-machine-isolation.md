---
title: Per-Customer Machine Isolation — One Fly.io Machine Per Customer, No Shared Runtime
date: 2026-05-20
status: accepted
captain: Scott Durgan
supersedes: none
related-issue: https://github.com/venturecrane/ss-console/issues/828
---

# ADR 0007 — Per-Customer Machine Isolation

**Status:** Accepted (Captain decision; embedded in the Operator PRDs since first draft; recorded here as a standalone ADR per [#828](https://github.com/venturecrane/ss-console/issues/828)).

**Source:** The platform multi-tenant model and the safety substrate (invariant #7, cross-Machine query prohibition).

> **Storage-model note (2026-07-13).** The Phase-1 storage shape here — dedicated per-customer Cloudflare **D1 / R2 / Vectorize bindings** (`hermes-{slug}-{d1,r2,vectorize}`) — was corrected by [ADR 0009](./0009-cross-machine-query-prohibition.md)'s 2026-06-15 note: per-Machine storage is **SQLite on the `/opt/data` Fly volume plus R2 buckets** (`ss-operator-{slug}-skills`, shared `smd-customer-config`); the per-customer D1/Vectorize bindings described below were never built. The isolation guarantee is unchanged — it rests on the per-Machine topology (§Decision), not on these binding names.

---

## Context

The Operator runs as a long-lived agent process per customer. The product surface includes:

- Per-customer persona, voice samples, memory rules, person-mappings (PRD §9, §10)
- Per-customer connector credentials (OAuth tokens for Microsoft Graph, Filevine, DocuSign, etc.)
- Per-customer storage of audit log, drafts, learned patterns, voice corrections
- Per-customer skill catalog pinning and trust ceilings
- Per-customer scope (which folders the agent watches, which keywords block watching)

The architectural question this ADR resolves is: how is one customer's runtime separated from another's?

Three patterns were available:

1. **Shared runtime with tenant scoping.** One agent process serves all customers; tenant ID is a parameter on every call; storage queries are scoped by tenant; trust is placed in the scoping layer.
2. **Shared runtime with per-customer namespaces.** One process, namespaced storage and credentials per customer, application-level isolation. The runtime still has the ability to read across customers.
3. **Per-customer Machine.** One Fly.io Machine per customer (`hermes-{customer-slug}`). Each Machine has its own storage bindings, its own credentials, its own pinned skill catalog. No runtime path crosses customer boundaries.

Pattern 1 is the standard SaaS multi-tenant pattern and is appropriate when the cost of cross-tenant leakage is bounded and recoverable (e.g., a single customer's records briefly visible to another customer). For an Operator in a regulated practice (PI law, but the principle applies to every regulated vertical the platform will ship — workers' comp, immigration, medical, accounting), the cost of cross-tenant leakage is unbounded: privilege breach, bar discipline, customer-existential lawsuit. The platform cannot ship multi-tenant in any sense that allows a runtime path between customers.

Pattern 2 places the isolation guarantee in application code. It is auditable but only as good as the code review. A bug in the tenant-scoping layer is a cross-customer data leak.

Pattern 3 places the isolation guarantee in the deployment topology. Customer A's Machine has no network or storage path to Customer B's data. Cross-customer access is not "denied by code," it is "architecturally impossible."

## Decision

**One Fly.io Machine per customer. No shared runtime across customers. Multi-tenancy is achieved through deployment isolation, not runtime tenancy.**

Each customer gets:

- A dedicated Fly.io Machine named `hermes-{customer-slug}`, deployed in the region matching the customer's data-residency requirement (PRD §7.3 example shows `us-west-2 (lax)`).
- Dedicated D1, R2, and Vectorize bindings, all namespaced to the customer (PRD §7.6).
- Dedicated OAuth tokens for each capability adapter, stored at the per-customer Infisical path (per PRD §7.9 OAuth token lifecycle). **[Amended 2026-11-09: superseded by [ADR 0010](./0010-per-customer-oauth-token-storage.md). Customer OAuth tokens live on the customer's own Fly volume and are never stored in Infisical.]**
- A pinned content-hash SHA of the Hermes agent runtime (per PRD §7.4 skill loading and pinning — updates do not propagate without explicit Captain re-pin).
- Its own audit log, its own memory artifact, its own dashboard.

The control plane (SMD's operational layer) provisions, monitors, and updates Machines. The control plane can read audit data across customers; the runtime Machines cannot.

Boot-time invariant: at Machine boot, the runtime verifies its storage bindings include only its own customer's namespaces. Bindings outside its namespace cause boot refusal (PRD §7.5 invariant #7). This is the architectural enforcement of cross-Machine query prohibition (see [ADR 0009](./0009-cross-machine-query-prohibition.md) for the runtime-level rule that pairs with this deployment-level rule).

## Consequences

**Positive.**

- The cross-customer data leak failure mode is architecturally impossible, not merely audited against. "Did the tenant-scoping layer get bypassed?" has no failure case because there is no tenant-scoping layer.
- Per-customer blast radius. A bug in one customer's adapter, a credential compromise on one Machine, an infinite loop in one Machine — none reach another customer.
- Per-customer cost attribution. Compute, storage, and connector token usage are per-Machine. COGS is line-itemable to a customer.
- Per-customer skill pinning is mechanically clean. Updating one customer's pinned SHA does not require coordinating with other customers' runtimes.
- Per-customer compliance posture. Customers with stricter data-residency requirements get a Machine in the right region; customers with HIPAA scope get a Machine with the right BAA. The compliance unit of work is the Machine.
- The draft-for-review posture (ADR 0035) and the customer-owned memory artifact (ADR 0008) both reduce to per-customer Machine bindings. The decisions compose cleanly.

**Negative / accepted.**

- Operational complexity scales linearly with customer count. The control plane must provision, monitor, update, and decommission Machines per customer. SMD's tooling (`bin/provision-customer.sh`, control-plane dashboard, audit-log aggregation) must absorb this complexity.
- Per-customer fixed cost. Each Machine carries baseline compute and storage cost even when idle. The pricing analysis follow-on to ADR 0004 must account for this. We accept the floor; it is the cost of the isolation guarantee.
- Cross-customer learning (e.g., "voice patterns from one PI firm inform another PI firm's voice model") is architecturally impossible at the runtime layer. Platform-level patterns are SMD-curated, source-controlled, human-reviewed, and pushed through the skill catalog re-pin mechanism — never via runtime data propagation. This is the intended posture (PRD §10.5).
- Fly.io is the named host. If we later need to migrate (cost, capability, or strategic reasons), the per-Machine abstraction is portable in concept but the migration is per-customer. We accept this as a future cost; the alternative — building on a shared runtime to preserve migration simplicity — is the wrong trade.

**Out of scope.**

- Control plane sharing. The control plane is a single multi-tenant SMD application (extension of `crane-console`). It has cross-customer visibility because that is its job. The cross-customer prohibition applies to customer-runtime Machines, not to the SMD operational layer.
- Per-Machine intra-customer isolation. A single customer's data is shared across that customer's users (Principal, Operator, Compliance per PRD §11.6 multi-user role model). Per-user isolation within a single customer Machine is a role-permission concern, not a multi-tenancy concern.

## References

- [ADR 0005 — External-send identity](./0005-external-send-identity.md)
- [ADR 0008 Customer-owned memory artifact](./0008-customer-owned-memory-artifact.md)
- [ADR 0009 Cross-Machine query prohibition](./0009-cross-machine-query-prohibition.md)
- [Issue #828](https://github.com/venturecrane/ss-console/issues/828)
