/**
 * Outreach events data access layer.
 *
 * INVARIANT: append-only. Once a row is inserted it is never updated or
 * deleted. The funnel math (sent → open → click → reply) is derived from
 * these rows, not stored on them. Mutating a row would corrupt historical
 * attribution.
 *
 * Idempotency: webhook handlers call `recordEvent` with a non-null
 * `provider_event_id`. The unique partial index on `provider_event_id`
 * lets us use INSERT OR IGNORE so Svix retries (which deliver the same
 * provider event id) collapse to a single row.
 *
 * See migrations/0028_create_outreach_events.sql for column semantics.
 */

export type OutreachEventType = 'sent' | 'open' | 'click' | 'bounce' | 'reply'

export type OutreachChannel = 'email'

export interface OutreachEvent {
  id: string
  org_id: string
  entity_id: string | null
  event_type: OutreachEventType
  channel: OutreachChannel
  message_id: string | null
  provider_event_id: string | null
  payload: string | null
  created_at: string
}

export interface RecordEventInput {
  org_id: string
  entity_id?: string | null
  event_type: OutreachEventType
  channel?: OutreachChannel
  message_id?: string | null
  provider_event_id?: string | null
  payload?: unknown
}

export interface RecordEventResult {
  /**
   * The id of the row in the database. When the event was deduped
   * (provider_event_id already existed), this is the id of the existing
   * row, not a new one.
   */
  id: string
  /**
   * True when a new row was written. False when the event was deduped
   * against an existing provider_event_id.
   */
  inserted: boolean
}

/**
 * Insert an outreach-event row, deduping on provider_event_id when present.
 *
 * Two dedup paths:
 *
 *   1. Webhook path (provider_event_id non-null): Svix delivers the same
 *      envelope id on retry. Dedup keyed on provider_event_id.
 *
 *   2. Synthetic 'sent' path (PR-2b, message_id non-null, provider_event_id
 *      null): the workflow's render-and-email step records a synthetic
 *      'sent' row keyed on the Resend message_id immediately after a
 *      successful send. Cloudflare Workflows step-result caching can
 *      replay this insert on retry, producing duplicate rows for the
 *      same Resend send — observed in production 2026-05-01 as 3 rows
 *      per scan with identical message_ids. Dedup keyed on
 *      (org_id, message_id, event_type='sent').
 *
 * Other event types (open/click/bounce/reply) without a provider_event_id
 * fall through to the regular insert. Webhook deliveries always carry one.
 */
export async function recordEvent(
  db: D1Database,
  input: RecordEventInput
): Promise<RecordEventResult> {
  // Dedup path 1: webhook envelope.
  if (input.provider_event_id) {
    const existing = await db
      .prepare('SELECT id FROM outreach_events WHERE provider_event_id = ? LIMIT 1')
      .bind(input.provider_event_id)
      .first<{ id: string }>()

    if (existing) {
      return { id: existing.id, inserted: false }
    }
  }

  // Dedup path 2: synthetic 'sent' row from the send wrapper / workflow
  // step. Keyed on (org_id, message_id) for the 'sent' event type only.
  // Other event types (open/click/etc) without provider_event_id are
  // unusual and fall through to insert; webhook deliveries always carry
  // a provider_event_id and are handled by path 1.
  if (!input.provider_event_id && input.message_id && input.event_type === 'sent') {
    const existing = await db
      .prepare(
        `SELECT id FROM outreach_events
         WHERE org_id = ? AND message_id = ? AND event_type = 'sent'
         LIMIT 1`
      )
      .bind(input.org_id, input.message_id)
      .first<{ id: string }>()

    if (existing) {
      return { id: existing.id, inserted: false }
    }
  }

  const id = crypto.randomUUID()
  const payloadJson =
    input.payload === undefined || input.payload === null
      ? null
      : typeof input.payload === 'string'
        ? input.payload
        : JSON.stringify(input.payload)

  await db
    .prepare(
      `INSERT INTO outreach_events (
        id, org_id, entity_id, event_type, channel, message_id,
        provider_event_id, payload
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      input.org_id,
      input.entity_id ?? null,
      input.event_type,
      input.channel ?? 'email',
      input.message_id ?? null,
      input.provider_event_id ?? null,
      payloadJson
    )
    .run()

  return { id, inserted: true }
}

/**
 * Look up the most recent 'sent' event for a given message_id. Used by the
 * webhook handler to recover the originating entity_id from a downstream
 * event (open / click / bounce / reply) — Resend echoes the message id in
 * `data.email_id` but does not echo our entity_id, so we re-resolve it from
 * the original send row.
 */
export async function findSentByMessageId(
  db: D1Database,
  messageId: string
): Promise<OutreachEvent | null> {
  const row = await db
    .prepare(
      `SELECT * FROM outreach_events
       WHERE message_id = ? AND event_type = 'sent'
       ORDER BY created_at ASC
       LIMIT 1`
    )
    .bind(messageId)
    .first<OutreachEvent>()
  return row ?? null
}
