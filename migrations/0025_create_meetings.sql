-- Meetings table — generalizes `assessments` to any meeting type (#469).
--
-- Context: the `assessments` table was narrowly scoped to the paid-assessment
-- call. In practice we run many meeting types — discovery calls, assessments,
-- follow-ups, delivery reviews, check-ins — and the admin UI now treats them
-- all the same. A nullable `meeting_type` column is carried so callers can
-- tag a meeting if useful, but the system does not require one.
--
-- This migration:
--   1. Creates `meetings` (same core columns as `assessments` + nullable
--      `meeting_type` and `completion_notes`).
--   2. Creates `meeting_schedule` (mirror of `assessment_schedule`, FK →
--      meetings.id). Holds slot times, guest identity, Google linkage, and
--      the manage token hash.
--   3. Backfills both new tables from their legacy counterparts, preserving
--      primary keys so foreign keys that reference `assessments.id` (e.g.
--      `quotes.assessment_id`) continue to resolve transitively to the same
--      meeting.
--   4. Adds `quotes.meeting_id` (nullable) and backfills it from
--      `assessment_id` for every existing quote.
--
-- NOT IN THIS MIGRATION (deliberate):
--   * Dropping `assessments` or `assessment_schedule`. Both stay in place for
--     a monitoring window. A follow-up issue tracks the drop.
--   * Changing `quotes.assessment_id` NOT NULL constraint. The column stays
--     authoritative until the drop migration lands.
--
-- Rollback: safe. Drop `meetings`, `meeting_schedule`, and
-- `quotes.meeting_id`. No legacy data is mutated.

-- ============================================================================
-- 1. meetings table
-- ============================================================================

CREATE TABLE meetings (
  id               TEXT PRIMARY KEY,
  org_id           TEXT NOT NULL REFERENCES organizations(id),
  entity_id        TEXT NOT NULL,
  -- Optional meeting type tag. NULL means untyped ("just a meeting").
  meeting_type     TEXT,
  scheduled_at     TEXT,
  completed_at     TEXT,
  duration_minutes INTEGER,
  transcript_path  TEXT,
  extraction       TEXT,
  live_notes       TEXT,
  -- Free-form completion outcome notes, written by the admin when the meeting
  -- is marked complete. Distinct from `live_notes` which captures during-call
  -- notes and from `extraction` which is structured post-processing.
  completion_notes TEXT,
  status           TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                     'scheduled', 'completed', 'disqualified', 'converted', 'cancelled'
                   )),
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_meetings_org_status ON meetings(org_id, status);
CREATE INDEX idx_meetings_entity ON meetings(entity_id);

-- Last-line-of-defense index against double-booking the same slot. Mirrors
-- the equivalent index on `assessments` from migration 0011.
CREATE UNIQUE INDEX uniq_meetings_scheduled_at_active
  ON meetings(org_id, scheduled_at)
  WHERE scheduled_at IS NOT NULL AND status = 'scheduled';


-- ============================================================================
-- 2. meeting_schedule table (sidecar, 1:1 with meetings)
-- ============================================================================
--
-- Schema mirrors assessment_schedule exactly except the FK points at
-- meetings(id).

