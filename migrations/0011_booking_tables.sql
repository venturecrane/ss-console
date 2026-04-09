-- Booking system (Calendly replacement) — migration 0011
--
-- This migration introduces the data model for the first-party booking flow
-- replacing Calendly on /book. It does five things:
--
--   1. Rewrites the `assessments` table to expand the status CHECK constraint
--      with a new 'cancelled' value. D1 enforces foreign keys on DROP TABLE,
--      so this requires a coordinated rewrite of the entire FK chain rooted at
--      assessments (9 tables total). Data is backed up, tables are dropped
--      leaf-first, recreated root-first, and data is restored.
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
-- D1 enforces FK constraints on DROP TABLE, so we cannot drop assessments
-- while quotes(assessment_id) references it. The full FK chain is:
--
--   assessments <- quotes <- engagements <- engagement_contacts
--                                        <- milestones
--                                        <- time_entries
--                                        <- parking_lot (engagement_id)
--                                        <- invoices (engagement_id)
--                                        <- follow_ups (engagement_id)
--                  quotes <- parking_lot (follow_on_quote_id)
--                  quotes <- follow_ups (quote_id)
--
-- Strategy: backup all 9 tables into constraint-free _bak tables, drop
-- leaf-first, recreate root-first with the fixed CHECK, restore data,
-- recreate indexes, drop _bak tables.

-- ---- Phase 1: Backup data (no constraints) ----

-- assessments: explicit column list WITHOUT entity_id. entity_id was added
-- out-of-band (not in any migration) so it may or may not exist. The target
-- schema includes entity_id (as NULL); any existing values must be restored
-- from the pre-migration snapshot if needed.
CREATE TABLE assessments_bak (
  id TEXT, org_id TEXT, scheduled_at TEXT, completed_at TEXT,
  duration_minutes INTEGER, transcript_path TEXT, extraction TEXT,
  problems TEXT, disqualifiers TEXT, champion_name TEXT, champion_role TEXT,
  status TEXT, notes TEXT, created_at TEXT
);
INSERT INTO assessments_bak
SELECT id, org_id, scheduled_at, completed_at, duration_minutes,
  transcript_path, extraction, problems, disqualifiers,
  champion_name, champion_role, status, notes, created_at
FROM assessments;
CREATE TABLE quotes_bak AS SELECT * FROM quotes;
CREATE TABLE engagements_bak AS SELECT * FROM engagements;
-- engagement_contacts and parking_lot were dropped out-of-band on remote.
-- Skip backup for these; Phase 4 creates them fresh (empty).
CREATE TABLE milestones_bak AS SELECT * FROM milestones;
CREATE TABLE invoices_bak AS SELECT * FROM invoices;
-- follow_ups: explicit column list. notes was dropped out-of-band on remote,
-- and entity_id/client_id vary by environment. Use common columns only.
CREATE TABLE follow_ups_bak (
  id TEXT, org_id TEXT, engagement_id TEXT, quote_id TEXT, type TEXT,
  scheduled_for TEXT, completed_at TEXT, status TEXT, created_at TEXT
);
INSERT INTO follow_ups_bak
SELECT id, org_id, engagement_id, quote_id, type,
  scheduled_for, completed_at, status, created_at
FROM follow_ups;
CREATE TABLE time_entries_bak AS SELECT * FROM time_entries;

-- ---- Phase 2: Drop indexes ----

DROP INDEX IF EXISTS idx_assessments_org_status;
DROP INDEX IF EXISTS idx_quotes_org_status;
DROP INDEX IF EXISTS idx_quotes_assessment_id;
DROP INDEX IF EXISTS idx_quotes_parent_id;
DROP INDEX IF EXISTS idx_engagements_org_status;
DROP INDEX IF EXISTS idx_engagements_quote_id;
DROP INDEX IF EXISTS idx_engagement_contacts_engagement;
DROP INDEX IF EXISTS idx_engagement_contacts_contact;
DROP INDEX IF EXISTS idx_milestones_engagement_order;
DROP INDEX IF EXISTS idx_milestones_status;
DROP INDEX IF EXISTS idx_parking_lot_engagement;
DROP INDEX IF EXISTS idx_parking_lot_disposition;
DROP INDEX IF EXISTS idx_invoices_org_status;
DROP INDEX IF EXISTS idx_invoices_engagement_id;
DROP INDEX IF EXISTS idx_invoices_stripe_id;
DROP INDEX IF EXISTS idx_invoices_due_date;
DROP INDEX IF EXISTS idx_follow_ups_org_scheduled;
DROP INDEX IF EXISTS idx_follow_ups_engagement_id;
DROP INDEX IF EXISTS idx_follow_ups_quote_id;
DROP INDEX IF EXISTS idx_follow_ups_client_id;
DROP INDEX IF EXISTS idx_time_entries_engagement;
DROP INDEX IF EXISTS idx_time_entries_org_date;

