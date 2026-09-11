/**
 * Stripe `customer.subscription.updated` / `.deleted` mirrored onto the local
 * subscription row, plus the alerts a client-scheduled cancellation raises.
 *
 * Split out of stripe-subscription-handler.ts on 2026-09-11 (review
 * 2026-09-10, Architecture 3); the invoice-event half stays there. Same
 * posture as before: NOTHING here touches the customer's Machine. Whether a
 * delinquent or cancelled seat gets paused or decommissioned is a Captain
 * decision under the offboarding doctrine (#1684), never a webhook side effect.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  getSubscriptionByStripeId,
  parseCancelAt,
  setSubscriptionBillingStatus,
  setSubscriptionCancelSchedule,
  type SubscriptionBillingRow,
} from '../db/subscriptions'
import { alertTeam, entityName, ok, serverError, unixToIso } from './stripe-subscription-shared'

/** The subscription-payload fields the status mirror consumes. */
export interface StripeSubscriptionEventPayload {
  id: string
  status: string
  pause_collection?: unknown
  /** True from the moment the client schedules a cancellation in the Stripe
   * Billing Portal until the period actually ends (or they reverse it). */
  cancel_at_period_end?: boolean
  /** Unix seconds the subscription will end. Stripe sets this alongside
   * `cancel_at_period_end`. */
  cancel_at?: number | null
  /** Fallback source for the end date: this Stripe API version carries
   * `current_period_end` on the subscription ITEM, not the subscription
   * (verified against the live account 2026-08-29). */
  items?: { data?: { current_period_end?: number | null }[] }
}

/**
 * The date a scheduled cancellation takes effect, or null when none is
 * scheduled. Returns `unreadable` when Stripe says a cancellation is
 * scheduled but names no date — a shape change we alert on rather than
 * guess at, since the date is what both sides plan around.
 */
function resolveCancelSchedule(
  payload: StripeSubscriptionEventPayload
): { kind: 'none' } | { kind: 'scheduled'; iso: string } | { kind: 'unreadable' } {
  if (payload.cancel_at_period_end !== true) return { kind: 'none' }
  const seconds = payload.cancel_at ?? payload.items?.data?.[0]?.current_period_end ?? null
  const iso = unixToIso(seconds ?? null)
  return iso ? { kind: 'scheduled', iso } : { kind: 'unreadable' }
}

/**
 * Mirror `customer.subscription.updated` / `.deleted` onto the local row.
 * Transitions are billing-scoped (see setSubscriptionBillingStatus): a
 * deleted subscription cancels the row; pause_collection presence maps to
 * paused/active. Unknown Stripe subscriptions are skipped honestly.
 *
 * A client-scheduled cancellation arrives here as an `updated` event that
 * changes no status (see setSubscriptionCancelSchedule) — it is mirrored to
 * settings_json and alerted on separately.
 */
export async function handleSubscriptionLifecycle(
  db: D1Database,
  eventType: 'customer.subscription.updated' | 'customer.subscription.deleted',
  payload: StripeSubscriptionEventPayload,
  resendApiKey?: string
): Promise<Response> {
  const sub = await getSubscriptionByStripeId(db, payload.id)
  if (!sub) {
    console.log(
      `[stripe-subscription] No local subscription for ${payload.id}; skipping ${eventType}`
    )
    return ok()
  }

  try {
    if (eventType === 'customer.subscription.deleted' || payload.status === 'canceled') {
      await setSubscriptionBillingStatus(db, sub.id, 'cancelled')
      if (parseCancelAt(sub.settings_json)) await setSubscriptionCancelSchedule(db, sub.id, null)
      await alertCancellationEffective(db, resendApiKey, sub)
      return ok()
    }
    if (payload.pause_collection !== null && payload.pause_collection !== undefined) {
      await setSubscriptionBillingStatus(db, sub.id, 'paused')
    } else if (payload.status === 'active' || payload.status === 'past_due') {
      // past_due keeps access: the failure alert + Captain decide, never the webhook.
      await setSubscriptionBillingStatus(db, sub.id, 'active')
    }
    await mirrorCancelSchedule(db, resendApiKey, sub, payload)
    return ok()
  } catch (err) {
    console.error('[stripe-subscription] lifecycle mirror failed:', err)
    return serverError()
  }
}

/**
 * Reconcile the row's scheduled-cancellation posture with the event, and
 * alert `team@` when it CHANGES. Stripe re-sends the whole subscription on
 * every `updated` event, so the stored value is the edge detector: without
 * it a routine price or payment-method update would re-alert a cancellation
 * scheduled weeks ago.
 */
async function mirrorCancelSchedule(
  db: D1Database,
  resendApiKey: string | undefined,
  sub: SubscriptionBillingRow,
  payload: StripeSubscriptionEventPayload
): Promise<void> {
  const known = parseCancelAt(sub.settings_json)
  const schedule = resolveCancelSchedule(payload)

  if (schedule.kind === 'unreadable') {
    // Stripe says a cancellation is scheduled but named no date. Never guess
    // one onto a client-facing surface; alert and leave the row honest.
    await alertTeam(
      resendApiKey,
      `Retainer cancellation scheduled, end date unreadable — ${await entityName(db, sub)}`,
      `<p>Stripe reports <code>cancel_at_period_end=true</code> on <code>${payload.id}</code> but carried no <code>cancel_at</code> and no item <code>current_period_end</code>.</p>` +
        `<p>The client's cancellation is REAL. The portal is not showing an end date because we could not read one — check Stripe and the payload shape.</p>`
    )
    return
  }

  const next = schedule.kind === 'scheduled' ? schedule.iso : null
  if (next === known) return // no change; nothing to write, nothing to say

  await setSubscriptionCancelSchedule(db, sub.id, next)
  const name = await entityName(db, sub)
  await (next === null
    ? alertTeam(
        resendApiKey,
        `Retainer cancellation REVERSED — ${name}`,
        `<p>${name} removed the scheduled cancellation on <code>${payload.id}</code>. Billing continues as normal.</p>`
      )
    : alertTeam(
        resendApiKey,
        `Retainer cancellation scheduled — ${name}`,
        `<p>${name} cancelled the Operator retainer from the portal.</p>` +
          `<ul><li>Service and billing continue until <strong>${next.slice(0, 10)}</strong></li>` +
          `<li>Stripe subscription: ${payload.id}</li></ul>` +
          `<p>No automatic action was taken. Offboarding (export, destruction, seat decommission) is a Captain decision under the offboarding doctrine, ss-console#1684.</p>`
      ))
}

/** The subscription ended. Alerts `team@`; offboarding stays a human act. */
async function alertCancellationEffective(
  db: D1Database,
  resendApiKey: string | undefined,
  sub: SubscriptionBillingRow
): Promise<void> {
  const name = await entityName(db, sub)
  await alertTeam(
    resendApiKey,
    `Retainer ENDED — ${name}`,
    `<p>The Operator retainer for ${name} has ended at Stripe; the local subscription row is now cancelled.</p>` +
      `<ul><li>Stripe subscription: ${sub.stripe_subscription_id ?? 'unknown'}</li></ul>` +
      `<p>The seat is still running. Offboarding under Section 9.3 — audit export, operational memory, Machine and volume destruction — is a Captain act (ss-console#1684).</p>`
  )
}
