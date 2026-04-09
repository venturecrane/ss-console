-- Booking system (Calendly replacement) — migration 0011
--
-- This migration introduces the data model for the first-party booking flow
-- replacing Calendly on /book. It does five things:
--
--   1. Rewrites the `assessments` table to expand the status CHECK constraint
--      with a new 'cancelled' value. SQLite does not support
--      ALTER TABLE … DROP CONSTRAINT, so a 12-step table rewrite is required.
--      The rewrite preserves all 15 existing columns in their current order
--      (including the additive `entity_id TEXT` column added out-of-band).
--
--   2. Adds a partial unique index on (org_id, scheduled_at) for active
--      scheduled assessments. This is the database-level last line of defense
--      against double-booking the same slot for the same org.
--
--   3. Creates `integrations` for OAuth credentials (Google Calendar today,
--      generic for future Gmail/HubSpot/etc).
--
--   4. Creates `oauth_states` for single-use OAuth state nonces (D1 instead
--      of KV so the nonce is durable and consume-once enforceable).
--
--   5. Creates `assessment_schedule` (1:1 sidecar on assessments),
--      `booking_holds` (5-minute pessimistic locks), and `availability_blocks`
--      (admin overrides — read-only in v1, schema reserved for v2 admin UI).
--
-- ROLLBACK is one-way without restoring the pre-migration export. Before
-- applying this migration to prod, snapshot the database via:
--   wrangler d1 export ss-console-db --remote --output=backup-pre-0011-$(date +%Y%m%d-%H%M%S).sql
--
-- Verification SQL is in migrations/0011_verify.sql — run it post-migration.

-- ============================================================================
-- 1. assessments table rewrite (expand CHECK constraint to add 'cancelled')
-- ============================================================================
--
-- SQLite recommended 12-step procedure adapted for D1:
--   - D1 cannot disable foreign keys via PRAGMA from migration SQL.
--   - SQLite allows DROP TABLE on a referenced table (FK is enforced on
--     INSERT/UPDATE, not DROP). The dangling FK from quotes(assessment_id)
--     is transient and resolved when the new assessments table is renamed
--     into place with the same name.
--   - quotes(assessment_id) FK rebinds by table name automatically.

CREATE TABLE assessments_new (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  scheduled_at    TEXT,
  completed_at    TEXT,
  duration_minutes INTEGER,
  transcript_path TEXT,
  extraction      TEXT,
  problems        TEXT,
  disqualifiers   TEXT,
  champion_name   TEXT,
  champion_role   TEXT,
  status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                    'scheduled', 'completed', 'disqualified', 'converted', 'cancelled'
                  )),
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  entity_id       TEXT
);

INSERT INTO assessments_new (
  id, org_id, scheduled_at, completed_at, duration_minutes,
  transcript_path, extraction, problems, disqualifiers,
  champion_name, champion_role, status, notes, created_at, entity_id
)
SELECT
  id, org_id, scheduled_at, completed_at, duration_minutes,
  transcript_path, extraction, problems, disqualifiers,
  champion_name, champion_role, status, notes, created_at, entity_id
FROM assessments;

DROP TABLE assessments;
ALTER TABLE assessments_new RENAME TO assessments;

-- Recreate the only existing index on assessments (verified via prod query).
CREATE INDEX idx_assessments_org_status ON assessments(org_id, status);


-- ============================================================================
-- 2. Unique partial index — last line of defense against double-booking
-- ============================================================================
--
-- Includes org_id so a future second org doesn't turn this into a global lock.
-- NULL scheduled_at values don't conflict (SQLite unique semantics permit
-- multiple NULLs), so cancelled/completed assessments at the same time don't
-- block rebooks.

CREATE UNIQUE INDEX uniq_assessments_scheduled_at_active
  ON assessments(org_id, scheduled_at)
  WHERE scheduled_at IS NOT NULL AND status = 'scheduled';


-- ============================================================================
-- 3. integrations — generic OAuth credential store
-- ============================================================================
--
-- Stores third-party service credentials (Google Calendar in v1, generic
-- shape for future providers). Refresh token is encrypted at rest with
-- BOOKING_ENCRYPTION_KEY (Web Crypto AES-GCM, format: base64(iv):base64(ct)).
-- UNIQUE(org_id, provider, account_email) supports multiple accounts per
-- provider per org if we ever need that.

CREATE TABLE integrations (
  id                       TEXT PRIMARY KEY,
  org_id                   TEXT NOT NULL REFERENCES organizations(id),
  provider                 TEXT NOT NULL CHECK (provider IN ('google_calendar')),
  account_email            TEXT NOT NULL,
  account_id               TEXT,                                 -- Google sub claim
  calendar_id              TEXT NOT NULL DEFAULT 'primary',
  scopes                   TEXT NOT NULL,                        -- space-delimited
  refresh_token_ciphertext TEXT NOT NULL,                        -- AES-GCM
  access_token             TEXT,                                 -- cached access token
  access_expires_at        TEXT,                                 -- ISO 8601
  status                   TEXT NOT NULL DEFAULT 'active' CHECK (status IN (
                             'active', 'revoked', 'error'
                           )),
  last_error               TEXT,
  created_at               TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at               TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(org_id, provider, account_email)
);