-- ---- Phase 3: Drop tables (leaf-first) ----

DROP TABLE IF EXISTS engagement_contacts;
DROP TABLE milestones;
DROP TABLE time_entries;
DROP TABLE IF EXISTS parking_lot;
DROP TABLE invoices;
DROP TABLE follow_ups;
DROP TABLE engagements;
DROP TABLE quotes;
DROP TABLE assessments;

-- ---- Phase 4: Recreate tables (root-first, with expanded CHECK) ----

CREATE TABLE assessments (
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

CREATE TABLE quotes (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  assessment_id   TEXT NOT NULL REFERENCES assessments(id),
  version         INTEGER NOT NULL DEFAULT 1,
  parent_quote_id TEXT REFERENCES quotes(id),
  line_items      TEXT NOT NULL,
  total_hours     REAL NOT NULL,
  rate            REAL NOT NULL,
  total_price     REAL NOT NULL,
  deposit_pct     REAL DEFAULT 0.5,
  deposit_amount  REAL,
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                    'draft', 'sent', 'accepted', 'declined', 'expired', 'superseded'
                  )),
  sent_at         TEXT,
  expires_at      TEXT,
  accepted_at     TEXT,
  sow_path        TEXT,
  signed_sow_path TEXT,
  signwell_doc_id TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE engagements (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  quote_id        TEXT NOT NULL REFERENCES quotes(id),
  scope_summary   TEXT,
  start_date      TEXT,
  estimated_end   TEXT,
  actual_end      TEXT,
  handoff_date    TEXT,
  safety_net_end  TEXT,
  status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                    'scheduled', 'active', 'handoff', 'safety_net',
                    'completed', 'cancelled'
                  )),
  estimated_hours REAL,
  actual_hours    REAL DEFAULT 0,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE engagement_contacts (
  id              TEXT PRIMARY KEY,
  engagement_id   TEXT NOT NULL REFERENCES engagements(id),
  contact_id      TEXT NOT NULL REFERENCES contacts(id),
  role            TEXT NOT NULL CHECK (role IN (
                    'owner', 'decision_maker', 'champion'
                  )),
  is_primary      INTEGER DEFAULT 0,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(engagement_id, contact_id, role)
);

