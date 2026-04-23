-- Rename entity stage value 'assessing' → 'meetings' (#466).
--
-- Also drops the DB-level FK from context.entity_id → entities(id). The FK
-- was the sole FK inbound to entities, and it is what blocks the standard
-- SQLite "build shadow, DROP old, RENAME new" pattern for rewriting a
-- CHECK constraint on a referenced table. D1's wrangler sends each SQL
-- statement as a separate D1 request, so pragmas (`foreign_keys=off`,
-- `defer_foreign_keys=on`, `legacy_alter_table=on`) set in one statement
-- DO NOT persist to the next. Four CI deploy attempts and a direct
-- `--file` batch all confirmed this; every variant tripped
-- SQLITE_CONSTRAINT_FOREIGNKEY on the DROP.
--
-- We drop the FK permanently. All code paths that write `context.entity_id`
-- already validate the referenced entity at the service layer (see
-- `src/lib/db/context.ts`'s `appendContext` + the entity-scoped routes).
-- Losing the DB-level constraint removes a dev-time safety net but does
-- not put data at risk, and it unblocks every future entities rewrite.
--
-- Strategy:
--   1. Rewrite `context` with the same schema minus the REFERENCES clause.
--   2. Rewrite `entities` — no FK points at it now, so DROP is free. The
--      INSERT translates 'assessing' → 'meetings' in the SELECT.
--   3. Recreate indexes on both tables.

-- ==========================================================================
-- STEP 1: context — drop the FK to entities
-- ==========================================================================

CREATE TABLE context_new (
  id              TEXT PRIMARY KEY,
  entity_id       TEXT NOT NULL,
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

INSERT INTO context_new SELECT * FROM context;

DROP TABLE context;

ALTER TABLE context_new RENAME TO context;

CREATE INDEX IF NOT EXISTS idx_context_entity ON context(entity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_entity_type ON context(entity_id, type);
CREATE INDEX IF NOT EXISTS idx_context_org_type ON context(org_id, type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_engagement ON context(engagement_id)
  WHERE engagement_id IS NOT NULL;

-- ==========================================================================
-- STEP 2: entities — rewrite with new CHECK, translate 'assessing'→'meetings'
-- ==========================================================================

CREATE TABLE entities_new (
  id                TEXT PRIMARY KEY,
  org_id            TEXT NOT NULL REFERENCES organizations(id),

  name              TEXT NOT NULL,
  slug              TEXT NOT NULL,
  phone             TEXT,
  website           TEXT,

  stage             TEXT NOT NULL DEFAULT 'signal' CHECK (stage IN (
    'signal', 'prospect', 'meetings', 'proposing',
    'engaged', 'delivered', 'ongoing', 'lost'
  )),
  stage_changed_at  TEXT NOT NULL DEFAULT (datetime('now')),

  pain_score        INTEGER CHECK (pain_score BETWEEN 1 AND 10),
  vertical          TEXT,
  area              TEXT,
  employee_count    INTEGER,

  tier              TEXT CHECK (tier IN ('hot', 'warm', 'cool', 'cold')),
  summary           TEXT,

  next_action       TEXT,
  next_action_at    TEXT,

  source_pipeline   TEXT,

  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now')),

  -- Preserved from migration 0017; v1-v4 of this migration dropped it silently.
  revenue_range     TEXT DEFAULT 'unknown',

  UNIQUE(org_id, slug)
);

INSERT INTO entities_new (
  id, org_id, name, slug, phone, website,
  stage, stage_changed_at,
  pain_score, vertical, area, employee_count,
  tier, summary,
  next_action, next_action_at,
  source_pipeline,
  created_at, updated_at,
  revenue_range
)
SELECT
  id, org_id, name, slug, phone, website,
  CASE WHEN stage = 'assessing' THEN 'meetings' ELSE stage END AS stage,
  stage_changed_at,
  pain_score, vertical, area, employee_count,
  tier, summary,
  next_action, next_action_at,
  source_pipeline,
  created_at, updated_at,
  revenue_range
FROM entities;

DROP TABLE entities;

ALTER TABLE entities_new RENAME TO entities;

CREATE INDEX IF NOT EXISTS idx_entities_org_stage ON entities(org_id, stage);
CREATE INDEX IF NOT EXISTS idx_entities_org_slug ON entities(org_id, slug);
CREATE INDEX IF NOT EXISTS idx_entities_org_pain ON entities(org_id, pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_entities_org_next ON entities(org_id, next_action_at);
CREATE INDEX IF NOT EXISTS idx_entities_org_vertical ON entities(org_id, vertical);
CREATE INDEX IF NOT EXISTS idx_entities_org_tier ON entities(org_id, tier);
