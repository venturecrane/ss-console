-- Add 'alert' to the context table's type CHECK constraint and seed a
-- system entity for non-entity-specific alerts (booking system errors, etc.).
--
-- D1/SQLite does not support ALTER TABLE ... ALTER COLUMN for CHECK constraints.
-- Uses the rename-copy-rename pattern to recreate the table with the updated
-- constraint, matching the pattern established in migration 0009.

-- ============================================================================
-- Step 0: Seed system entity (used as entity_id for system-level context rows)
-- ============================================================================

INSERT OR IGNORE INTO entities (
  id, org_id, name, slug, stage, stage_changed_at, source_pipeline, created_at, updated_at
) VALUES (
  '00000000-0000-0000-0000-000000000000',
  '01JQFK0000SMDSERVICES000',
  '_system',
  '_system',
  'signal',
  datetime('now'),
  'system',
  datetime('now'),
  datetime('now')
);

-- ============================================================================
-- Step 1: Rename existing table
-- ============================================================================

ALTER TABLE context RENAME TO context_old;

-- ============================================================================
-- Step 2: Create new table with 'alert' added to CHECK constraint
-- ============================================================================

CREATE TABLE context (
  id              TEXT PRIMARY KEY,
  entity_id       TEXT NOT NULL REFERENCES entities(id),
  org_id          TEXT NOT NULL REFERENCES organizations(id),

  type            TEXT NOT NULL CHECK (type IN (
    'signal',
    'enrichment',
    'note',
    'transcript',
    'extraction',
    'outreach_draft',
    'engagement_log',
    'follow_up_result',
    'feedback',
    'parking_lot',
    'stage_change',
    'intake',
    'scorecard',
    'alert'
  )),

  content         TEXT NOT NULL,
  source          TEXT NOT NULL,
  source_ref      TEXT,
  content_size    INTEGER,
  metadata        TEXT,
  engagement_id   TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Step 3: Copy all existing data
-- ============================================================================

INSERT INTO context SELECT * FROM context_old;

-- ============================================================================
-- Step 4: Drop old table
-- ============================================================================

DROP TABLE context_old;

-- ============================================================================
-- Step 5: Recreate indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_context_entity ON context(entity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_entity_type ON context(entity_id, type);
CREATE INDEX IF NOT EXISTS idx_context_org_type ON context(org_id, type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_engagement ON context(engagement_id)
  WHERE engagement_id IS NOT NULL;

-- New index: enables the 30-minute window rate-limit query in booking alerts
CREATE INDEX IF NOT EXISTS idx_context_alert_kind ON context(type, created_at DESC)
  WHERE type = 'alert';