CREATE INDEX idx_integrations_org_provider
  ON integrations(org_id, provider, status);


-- ============================================================================
-- 4. oauth_states — single-use OAuth state nonces (D1, not KV)
-- ============================================================================
--
-- Inserted when an admin clicks "Connect Google Calendar". Consumed exactly
-- once by the OAuth callback. Expires after 5 minutes. D1 (rather than KV)
-- guarantees consume-once semantics atomically.

CREATE TABLE oauth_states (
  state          TEXT PRIMARY KEY,
  org_id         TEXT NOT NULL REFERENCES organizations(id),
  provider       TEXT NOT NULL,
  initiated_by   TEXT NOT NULL,                                  -- admin user id
  expires_at     TEXT NOT NULL,                                  -- now + 5 minutes
  consumed_at    TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_oauth_states_expires ON oauth_states(expires_at);


-- ============================================================================
-- 5. assessment_schedule — 1:1 booking sidecar on assessments
-- ============================================================================
--
-- Booking-specific metadata that doesn't belong on the assessments table.
-- 1:1 with assessments via UNIQUE(assessment_id) + ON DELETE CASCADE.
-- Contains:
--   - Slot times (denormalized from assessments.scheduled_at for convenience)
--   - Denormalized guest_name/guest_email for fast manage-page hydration
--     without joining entities + contacts
--   - Google Calendar linkage (event id, link, Meet URL, sync state)
--   - Hashed manage token (raw token returned to guest only once, never stored)
--   - Cancellation + reschedule tracking
--   - Reserved column for v1.1 reminders (reminder_sent_at)

CREATE TABLE assessment_schedule (
  id                       TEXT PRIMARY KEY,
  assessment_id            TEXT NOT NULL UNIQUE REFERENCES assessments(id) ON DELETE CASCADE,
  org_id                   TEXT NOT NULL REFERENCES organizations(id),

  -- Slot (UTC ISO 8601)
  slot_start_utc           TEXT NOT NULL,
  slot_end_utc             TEXT NOT NULL,
  duration_minutes         INTEGER NOT NULL DEFAULT 30,
  timezone                 TEXT NOT NULL DEFAULT 'America/Phoenix',
  guest_timezone           TEXT,

  -- Denormalized guest identity
  guest_name               TEXT NOT NULL,
  guest_email              TEXT NOT NULL,

  -- Google Calendar linkage
  google_event_id          TEXT,
  google_event_link        TEXT,
  google_meet_url          TEXT,
  google_sync_state        TEXT NOT NULL DEFAULT 'pending' CHECK (google_sync_state IN (
                             'pending', 'synced', 'error', 'cancelled'
                           )),
  google_last_error        TEXT,

  -- Manage token (SHA-256 hex hash; raw token only ever returned to guest)
  manage_token_hash        TEXT NOT NULL UNIQUE,
  manage_token_expires_at  TEXT,                                 -- 48h after slot_end_utc

  -- Cancellation + reschedule tracking
  cancelled_at             TEXT,
  cancelled_reason         TEXT,
  cancelled_by             TEXT CHECK (cancelled_by IN ('guest', 'admin', 'system')),
  reschedule_count         INTEGER NOT NULL DEFAULT 0,
  previous_slot_utc        TEXT,

  -- Reminders (reserved for v1.1; nullable, untouched in v1)
  reminder_sent_at         TEXT,

  created_at               TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_assessment_schedule_slot
  ON assessment_schedule(org_id, slot_start_utc);
CREATE INDEX idx_assessment_schedule_manage_hash
  ON assessment_schedule(manage_token_hash);
CREATE INDEX idx_assessment_schedule_google_event
  ON assessment_schedule(google_event_id) WHERE google_event_id IS NOT NULL;


-- ============================================================================
-- 6. booking_holds — short-lived pessimistic slot locks
-- ============================================================================
--
-- Acquired at the start of POST /api/booking/reserve via an upsert-when-expired
-- pattern (see src/lib/booking/holds.ts). UNIQUE(org_id, slot_start_utc)
-- enforces single-acquisition under concurrent reserves. Cleaned daily by the
-- workers/booking-cleanup cron.

CREATE TABLE booking_holds (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  slot_start_utc  TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  guest_email     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(org_id, slot_start_utc)
);

CREATE INDEX idx_booking_holds_expires ON booking_holds(expires_at);


-- ============================================================================
-- 7. availability_blocks — manual admin overrides (v2 admin UI; read-only in v1)
-- ============================================================================
--
-- Empty in v1. Schema reserved so the v2 admin UI for blocking time can ship
-- without another migration. The availability engine reads from this table on
-- every slot computation, unioned with Google freebusy.

CREATE TABLE availability_blocks (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  start_utc       TEXT NOT NULL,
  end_utc         TEXT NOT NULL,
  reason          TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_availability_blocks_range
  ON availability_blocks(org_id, start_utc, end_utc);