CREATE TABLE milestones (
  id              TEXT PRIMARY KEY,
  engagement_id   TEXT NOT NULL REFERENCES engagements(id),
  name            TEXT NOT NULL,
  description     TEXT,
  due_date        TEXT,
  completed_at    TEXT,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                    'pending', 'in_progress', 'completed', 'skipped'
                  )),
  payment_trigger INTEGER DEFAULT 0,
  sort_order      INTEGER DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE parking_lot (
  id              TEXT PRIMARY KEY,
  engagement_id   TEXT NOT NULL REFERENCES engagements(id),
  description     TEXT NOT NULL,
  requested_by    TEXT,
  requested_at    TEXT NOT NULL DEFAULT (datetime('now')),
  disposition     TEXT CHECK (disposition IN (
                    'fold_in', 'follow_on', 'dropped'
                  )),
  disposition_note TEXT,
  reviewed_at     TEXT,
  follow_on_quote_id TEXT REFERENCES quotes(id),
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE invoices (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  engagement_id   TEXT REFERENCES engagements(id),
  type            TEXT NOT NULL CHECK (type IN (
                    'deposit', 'completion', 'milestone', 'assessment', 'retainer'
                  )),
  amount          REAL NOT NULL,
  description     TEXT,
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                    'draft', 'sent', 'paid', 'overdue', 'void'
                  )),
  stripe_invoice_id TEXT,
  stripe_hosted_url TEXT,
  due_date        TEXT,
  sent_at         TEXT,
  paid_at         TEXT,
  payment_method  TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE follow_ups (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  engagement_id   TEXT REFERENCES engagements(id),
  quote_id        TEXT REFERENCES quotes(id),
  type            TEXT NOT NULL CHECK (type IN (
                    'initial_outreach', 'outreach_followup_d3', 'outreach_followup_d7',
                    're_engage_30d', 're_engage_90d',
                    'proposal_day2', 'proposal_day5', 'proposal_day7',
                    'review_request', 'referral_ask',
                    'safety_net_checkin', 'feedback_30day',
                    'custom'
                  )),
  scheduled_for   TEXT NOT NULL,
  completed_at    TEXT,
  status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                    'scheduled', 'completed', 'skipped'
                  )),
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE time_entries (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  engagement_id   TEXT NOT NULL REFERENCES engagements(id),
  date            TEXT NOT NULL,
  hours           REAL NOT NULL,
  description     TEXT,
  category        TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---- Phase 5: Restore data (root-first, explicit column lists) ----

INSERT INTO assessments (id, org_id, scheduled_at, completed_at, duration_minutes,
  transcript_path, extraction, problems, disqualifiers, champion_name, champion_role,
  status, notes, created_at)
SELECT id, org_id, scheduled_at, completed_at, duration_minutes,
  transcript_path, extraction, problems, disqualifiers, champion_name, champion_role,
  status, notes, created_at
FROM assessments_bak;

INSERT INTO quotes (id, org_id, assessment_id, version, parent_quote_id,
  line_items, total_hours, rate, total_price, deposit_pct, deposit_amount,
  status, sent_at, expires_at, accepted_at, sow_path, signed_sow_path,
  signwell_doc_id, notes, created_at, updated_at)
SELECT id, org_id, assessment_id, version, parent_quote_id,
  line_items, total_hours, rate, total_price, deposit_pct, deposit_amount,
  status, sent_at, expires_at, accepted_at, sow_path, signed_sow_path,
  signwell_doc_id, notes, created_at, updated_at
FROM quotes_bak;

INSERT INTO engagements (id, org_id, quote_id, scope_summary, start_date,
  estimated_end, actual_end, handoff_date, safety_net_end, status,
  estimated_hours, actual_hours, notes, created_at, updated_at)
SELECT id, org_id, quote_id, scope_summary, start_date,
  estimated_end, actual_end, handoff_date, safety_net_end, status,
  estimated_hours, actual_hours, notes, created_at, updated_at
FROM engagements_bak;

-- engagement_contacts: no backup to restore (table may not have existed)
-- parking_lot: no backup to restore (table may not have existed)

INSERT INTO milestones (id, engagement_id, name, description, due_date,
  completed_at, status, payment_trigger, sort_order, created_at)
SELECT id, engagement_id, name, description, due_date,
  completed_at, status, payment_trigger, sort_order, created_at
FROM milestones_bak;

INSERT INTO invoices (id, org_id, engagement_id, type, amount, description,
  status, stripe_invoice_id, stripe_hosted_url, due_date, sent_at, paid_at,
  payment_method, notes, created_at, updated_at)
SELECT id, org_id, engagement_id, type, amount, description,
  status, stripe_invoice_id, stripe_hosted_url, due_date, sent_at, paid_at,
  payment_method, notes, created_at, updated_at
FROM invoices_bak;

INSERT INTO follow_ups (id, org_id, engagement_id, quote_id, type,
  scheduled_for, completed_at, status, created_at)
SELECT id, org_id, engagement_id, quote_id, type,
  scheduled_for, completed_at, status, created_at
FROM follow_ups_bak;

INSERT INTO time_entries (id, org_id, engagement_id, date, hours,
  description, category, created_at)
SELECT id, org_id, engagement_id, date, hours,
  description, category, created_at
FROM time_entries_bak;

-- ---- Phase 6: Recreate indexes ----

CREATE INDEX idx_assessments_org_status ON assessments(org_id, status);
CREATE INDEX idx_quotes_org_status ON quotes(org_id, status);
CREATE INDEX idx_quotes_assessment_id ON quotes(assessment_id);
CREATE INDEX idx_quotes_parent_id ON quotes(parent_quote_id);
CREATE INDEX idx_engagements_org_status ON engagements(org_id, status);
CREATE INDEX idx_engagements_quote_id ON engagements(quote_id);
CREATE INDEX idx_engagement_contacts_engagement ON engagement_contacts(engagement_id);
CREATE INDEX idx_engagement_contacts_contact ON engagement_contacts(contact_id);
CREATE INDEX idx_milestones_engagement_order ON milestones(engagement_id, sort_order);
CREATE INDEX idx_milestones_status ON milestones(status);
CREATE INDEX idx_parking_lot_engagement ON parking_lot(engagement_id);
CREATE INDEX idx_parking_lot_disposition ON parking_lot(disposition);
CREATE INDEX idx_invoices_org_status ON invoices(org_id, status);
CREATE INDEX idx_invoices_engagement_id ON invoices(engagement_id);
CREATE INDEX idx_invoices_stripe_id ON invoices(stripe_invoice_id);
CREATE INDEX idx_invoices_due_date ON invoices(due_date);
CREATE INDEX idx_follow_ups_org_scheduled ON follow_ups(org_id, status, scheduled_for);
CREATE INDEX idx_follow_ups_engagement_id ON follow_ups(engagement_id);
CREATE INDEX idx_follow_ups_quote_id ON follow_ups(quote_id);
CREATE INDEX idx_time_entries_engagement ON time_entries(engagement_id);
CREATE INDEX idx_time_entries_org_date ON time_entries(org_id, date);

-- ---- Phase 7: Drop backup tables ----

DROP TABLE assessments_bak;
DROP TABLE quotes_bak;
DROP TABLE engagements_bak;
DROP TABLE milestones_bak;
DROP TABLE invoices_bak;
DROP TABLE follow_ups_bak;
DROP TABLE time_entries_bak;


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
