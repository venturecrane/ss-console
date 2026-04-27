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
 * The dedupe path is the primary entry point from the Resend webhook —
 * Svix delivers the same envelope id on retry and we must not double-count.
 * Synthetic 'sent' rows from the send wrapper omit `provider_event_id` and
 * always insert.
 */
export async function recordEvent(
  db: D1Database,
  input: RecordEventInput
): Promise<RecordEventResult> {
  // Dedupe path: if we already have a row for this provider_event_id,
  // return that id without inserting.
  if (input.provider_event_id) {
    const existing = await db
      .prepare('SELECT id FROM outreach_events WHERE provider_event_id = ? LIMIT 1')
      .bind(input.provider_event_id)
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

/**
 * Return all events for an entity, newest first. Used by the admin entity
 * detail page to render a per-prospect outreach timeline.
 */
export async function listEventsByEntity(
  db: D1Database,
  entityId: string
): Promise<OutreachEvent[]> {
  const result = await db
    .prepare(
      `SELECT * FROM outreach_events
       WHERE entity_id = ?
       ORDER BY created_at DESC`
    )
    .bind(entityId)
    .all<OutreachEvent>()
  return result.results ?? []
}
