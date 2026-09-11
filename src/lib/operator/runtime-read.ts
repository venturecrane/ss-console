/**
 * Live per-customer runtime read client (ADR 0043 path A) — the frozen
 * console→Machine read contract both portals' deep drill-ins import.
 *
 * Each operator keeps its runtime state (audit log, drafts, matters, activity)
 * on its own isolated Machine D1 (ADR 0007). The console cannot query that D1
 * across the isolation boundary (ADR 0009). This client is the controlled,
 * audited read path: it fetches deep detail for EXACTLY ONE customer, on
 * demand, read-only, and fails closed (empty) when the Machine is unreachable
 * so one operator being down never breaks another's surface.
 *
 * Four invariants this module encodes and never bends (ADR 0043 §Invariants):
 *   1. Single customer per call. The function takes one `customerSlug`; there
 *      is no list form and no surface here ever joins across customers.
 *   2. Read-only. The transport exposes `read` and nothing else — no mutation
 *      path exists.
 *   3. Audited at the console. Every read attempt records who looked at what,
 *      distinct from the operator's own runtime audit log.
 *   4. Fail-closed. A transport error resolves to an empty result with a
 *      reason, never a throw that bubbles into a portal render.
 *
 * The transport and the audit sink are injected; the production wiring (the
 * authenticated console→Machine HTTP call and the read-audit D1 insert) lives
 * in runtime-read-transport.ts. The Machine-side read endpoint lives in the
 * overlay repo; this contract is the console side of that seam.
 */

/** The classes of runtime detail a drill-in can request. Each maps to a
 * read-only endpoint on the Machine. Extend as drill-in surfaces are added.
 *
 * Reconciliation with the Machine (overlay `shared/runtime_read.py`,
 * checked 2026-07-04 / #1678): every kind listed here is in the Machine's
 * SUPPORTED_KINDS, so no console request can 404. `audit_log`,
 * `memory_export`, `config_export`, `audit_export`, and `jobs` are REAL
 * (backed by a table or the broker ledger); `activity` is supported but
 * serves an honest empty page until its runtime table lands — portal Home
 * therefore derives its activity feed from `audit_log`, and only the admin
 * observe surface still requests `activity` (renders the empty state it was
 * built for). The Machine additionally serves `config` / `draft` / `matter`
 * for the decommission pipeline and drift audit; those are deliberately NOT
 * listed here.
 *
 * `audit_export` serves the FULL audit_log row — `matter_ref`,
 * `trust_ceiling`, the digests, the metadata blob, and the hash-chain
 * columns. The UI kind (`audit_log`) deliberately omits them, which is
 * correct for a narrative activity feed and wrong for a compliance record:
 * "what authorized this action" lives in `trust_ceiling` + the routine
 * attribution inside `metadata`, and neither crosses the seam on the UI
 * kind (ss#2122). Consumption is restricted by construction rather than by
 * kind availability: the only portal caller is the role-gated per-matter
 * audit record path (`portal/operator/object-audit-record.ts`), reachable
 * solely by a firm's Named Administrator or its compliance reviewer. An
 * ordinary drill-in must keep requesting `audit_log`.
 *
 * `memory_export` serves an allow-listed Machine-local memory table one at a
 * time via the `table` query field (ADR 0016 mirror tables + `peer_preferences`,
 * the relationship model's learned lane — per-peer working-preference memory the
 * operator captured on Hermes' native memory loop). The Machine refuses any
 * table outside its allow-list.
 *
 * `config_export` serves an allow-listed authored CONTENT block from the live
 * `customer.yaml` via the `section` query field — `relationship` (the model's
 * authored behavioral lane, ADR 0048) is the only section today. Unlike a whole
 * config read, it is allow-listed to non-secret sections so the connector
 * secrets in `customer.yaml` can never cross the seam. The Machine refuses any
 * section outside its allow-list.
 *
 * `jobs` serves the B1 durable-job control plane (ADR 0051) — the broker-owned
 * job ledger projected to the operator-visible control facts (status, cost,
 * lease, result, error) so a background job is verifiable end-to-end over the
 * same authenticated read seam. It takes no extra query field; the Machine
 * reads its own ledger over the broker socket and returns a single page. */