CREATE TABLE meeting_schedule (
  id                       TEXT PRIMARY KEY,
  meeting_id               TEXT NOT NULL UNIQUE REFERENCES meetings(id) ON DELETE CASCADE,
  org_id                   TEXT NOT NULL REFERENCES organizations(id),

  slot_start_utc           TEXT NOT NULL,
  slot_end_utc             TEXT NOT NULL,
  duration_minutes         INTEGER NOT NULL DEFAULT 30,
  timezone                 TEXT NOT NULL DEFAULT 'America/Phoenix',
  guest_timezone           TEXT,

  guest_name               TEXT NOT NULL,
  guest_email              TEXT NOT NULL,

  google_event_id          TEXT,
  google_event_link        TEXT,
  google_meet_url          TEXT,
  google_sync_state        TEXT NOT NULL DEFAULT 'pending' CHECK (google_sync_state IN (
                             'pending', 'synced', 'error', 'cancelled'
                           )),
  google_last_error        TEXT,

  manage_token_hash        TEXT NOT NULL UNIQUE,
  manage_token_expires_at  TEXT,

  cancelled_at             TEXT,
  cancelled_reason         TEXT,
  cancelled_by             TEXT CHECK (cancelled_by IN ('guest', 'admin', 'system')),
  reschedule_count         INTEGER NOT NULL DEFAULT 0,
  previous_slot_utc        TEXT,

  reminder_sent_at         TEXT,

  created_at               TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_meeting_schedule_slot
  ON meeting_schedule(org_id, slot_start_utc);
CREATE INDEX idx_meeting_schedule_manage_hash
  ON meeting_schedule(manage_token_hash);
CREATE INDEX idx_meeting_schedule_google_event
  ON meeting_schedule(google_event_id) WHERE google_event_id IS NOT NULL;


-- ============================================================================
-- 3. Backfill meetings from assessments (preserving IDs)
-- ============================================================================
--
-- One-time copy. IDs are preserved so FKs elsewhere (e.g. quotes.assessment_id)
-- continue to match the meeting row that replaced the assessment.

INSERT INTO meetings (
  id, org_id, entity_id, meeting_type,
  scheduled_at, completed_at, duration_minutes,
  transcript_path, extraction, live_notes, completion_notes,
  status, created_at
)
SELECT
  id, org_id, entity_id, 'assessment',
  scheduled_at, completed_at, duration_minutes,
  transcript_path, extraction, live_notes, notes,
  status, created_at
FROM assessments
WHERE entity_id IS NOT NULL;


-- ============================================================================
-- 4. Backfill meeting_schedule from assessment_schedule (preserving IDs)
-- ============================================================================
--
-- Manage-token hashes are UNIQUE on the new table but the legacy table is
-- untouched — the new row carries the same hash so guest-facing manage URLs
-- resolve against either table during the monitoring window. After the
-- legacy drop, only meeting_schedule remains.

INSERT INTO meeting_schedule (
  id, meeting_id, org_id,
  slot_start_utc, slot_end_utc, duration_minutes, timezone, guest_timezone,
  guest_name, guest_email,
  google_event_id, google_event_link, google_meet_url,
  google_sync_state, google_last_error,
  manage_token_hash, manage_token_expires_at,
  cancelled_at, cancelled_reason, cancelled_by,
  reschedule_count, previous_slot_utc,
  reminder_sent_at,
  created_at, updated_at
)
SELECT
  s.id, s.assessment_id, s.org_id,
  s.slot_start_utc, s.slot_end_utc, s.duration_minutes, s.timezone, s.guest_timezone,
  s.guest_name, s.guest_email,
  s.google_event_id, s.google_event_link, s.google_meet_url,
  s.google_sync_state, s.google_last_error,
  s.manage_token_hash, s.manage_token_expires_at,
  s.cancelled_at, s.cancelled_reason, s.cancelled_by,
  s.reschedule_count, s.previous_slot_utc,
  s.reminder_sent_at,
  s.created_at, s.updated_at
FROM assessment_schedule s
-- Only copy rows whose meeting was successfully backfilled above. This
-- guards against FK failures if an assessment_schedule row points at a
-- pre-migration-0010 assessment with no entity_id.
WHERE EXISTS (SELECT 1 FROM meetings m WHERE m.id = s.assessment_id);


-- ============================================================================
-- 5. quotes.meeting_id — nullable mirror of assessment_id
-- ============================================================================
--
-- Because meeting IDs equal assessment IDs by construction, new quotes created
-- via the post-migration code path can set meeting_id authoritatively while
-- still honoring the NOT NULL assessment_id column for legacy compatibility.
-- Once the drop migration removes assessments, a follow-up drops
-- quotes.assessment_id as well.

ALTER TABLE quotes ADD COLUMN meeting_id TEXT;
UPDATE quotes SET meeting_id = assessment_id WHERE meeting_id IS NULL;
CREATE INDEX idx_quotes_meeting_id ON quotes(meeting_id);
