-- Outreach attribution telemetry. Captures per-message lifecycle events
-- (sent / open / click / bounce / reply) so we can answer the most basic
-- ROI question for lead-gen: which signals turn into engagements, and at
-- what funnel-stage drop-off rate.
--
-- Without this table, every outreach decision is guesswork. See
-- docs/strategy/lead-gen-strategy-2026-04-25.md and issue #587.
--
-- Append-only. One row per provider event. Do NOT mutate rows after insert.
-- Funnel math is computed by aggregating over (entity_id, message_id, event_type).
--
-- Field semantics
-- ---------------
-- entity_id         FK to entities.id. The originating signal that the message
--                   was sent for. Threaded through the send path so every
--                   downstream event (open, click, bounce, reply) links back
--                   to a specific entity in the lead-gen pipeline.
--
-- event_type        Lifecycle event, taken from the Resend webhook payload's
--                   `type` field, mapped to a stable internal vocabulary so
--                   the schema does not lock to one provider.
--                   - sent     — Resend `email.sent` (handed to upstream MTA)
--                   - open     — Resend `email.opened` (tracking pixel hit)
--                   - click    — Resend `email.clicked` (tracked link clicked)
--                   - bounce   — Resend `email.bounced`
--                   - reply    — synthesized when an inbound message arrives
--                                that we attribute to a sent message_id.
--                                (Reply parsing is issue #590 — schema is
--                                ready for it; events insert as the parser
--                                lands.)
--
-- channel           'email' for now. Reserved for SMS, LinkedIn, etc., once
--                   those send paths exist.
--
-- message_id        Provider-issued unique identifier. For Resend, this is
--                   the `id` returned by POST /emails (UUID). Webhook events
--                   carry this in `data.email_id`. Used to join sent → opens
--                   → clicks → replies for the same message.
--
-- provider_event_id Resend webhook envelope `id` (Svix message id). Stored
--                   so we can dedupe retries — Svix retries deliver the same
--                   id, and we never want to double-count an open.
--
-- payload           Raw JSON body of the provider event. Kept verbose so
--                   future analytics (e.g. reading user-agent or geo from
--                   open events) does not require a schema migration.
--                   May be NULL for synthetic 'sent' rows recorded by the
--                   send wrapper before the webhook lands.
--
-- created_at        Wall-clock time the row was inserted, not the provider
--                   event time. Provider event time (when distinct) lives
--                   inside `payload`. We index on created_at because that
--                   is what backfill, dedupe, and monitoring queries use.
--
-- Indexes
-- -------
-- (entity_id, created_at DESC)         — entity timeline ("show me everything
--                                        that happened for this prospect").
-- (message_id, event_type)             — per-message rollup ("did this email
--                                        bounce? was it opened?").
-- (provider_event_id) UNIQUE           — webhook idempotency. INSERT OR IGNORE
--                                        on the webhook handler is the dedupe
--                                        primitive.

CREATE TABLE outreach_events (
  id                 TEXT PRIMARY KEY,
  org_id             TEXT NOT NULL REFERENCES organizations(id),
  entity_id          TEXT REFERENCES entities(id),

  event_type         TEXT NOT NULL CHECK (event_type IN (
    'sent', 'open', 'click', 'bounce', 'reply'
  )),
  channel            TEXT NOT NULL DEFAULT 'email' CHECK (channel IN (
    'email'
  )),

  message_id         TEXT,
  provider_event_id  TEXT,
  payload            TEXT,

  created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_outreach_events_entity
  ON outreach_events(entity_id, created_at DESC);

CREATE INDEX idx_outreach_events_message
  ON outreach_events(message_id, event_type);

CREATE UNIQUE INDEX idx_outreach_events_provider_event
  ON outreach_events(provider_event_id)
  WHERE provider_event_id IS NOT NULL;