export type RuntimeReadKind =
  | 'audit_log'
  /** Full-row compliance read. See the `audit_export` paragraph above for the
   * restriction on who may request it. */
  | 'audit_export'
  | 'activity'
  | 'memory_export'
  | 'config_export'
  | 'jobs'
  // Live runtime exposure overrides (ss#2003 Q7 — the entitlement dial). The
  // Machine serves its volume-backed override store directly, so the portal
  // settings surface and the live probes read the ACTUAL enforced posture,
  // never a projection.
  | 'entitlements'
  // Per-person token meter (#2070). The Machine attributes each API request to
  // the inbound sender whose email opened the turn, or to `system:<platform>`
  // for cron/skills/delegated work, and aggregates by (day, person, model).
  // SMD-only: this feeds the admin cost plane, never a client surface.
  | 'usage_export'
  // Chronology-package jobs (routine 11, ss#2614): the broker-owned
  // medchron_jobs ledger projected to states, counts, cents and the delivery
  // folder id. Never a document, never a page of text (ADR 0052: the console
  // is a management surface, not a data surface). One page, no query field.
  | 'medchron_jobs'

export interface RuntimeReadQuery {
  kind: RuntimeReadKind
  /** Opaque pagination cursor for list kinds (audit_log, activity). */
  cursor?: string | null
  /** Specific item id for detail kinds (draft, matter). */
  id?: string | null
  /** Page size hint for list kinds; the Machine clamps it. */
  limit?: number | null
  /** Allow-listed table name for the `memory_export` kind (e.g.
   * `peer_preferences`); ignored by other kinds. The Machine refuses any
   * table outside its allow-list. */
  table?: string | null
  /** Allow-listed section name for the `config_export` kind (e.g.
   * `relationship`); ignored by other kinds. The Machine refuses any section
   * outside its allow-list. */
  section?: string | null
}

export interface RuntimeReadActor {
  actor: string
  actorRole: string
}

export type RuntimeReadFailure = 'unreachable' | 'unauthorized' | 'not_configured'

export type RuntimeReadResult =
  | { ok: true; kind: RuntimeReadKind; data: unknown }
  | { ok: false; kind: RuntimeReadKind; reason: RuntimeReadFailure }

/**
 * Injected console→Machine transport. Read-only by construction — the only
 * method is `read`. Scoped to one customer per call. Throws on transport
 * failure (unreachable, timeout); the client collapses that to a fail-closed
 * empty result. A thrown {@link RuntimeReadUnauthorizedError} is mapped to the
 * `unauthorized` reason instead of `unreachable`.
 */
export interface MachineRuntimeTransport {
  read(customerSlug: string, query: RuntimeReadQuery): Promise<{ data: unknown }>
}

/** Injected console-side read-audit sink. Records the read ATTEMPT (who, what
 * customer, what kind, outcome) — distinct from the operator's runtime audit. */
export interface RuntimeReadAudit {
  record(row: {
    customerSlug: string
    actor: string
    actorRole: string
    kind: RuntimeReadKind
    outcome: 'ok' | RuntimeReadFailure
  }): Promise<void>
}

/** Transports throw this to signal an auth failure (vs a reachability failure),
 * so the client can distinguish `unauthorized` from `unreachable`. */
export class RuntimeReadUnauthorizedError extends Error {
  constructor() {
    super('console→Machine runtime read was unauthorized')
    this.name = 'RuntimeReadUnauthorizedError'
  }
}

/**
 * Read deep runtime detail for one customer. Audits the attempt (always),
 * returns the data on success, and fails closed to an empty result with a
 * reason on any transport failure — never throws into the caller.
 *
 * The single `customerSlug` parameter is the isolation guarantee: there is no
 * way to express a cross-customer read through this signature.
 */
export async function readMachineRuntime(
  deps: { transport: MachineRuntimeTransport; audit: RuntimeReadAudit },
  customerSlug: string,
  query: RuntimeReadQuery,
  actor: RuntimeReadActor
): Promise<RuntimeReadResult> {
  try {
    const { data } = await deps.transport.read(customerSlug, query)
    await safeAudit(deps.audit, {
      customerSlug,
      actor: actor.actor,
      actorRole: actor.actorRole,
      kind: query.kind,
      outcome: 'ok',
    })
    return { ok: true, kind: query.kind, data }
  } catch (err) {
    const reason: RuntimeReadFailure =
      err instanceof RuntimeReadUnauthorizedError ? 'unauthorized' : 'unreachable'
    await safeAudit(deps.audit, {
      customerSlug,
      actor: actor.actor,
      actorRole: actor.actorRole,
      kind: query.kind,
      outcome: reason,
    })
    return { ok: false, kind: query.kind, reason }
  }
}

/** Audit failures must never turn a read into a throw — the read path is
 * fail-closed end to end. A failed audit write is swallowed (the read outcome
 * is what the caller needs); production audit should log its own failures. */
async function safeAudit(
  audit: RuntimeReadAudit,
  row: {
    customerSlug: string
    actor: string
    actorRole: string
    kind: RuntimeReadKind
    outcome: 'ok' | RuntimeReadFailure
  }
): Promise<void> {
  try {
    await audit.record(row)
  } catch {
    // swallow — see doc comment
  }
}
