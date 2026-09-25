/**
 * checkout.session.completed handling for the Hosted Agent SKU (ADR 0067).
 *
 * For this SKU, checkout IS the front half of provisioning: the webhook
 * creates the commercial substrate (entity + subscription row at
 * `provisioning` + principal role + intake work item) and hands the
 * concierge baton to the Captain via the admin queue and a team@ alert.
 * Activation (provisioning → active) is ALWAYS a Captain action; billing
 * events can never promote it (guard in setSubscriptionBillingStatus).
 *
 * Idempotency: the `stripe_checkout_orders` ledger is keyed by session id.
 * A replayed event that finds a processed row is acknowledged without
 * re-running the pipeline; a partial failure leaves the row `received` so
 * Stripe's retry re-enters and the entity/subscription writes are all
 * existence-guarded.
 *
 * Same two-phase discipline as the sibling handlers: Phase 1 D1 writes
 * decide the response (500 → Stripe retries); Phase 2 emails are
 * best-effort.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { failedResponse } from '../api/failures'
import { captureError } from '../observability/sentry'
import { ok } from './stripe-subscription-shared'
import { ORG_ID } from '../constants'
import { EMAIL_IDENTITY_PREDICATE, normalizeEmail } from '../identity/email'
import { createEntity } from '../db/entities'
import { createHostedAgentIntake } from '../db/hosted-agent-intake'
import { HOSTED_AGENT_PRODUCT_SLUG } from '../portal/hosted-agent-access'
import { sendEmail } from '../email/resend'
import {
  hostedAgentOrderNotificationEmailHtml,
  hostedAgentWelcomeEmailHtml,
} from '../email/hosted-agent-templates'

const ALERT_EMAIL = 'team@smd.services'

const AREA = 'webhook/stripe/hosted-agent-checkout'

/** The session-payload fields the pipeline consumes. */
export interface HostedAgentCheckoutSessionPayload {
  id: string
  client_reference_id: string | null
  customer: string | null
  subscription: string | null
  amount_total: number | null
  customer_details: { email: string | null; name: string | null } | null
  metadata: Record<string, string>
  /** Present when a discount applied — founding-seat detection. */
  total_details?: { amount_discount?: number } | null
}

interface LocalUserRow {
  id: string
  email: string
  name: string | null
  entity_id: string | null
}

async function resolveBuyer(
  db: D1Database,
  payload: HostedAgentCheckoutSessionPayload
): Promise<LocalUserRow | null> {
  if (payload.client_reference_id) {
    const byClerk = await db
      .prepare('SELECT id, email, name, entity_id FROM users WHERE clerk_user_id = ?')
      .bind(payload.client_reference_id)
      .first<LocalUserRow>()
    if (byClerk) return byClerk
  }
  const email = payload.customer_details?.email
  if (email) {
    const byEmail = await db
      .prepare(`SELECT id, email, name, entity_id FROM users WHERE ${EMAIL_IDENTITY_PREDICATE}`)
      .bind(normalizeEmail(email))
      .first<LocalUserRow>()
    if (byEmail) return byEmail
  }
  return null
}

async function ensureEntityForBuyer(db: D1Database, user: LocalUserRow): Promise<string> {
  if (user.entity_id) return user.entity_id
  const displayName = user.name?.trim() || user.email.split('@')[0]
  const entity = await createEntity(db, ORG_ID, {
    name: displayName,
    stage: 'engaged',
    source_pipeline: 'hosted-agent-self-serve',
  })
  await db
    .prepare("UPDATE users SET entity_id = ?, updated_at = datetime('now') WHERE id = ?")
    .bind(entity.id, user.id)
    .run()
  return entity.id
}

/** Record the order in the idempotency ledger. Returns true when the event
 * was already fully processed (caller acks without re-running). */
