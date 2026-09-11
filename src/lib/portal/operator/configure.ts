/**
 * Configure surface model (client-portal §5.6). Read-side parsers for the
 * scope / hours sub-domains. Pure — no I/O.
 *
 * The governance-floor rows and ceiling labels that once lived here were
 * removed 2026-09-09: no facet rendered them, and ADR 0073 had already
 * emptied every vertical floor. Governance is still the ACTION-CLASS model
 * (ADR 0025); the runtime treats an unconfigured action class as fail-closed
 * (refused), never "drafts for review" (ADR 0035 landmine).
 */

import {
  OUTBOUND_ROSTER_CLASSES,
  type OutboundRosterClass,
  type OutboundRosterEntry,
  type Scope,
  type BusinessHours,
} from '../../operator/customer-yaml/types'
import { isRecord } from '../../api/helpers'

function strArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/** Parse the projected `scope` blob into a Scope, or null when absent/malformed. */
export function parseScope(raw: unknown): Scope | null {
  if (!isRecord(raw)) return null
  return {
    email_folders_visible: strArray(raw['email_folders_visible']),
    email_folders_blind: strArray(raw['email_folders_blind']),
    email_keyword_blocks: strArray(raw['email_keyword_blocks']),
    domain_blocks: strArray(raw['domain_blocks']),
    matter_blocks: strArray(raw['matter_blocks']),
    inbound_allow_from: strArray(raw['inbound_allow_from']),
    outbound_roster: parseOutboundRoster(raw['outbound_roster']),
    admins: strArray(raw['admins']),
    rule_requests_to: strArray(raw['rule_requests_to']),
    ops_reply_from: strArray(raw['ops_reply_from']),
  }
}

/**
 * Read-side parser for the projected `outbound_roster` (ADR 0075). Lenient:
 * keeps only well-formed entries (a non-empty address string + a class in the
 * closed vocabulary), so a hand-edited or partial projection renders its valid
 * rows and silently drops malformed ones. The authoring-time validator is the
 * strict gate; this is display-only.
 */
function parseOutboundRoster(raw: unknown): OutboundRosterEntry[] {
  if (!Array.isArray(raw)) return []
  const out: OutboundRosterEntry[] = []
  for (const e of raw) {
    if (!isRecord(e)) continue
    const address = e['address']
    const cls = e['class']
    if (typeof address !== 'string' || address.length === 0) continue
    if (typeof cls !== 'string' || !(OUTBOUND_ROSTER_CLASSES as readonly string[]).includes(cls)) {
      continue
    }
    const entry: OutboundRosterEntry = { address, class: cls as OutboundRosterClass }
    const note = e['note']
    if (typeof note === 'string') entry.note = note
    out.push(entry)
  }
  return out
}

/** Parse the projected `business_hours` blob, or null when absent/malformed. */
export function parseBusinessHours(raw: unknown): BusinessHours | null {
  if (!isRecord(raw)) return null
  const timezone = typeof raw['timezone'] === 'string' ? raw['timezone'] : null
  const start = typeof raw['start'] === 'string' ? raw['start'] : null
  const end = typeof raw['end'] === 'string' ? raw['end'] : null
  if (timezone === null || start === null || end === null) return null
  return { timezone, days: strArray(raw['days']), start, end }
}
