/**
 * Connections surface model (client-portal §5.8). Joins the connector status
 * rows (settings.ts) with each connector's resolved credential custody
 * (ADR 0042) so the /connections surface can show, per connector: status,
 * health, and whether SMD can reach the secret.
 *
 * Pure — no I/O. Reuses connectorRowsFromCustomerYaml for the base rows
 * (capability, adapter, health) and layers custody on top by re-reading the
 * per-connector `credential_custody` from the same projected YAML, resolved
 * against the client-level default.
 */

import { connectorRowsFromCustomerYaml, type ConnectorStatusRow } from './settings'
import { isRecord } from '../../api/helpers'
import {
  resolveCredentialCustody,
  smdCanReachSecret,
  parseCredentialCustody,
  type CredentialCustody,
} from '../../operator/credential-custody'

/**
 * Mailbox visibility, rendered INSIDE the email connection's row (Captain,
 * 2026-07-15: a standalone "Email" box above an "AgentMail" row read as two
 * disconnected things when they describe the same mailbox).
 */
export interface EmailAccessView {
  sendAs: string | null
  sees: string[]
  neverSees: string[]
}

export interface ConnectionRow extends ConnectorStatusRow {
  /** Resolved custody: per-connector value → client default → delegated. */
  custody: CredentialCustody
  /** True when SMD staff may reach/rotate the secret (delegated only). */
  smdReachable: boolean
}

/**
 * Build the connections rows for a customer: the connector status rows plus
 * each connector's resolved custody. `connectorsYaml` is the projected
 * `connectors` map (Partial<Record<CapabilityName, Connector>>); the per-row
 * custody is read back from it by capability key.
 */
export function buildConnectionRows(
  connectorsYaml: unknown,
  clientDefault: CredentialCustody
): ConnectionRow[] {
  const base = connectorRowsFromCustomerYaml(connectorsYaml)
  return base.map((row) => {
    const entry = isRecord(connectorsYaml) ? connectorsYaml[row.capabilityName] : undefined
    const perConnector = isRecord(entry)
      ? parseCredentialCustody(entry['credential_custody'])
      : null
    const custody = resolveCredentialCustody(perConnector, clientDefault)
    return { ...row, custody, smdReachable: smdCanReachSecret(custody) }
  })
}

/**
 * Client-facing product names for adapter slugs (Captain finding, 2026-07-15:
 * raw slugs like "agentmail"/"smokeball" reached the page). Closed display map,
 * same shape as the tier/ceiling maps in the work resolver: the internal slug
 * never renders; an unmapped slug falls back to the capability label rather
 * than leaking. Extend as connectors are authored.
 */
const ADAPTER_DISPLAY_NAMES: Record<string, string> = {
  agentmail: 'AgentMail',
  smokeball: 'Smokeball',
  clio: 'Clio',
  filevine: 'Filevine',
  brave: 'Brave Search',
  'm365-mail': 'Microsoft 365 Mail',
  'm365-calendar': 'Microsoft 365 Calendar',
  'ms-365': 'Microsoft 365',
  'google-gmail': 'Gmail',
  'google-calendar': 'Google Calendar',
  'google-drive': 'Google Drive',
}

/** Product display name for an adapter slug; null when unmapped (caller falls
 *  back to the capability label — never the raw slug). */
export function adapterDisplayName(adapter: string): string | null {
  return ADAPTER_DISPLAY_NAMES[adapter] ?? null
}

/** Client-facing label for a capability key: "PracticeManagement" → "Practice management". */
export function capabilityDisplayName(capabilityName: string): string {
  const spaced = capabilityName.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase()
}

/**
 * Client-facing label for a custody mode. "Managed by SMD" (not "Delegated to
 * SMD" — Captain, 2026-07-15: "delegated" read as something out of the
 * client's control rather than a service being provided).
 */
export function formatCustody(custody: CredentialCustody): string {
  return custody === 'self_held' ? 'Key held by your firm' : 'Managed by SMD'
}

/**
 * Product-truth note for connections that are SMD-provided defaults rather
 * than the client's own system. Today that is exactly the AgentMail mailbox
 * (Captain, 2026-07-15: be clear it is a default, not a requirement). Any
 * engagement-specific plan (e.g. moving onto a particular firm's Microsoft
 * 365) is authored config and renders when that connector is authored, never
 * from template copy.
 */
export function connectionDefaultNote(row: ConnectionRow): string | null {
  if (row.adapter === 'agentmail') {
    return "This is a mailbox of its own, provided by SMD as the default. Your operator can work in your firm's own email system instead once that connection is authorized."
  }
  return null
}

/**
 * One-line explanation of what connection care means for THIS row, shown at
 * the connector. Honest about the trade (ADR 0042 §boundaries) AND about who
 * can actually reconnect it (Captain, 2026-07-15): an authorization_code
 * connection can only be re-established by the firm approving a fresh
 * authorization — SMD sends the link, the firm clicks Allow. Claiming SMD
 * "can re-establish it for you" is only true where SMD holds the credential.
 */
export function connectionCareNote(row: ConnectionRow): string {
  if (row.custody === 'self_held') {
    return 'Only you can re-establish this connection. SMD cannot read or rotate the secret.'
  }
  if (row.authMode === 'authorization_code') {
    return 'SMD monitors this connection. If it ever needs re-authorizing, SMD sends a fresh authorization link and someone at your firm approves it.'
  }
  return 'SMD monitors this connection and can re-establish it for you.'
}
