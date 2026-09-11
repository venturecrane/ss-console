/**
 * Operator settings — the connector-status rows the console renders from
 * the config projection.
 *
 * The trust-ceiling vocabulary, its label, and the skill-toggle row shape
 * that once lived here went with the components that rendered them
 * (2026-09-10; nothing mounted those components).
 *
 * Source of truth is `customer.yaml` per
 * [ADR 0012](../../../../docs/adr/0012-customer-yaml-storage.md); the
 * portal D1 `customer_configs` table is the projected read replica
 * (see `src/lib/portal/customer-config.ts`) and these helpers shape
 * that projection for the facet resolvers that consume them
 * (overview, skills, connections).
 *
 * Voice-sample management was removed 2026-07-15 (Captain close-out):
 * the portal surface was chrome over a stub — no ingestion wiring
 * existed. Client-voice establishment is its own workstream.
 */

// ---------------------------------------------------------------------------
// Connector status
// ---------------------------------------------------------------------------

/**
 * Closed vocabulary for the capability conformance harness's
 * `health_check()` result. `ok` means the connector authenticated
 * and the latest probe round-tripped. `warn` is a degraded but
 * functional state (quota near limit, partial scope grant). `fail`
 * is unauthenticated or unreachable. `unconfigured` is the
 * customer.yaml shape: the capability is named in `connectors:`
 * but no health check has run yet. See
 * `docs/specs/operator/capability-contracts.md`.
 */
export type ConnectorHealth = 'ok' | 'warn' | 'fail' | 'unconfigured'

/**
 * One connector row.
 *
 *   capabilityName    — closed-union value from CapabilityName
 *                       (e.g. `Email`, `PracticeManagement`,
 *                       `Calendar`). Reads straight from
 *                       customer.yaml's `connectors:` map keys.
 *   adapter           — adapter slug (e.g. `filevine`,
 *                       `microsoft-graph`).
 *   health            — current health from the conformance
 *                       harness. `unconfigured` when no probe has
 *                       run yet.
 *   reconsentRequired — true when the harness has signaled the
 *                       customer needs to re-grant scope (token
 *                       expired or revoked). Drives a
 *                       "Re-authorize" affordance in the UI.
 */
export interface ConnectorStatusRow {
  capabilityName: string
  adapter: string
  /** Authored `auth_mode` (e.g. 'authorization_code') — decides whether SMD
   *  can re-establish the connection alone or the firm must approve a fresh
   *  authorization. Null when not authored. */
  authMode: string | null
  health: ConnectorHealth
  reconsentRequired: boolean
}

/**
 * Shape of the `connectors:` map in customer.yaml after JSON parse.
 * Modeled loosely because the projection stores it as `unknown` and
 * we cannot rely on schema validation having run inside the portal.
 */
interface ConnectorYamlEntry {
  adapter?: unknown
}

/**
 * Project the customer.yaml `connectors:` map into a connector row
 * list. The health for every connector is `unconfigured` today
 * because the conformance harness has not yet been wired to the
 * portal Worker; PR #949 (Filevine) and #822 (Microsoft Graph) own
 * the upstream connector binding. When the harness lands, swap
 * `loadConnectorHealth` below; the row shape and rendering stay.
 */
export function connectorRowsFromCustomerYaml(connectorsYaml: unknown): ConnectorStatusRow[] {
  if (!connectorsYaml || typeof connectorsYaml !== 'object') return []
  const entries = Object.entries(connectorsYaml as Record<string, unknown>)
  const rows: ConnectorStatusRow[] = []
  for (const [capabilityName, raw] of entries) {
    if (!raw || typeof raw !== 'object') continue
    const entry = raw as ConnectorYamlEntry
    const adapter = typeof entry.adapter === 'string' ? entry.adapter : ''
    const authModeRaw = (entry as Record<string, unknown>)['auth_mode']
    rows.push({
      capabilityName,
      adapter,
      authMode: typeof authModeRaw === 'string' ? authModeRaw : null,
      health: 'unconfigured',
      reconsentRequired: false,
    })
  }
  rows.sort((a, b) => a.capabilityName.localeCompare(b.capabilityName))
  return rows
}

// ---------------------------------------------------------------------------
// Composite settings view — REMOVED (2026-07-15, Captain close-out of inert
// voice chrome). `loadSettingsView` had no callers; it existed to carry a
// voice-samples list whose fetch was a stub returning [] and whose portal
// endpoint only logged intent. Real voice-sample ingestion is the #1851 /
// voice-establishment workstream; nothing renders sample chrome until the
// wiring exists (feedback: never build the chrome ahead of the wiring).
// The per-persona row projections (trust-ceiling rows, skill-toggle rows)
// went the same way on 2026-09-09: no facet resolver called them. What remains
// is the closed vocabularies, their labels, and the connector rows.
// ---------------------------------------------------------------------------
