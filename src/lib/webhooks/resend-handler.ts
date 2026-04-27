/**
 * Resend webhook event handler.
 *
 * Maps Resend event types to internal `outreach_events` rows and resolves
 * the originating `entity_id` from the synthetic 'sent' row recorded by
 * the send wrapper (since Resend does not echo arbitrary metadata in
 * webhook payloads).
 *
 * Refs:
 *   https://resend.com/docs/dashboard/webhooks/event-types
 *   https://resend.com/docs/api-reference/webhooks/introduction
 */

import { recordEvent, findSentByMessageId, type OutreachEventType } from '../db/outreach-events'

/**
 * Subset of Resend webhook payload that we depend on. Resend includes more
 * fields (created_at, tags, etc.) but we keep our type narrow so a vendor
 * payload reshape doesn't silently break the handler at compile time.
 */
export interface ResendWebhookPayload {
  type: string
  data?: {
    email_id?: string
    /** Recipient list — Resend sends this as an array of strings. */
    to?: string[]
    from?: string
    subject?: string
    [key: string]: unknown
  }
  [key: string]: unknown
}

/**
 * Map Resend's event type vocabulary to our internal event_type taxonomy.
 * Returns null for events we do not record (delivery_delayed, etc.) — they
 * are acknowledged at the route level but produce no row.
 *
 * Resend event names per
 * https://resend.com/docs/dashboard/webhooks/event-types:
 *   email.sent           → 'sent'
 *   email.delivered      → 'sent' (delivered to inbox; we collapse to 'sent'
 *                                  since funnel math wants "did it leave")
 *   email.opened         → 'open'
 *   email.clicked        → 'click'
 *   email.bounced        → 'bounce'
 *   email.complained     → 'bounce' (spam complaint — same lifecycle bucket
 *                                    for funnel math; payload preserves the
 *                                    exact provider type for forensics)
 *   email.delivery_delayed → null (transient, no funnel signal)
 *   contact.* / domain.* → null (not message-scoped)
 */
export function mapResendEventType(resendType: string): OutreachEventType | null {
  switch (resendType) {
    case 'email.sent':
    case 'email.delivered':
      return 'sent'
    case 'email.opened':
      return 'open'
    case 'email.clicked':
      return 'click'
    case 'email.bounced':
    case 'email.complained':
      return 'bounce'
    default:
      return null
  }
}

export interface HandleResendEventInput {
  /** Svix message id from `svix-id` header. Used as our dedupe key. */
  providerEventId: string
  /** Parsed payload from the verified webhook body. */
  payload: ResendWebhookPayload
  /** Fallback org id when we can't resolve from a 'sent' row. */
  fallbackOrgId: string
}

export interface HandleResendEventResult {
  /** True when a row was inserted; false when ignored or deduped. */
  recorded: boolean
  /** Reason when recorded=false. */
  reason?: 'unhandled_event_type' | 'no_message_id' | 'deduped'
  /** Internal event_type written when recorded=true. */
  eventType?: OutreachEventType
  /** Resolved entity_id, if known. */
  entityId?: string | null
}

/**
 * Process a single verified Resend webhook event:
 *   1. Map provider event type → internal event_type, or short-circuit.
 *   2. Resolve entity_id by joining message_id → original 'sent' row.
 *   3. Insert (or dedupe via provider_event_id) into outreach_events.
 *
 * The 'sent' row recorded here (when type is email.sent / email.delivered)
 * is distinct from the synthetic 'sent' row written by the send wrapper:
 *   - send-wrapper row is written immediately after Resend returns 200,
 *     carries entity_id, has a NULL provider_event_id.
 *   - webhook 'sent'/'delivered' row is written when Resend's MTA reports
 *     the email handed off, carries the Svix provider_event_id.
 *
 * Both are intentional — the send-wrapper row anchors entity attribution
 * for everything downstream; the webhook row gives us the provider's
 * authoritative timeline.
 */
export async function handleResendEvent(
  db: D1Database,
  input: HandleResendEventInput
): Promise<HandleResendEventResult> {
  const internalType = mapResendEventType(input.payload.type)
  if (!internalType) {
    return { recorded: false, reason: 'unhandled_event_type' }
  }

  const messageId = input.payload.data?.email_id ?? null

  // Without a message_id we cannot link the event to anything actionable.
  // 'sent' webhook events without an email_id would be a Resend bug, but
  // we still ack and skip rather than insert an orphan row.
  if (!messageId) {
    return { recorded: false, reason: 'no_message_id' }
  }

  // Resolve attribution by joining back to the synthetic 'sent' row from
  // the send wrapper. If no such row exists (e.g. transactional email
  // from outside the outreach path), entity_id stays null and the row
  // is recorded as org-scoped only.
  const sentRow = await findSentByMessageId(db, messageId)
  const orgId = sentRow?.org_id ?? input.fallbackOrgId
  const entityId = sentRow?.entity_id ?? null

  const result = await recordEvent(db, {
    org_id: orgId,
    entity_id: entityId,
    event_type: internalType,
    channel: 'email',
    message_id: messageId,
    provider_event_id: input.providerEventId,
    payload: input.payload,
  })

  if (!result.inserted) {
    return { recorded: false, reason: 'deduped', eventType: internalType, entityId }
  }

  return { recorded: true, eventType: internalType, entityId }
}