async function recordOrder(
  db: D1Database,
  payload: HostedAgentCheckoutSessionPayload,
  plan: 'founding' | 'standard'
): Promise<boolean> {
  const existingOrder = await db
    .prepare('SELECT status FROM stripe_checkout_orders WHERE session_id = ?')
    .bind(payload.id)
    .first<{ status: string }>()
  if (existingOrder?.status === 'processed') return true
  if (!existingOrder) {
    await db
      .prepare(
        `INSERT OR IGNORE INTO stripe_checkout_orders
           (session_id, clerk_user_id, email, stripe_customer_id, stripe_subscription_id,
            product_slug, plan, amount_total)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        payload.id,
        payload.client_reference_id,
        payload.customer_details?.email ?? null,
        payload.customer,
        payload.subscription,
        HOSTED_AGENT_PRODUCT_SLUG,
        plan,
        payload.amount_total
      )
      .run()
  }
  return false
}

async function setOrderStatus(
  db: D1Database,
  sessionId: string,
  status: 'processed' | 'failed'
): Promise<void> {
  await db
    .prepare(
      "UPDATE stripe_checkout_orders SET status = ?, updated_at = datetime('now') WHERE session_id = ?"
    )
    .bind(status, sessionId)
    .run()
}

/** Find or create the (entity, hosted-agent) subscription row. Checkout is
 * this SKU's provisioning grant (ADR 0067 §7). */
async function ensureSubscription(
  db: D1Database,
  entityId: string,
  payload: HostedAgentCheckoutSessionPayload,
  plan: 'founding' | 'standard'
): Promise<string> {
  const existingSub = await db
    .prepare(
      `SELECT id FROM subscriptions WHERE entity_id = ? AND product_slug = ?
         AND status IN ('provisioning', 'active', 'paused')`
    )
    .bind(entityId, HOSTED_AGENT_PRODUCT_SLUG)
    .first<{ id: string }>()
  if (existingSub) return existingSub.id

  const subscriptionId = crypto.randomUUID()
  const settings = JSON.stringify({
    plan,
    stripe_customer_id: payload.customer,
    checkout_session_id: payload.id,
  })
  await db
    .prepare(
      `INSERT INTO subscriptions
         (id, org_id, entity_id, product_slug, status, started_at, settings_json,
          stripe_subscription_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'provisioning', datetime('now'), ?, ?, datetime('now'), datetime('now'))`
    )
    .bind(
      subscriptionId,
      ORG_ID,
      entityId,
      HOSTED_AGENT_PRODUCT_SLUG,
      settings,
      payload.subscription
    )
    .run()
  return subscriptionId
}

async function ensurePrincipalRole(
  db: D1Database,
  userId: string,
  entityId: string
): Promise<void> {
  const existingRole = await db
    .prepare(
      `SELECT id FROM product_roles WHERE user_id = ? AND entity_id = ? AND product_slug = ?
         AND revoked_at IS NULL`
    )
    .bind(userId, entityId, HOSTED_AGENT_PRODUCT_SLUG)
    .first<{ id: string }>()
  if (existingRole) return
  await db
    .prepare(
      `INSERT INTO product_roles
         (id, org_id, user_id, entity_id, product_slug, role, granted_by, granted_at)
       VALUES (?, ?, ?, ?, ?, 'principal', NULL, datetime('now'))`
    )
    .bind(crypto.randomUUID(), ORG_ID, userId, entityId, HOSTED_AGENT_PRODUCT_SLUG)
    .run()
}

async function ensureIntakeWorkItem(
  db: D1Database,
  entityId: string,
  subscriptionId: string
): Promise<void> {
  const existingIntake = await db
    .prepare('SELECT id FROM hosted_agent_intake WHERE subscription_id = ?')
    .bind(subscriptionId)
    .first<{ id: string }>()
  if (!existingIntake) {
    await createHostedAgentIntake(db, { orgId: ORG_ID, entityId, subscriptionId })
  }
}

/**
 * The concierge pipeline. Existence-guarded at every step so Stripe
 * retries are safe re-entries.
 */
export async function handleHostedAgentCheckoutCompleted(
  db: D1Database,
  resendApiKey: string | undefined,
  portalUrl: string,
  adminQueueUrl: string,
  payload: HostedAgentCheckoutSessionPayload
): Promise<Response> {
  // Only this SKU's sessions belong here; ack anything else honestly.
  if (payload.metadata['product_slug'] !== HOSTED_AGENT_PRODUCT_SLUG) return ok()

  const plan: 'founding' | 'standard' =
    (payload.total_details?.amount_discount ?? 0) > 0 ? 'founding' : 'standard'

  try {
    const alreadyProcessed = await recordOrder(db, payload, plan)
    if (alreadyProcessed) return ok()

    const user = await resolveBuyer(db, payload)
    if (!user) {
      // A paying stranger the console cannot bind (no Clerk row, no email
      // match). Never 500-loop Stripe over it: record, alert, reconcile by
      // hand.
      await setOrderStatus(db, payload.id, 'failed')
      await sendUnresolvedBuyerAlert(resendApiKey, payload)
      return ok()
    }

    const entityId = await ensureEntityForBuyer(db, user)
    const subscriptionId = await ensureSubscription(db, entityId, payload, plan)
    await ensurePrincipalRole(db, user.id, entityId)
    await ensureIntakeWorkItem(db, entityId, subscriptionId)
    await setOrderStatus(db, payload.id, 'processed')

    // --- Phase 2: emails, best-effort ---
    await sendPurchaseEmails(db, resendApiKey, portalUrl, adminQueueUrl, {
      buyerEmail: user.email,
      buyerName: user.name?.trim() || user.email,
      entityId,
      plan,
    })
    return ok()
  } catch (err) {
    return failedResponse(err, AREA) // a 500 lets Stripe retry; Sentry hears each one
  }
}

async function sendPurchaseEmails(
  db: D1Database,
  resendApiKey: string | undefined,
  portalUrl: string,
  adminQueueUrl: string,
  input: { buyerEmail: string; buyerName: string; entityId: string; plan: 'founding' | 'standard' }
): Promise<void> {
  try {
    await sendEmail(resendApiKey, {
      to: input.buyerEmail,
      subject: 'Your Hosted Agent subscription is active',
      // Deep-link the product page on the portal host, not the portal root.
      html: hostedAgentWelcomeEmailHtml(
        input.buyerName,
        `${portalUrl}/portal/products/hosted-agent`
      ),
    })
  } catch (err) {
    console.error('[hosted-agent-checkout] welcome email failed:', err)
    captureError(err, AREA)
  }
  try {
    const entity = await db
      .prepare('SELECT name FROM entities WHERE id = ?')
      .bind(input.entityId)
      .first<{ name: string }>()
    await sendEmail(resendApiKey, {
      to: ALERT_EMAIL,
      subject: `Hosted Agent purchase: ${entity?.name ?? input.entityId} (${input.plan})`,
      html: hostedAgentOrderNotificationEmailHtml({
        entityName: entity?.name ?? input.entityId,
        buyerEmail: input.buyerEmail,
        plan: input.plan,
        entityId: input.entityId,
        adminQueueUrl,
      }),
    })
  } catch (err) {
    console.error('[hosted-agent-checkout] team notification failed:', err)
    captureError(err, AREA)
  }
}

async function sendUnresolvedBuyerAlert(
  resendApiKey: string | undefined,
  payload: HostedAgentCheckoutSessionPayload
): Promise<void> {
  try {
    await sendEmail(resendApiKey, {
      to: ALERT_EMAIL,
      subject: 'Hosted Agent purchase needs manual reconciliation',
      html:
        `<p>A completed Hosted Agent checkout could not be bound to a local user.</p>` +
        `<ul><li>Checkout session: ${payload.id}</li>` +
        `<li>Buyer email: ${payload.customer_details?.email ?? 'unknown'}</li>` +
        `<li>Stripe subscription: ${payload.subscription ?? 'unknown'}</li></ul>` +
        `<p>The order row is marked failed. Bind the buyer and re-run the event from the Stripe dashboard.</p>`,
    })
  } catch (err) {
    console.error('[hosted-agent-checkout] unresolved-buyer alert failed:', err)
    captureError(err, AREA)
  }
}
