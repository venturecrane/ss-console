/**
 * Shared pieces of the Stripe subscription webhook family: the two response
 * shapes, the alert address and sender, the entity name for alert copy, and
 * the unix-to-ISO helper. Imported by stripe-subscription-handler.ts (invoice
 * events), stripe-subscription-lifecycle.ts (subscription events), and the
 * legacy and checkout handlers (alerts only).
 *
 * Split out on 2026-09-11 (review 2026-09-10, Architecture 3).
 */

import type { D1Database } from '@cloudflare/workers-types'
import { sendEmail } from '../email/resend'
import type { SubscriptionBillingRow } from '../db/subscriptions'

/** SMD operational-alert address (CLAUDE.md Contact Addresses). */
export const ALERT_EMAIL = 'team@smd.services'

export function ok(): Response {
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function serverError(): Response {
  return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
    status: 500,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function unixToIso(unix: number | null): string | null {
  return unix === null ? null : new Date(unix * 1000).toISOString()
}

/** Entity display name for alert copy; falls back to the id, never invents. */
export async function entityName(db: D1Database, sub: SubscriptionBillingRow): Promise<string> {
  try {
    const entity = await db
      .prepare('SELECT name FROM entities WHERE id = ? AND org_id = ?')
      .bind(sub.entity_id, sub.org_id)
      .first<{ name: string }>()
    return entity?.name ?? sub.entity_id
  } catch {
    return sub.entity_id
  }
}

/** Operational alert to team@. Best-effort: never turns a mirrored billing
 * event into a webhook failure Stripe will retry. Shared with the checkout
 * handler (its failed-first-payment path). */
export async function alertTeam(
  resendApiKey: string | undefined,
  subject: string,
  html: string
): Promise<void> {
  try {
    await sendEmail(resendApiKey, { to: ALERT_EMAIL, subject, html })
  } catch (err) {
    console.error('[stripe-subscription] alert email failed:', subject, err)
  }
}
