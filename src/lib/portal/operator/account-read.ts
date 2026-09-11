/**
 * Account read path (client-portal §5.9). Subscription state, escalation
 * contacts, and the entry to notification preferences.
 *
 * Two read concerns, two authority domains:
 *   - Subscription (provisioning / active / paused) is the `provisioning`
 *     domain — SMD-only and non-switchable. The client watches it; it is never
 *     operable, and the provisioning/paused states render as honest status
 *     surfaces with no fabricated controls (§5.9).
 *   - Escalation contacts (who the operator alerts on a red flag or a failure)
 *     are authored config — the `configuration` domain. Read + Request at
 *     launch, operable once SMD flips the switch.
 *
 * Notification preferences are self-service per-user and keep their existing
 * page; Account links to them rather than re-implementing.
 *
 * Defensive throughout: a missing subscription row is a real state (not yet
 * provisioned), and escalation parses from an `unknown` projection blob —
 * non-string recipients are dropped, never a fabricated contact.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { isRecord } from '../../api/helpers'
import { getProductSubscription } from '../product-access'
import { getCustomerConfig } from '../customer-config'

const PRODUCT_SLUG = 'operator'

export type SubscriptionStatus = 'provisioning' | 'active' | 'paused' | 'unknown'

export interface SubscriptionView {
  /** Present only when a subscription row exists. */
  status: SubscriptionStatus
  startedAt: string | null
  endedAt: string | null
}

export interface CaseAlertRoutingView {
  mode: 'central' | 'matter_staff'
  fallbackRecipients: string[]
}

export interface EscalationView {
  redFlagRecipients: string[]
  failureRecipients: string[]
  ackWindowMinutes: number | null
  /** Null = unauthored = central (today's behavior). See #2004. */
  caseAlertRouting: CaseAlertRoutingView | null
}

export interface AccountState {
  /** Null when no subscription row exists yet (pre-provisioning). */
  subscription: SubscriptionView | null
  escalation: EscalationView
}

const KNOWN_STATUSES: ReadonlySet<string> = new Set(['provisioning', 'active', 'paused'])

function normalizeStatus(raw: string): SubscriptionStatus {
  return KNOWN_STATUSES.has(raw) ? (raw as SubscriptionStatus) : 'unknown'
}

export async function loadAccountState(db: D1Database, entityId: string): Promise<AccountState> {
  const sub = await getProductSubscription(db, entityId, PRODUCT_SLUG)
  const config = await getCustomerConfig(db, entityId)
  return {
    subscription: sub
      ? { status: normalizeStatus(sub.status), startedAt: sub.started_at, endedAt: sub.ended_at }
      : null,
    escalation: parseEscalation(config?.escalation),
  }
}

/** Keep only non-empty string entries — never a fabricated or blank contact. */
function stringList(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return v.filter((x): x is string => typeof x === 'string' && x.trim().length > 0)
}

/**
 * Parse the projected escalation blob (`unknown`) into a display shape. Total:
 * any malformed field resolves to an empty list / null, never throws.
 */
export function parseEscalation(raw: unknown): EscalationView {
  if (!isRecord(raw)) {
    return {
      redFlagRecipients: [],
      failureRecipients: [],
      ackWindowMinutes: null,
      caseAlertRouting: null,
    }
  }
  const ack = raw['acknowledgement_window_minutes']
  return {
    redFlagRecipients: stringList(raw['red_flag_recipients']),
    failureRecipients: stringList(raw['failure_recipients']),
    ackWindowMinutes: typeof ack === 'number' && Number.isFinite(ack) && ack > 0 ? ack : null,
    caseAlertRouting: parseCaseAlertRouting(raw['case_alert_routing']),
  }
}

/**
 * Total parse of the projected case-alert routing block (#2004). null =
 * unauthored = central (today's behavior); a malformed blob also resolves to
 * null rather than throwing, matching the rest of this reader.
 */
function parseCaseAlertRouting(raw: unknown): CaseAlertRoutingView | null {
  if (!isRecord(raw)) return null
  const mode = raw['mode']
  if (mode !== 'central' && mode !== 'matter_staff') return null
  return { mode, fallbackRecipients: stringList(raw['fallback_recipients']) }
}

/** One-line human label for a subscription status. Client language, not
 *  infrastructure language — "Being set up", never "Provisioning" (admin
 *  surfaces keep their own internal vocabulary). */
export function subscriptionStatusLabel(status: SubscriptionStatus): string {
  switch (status) {
    case 'provisioning':
      return 'Being set up'
    case 'active':
      return 'Active'
    case 'paused':
      return 'Paused'
    default:
      return 'Unknown'
  }
}

/** Honest one-sentence prose for each subscription state. */
export function subscriptionStatusProse(status: SubscriptionStatus): string {
  switch (status) {
    case 'provisioning':
      return 'Your operator is being set up. We will let you know the moment it is ready.'
    case 'active':
      return 'Your operator is running.'
    case 'paused':
      return 'Your operator is paused. Contact us to resume it.'
    default:
      return 'We could not read your subscription state. Contact us if this persists.'
  }
}
