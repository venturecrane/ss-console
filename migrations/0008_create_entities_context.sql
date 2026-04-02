-- Entity-context architecture — Phase A (additive only, no drops).
--
-- Two new tables form the intelligence layer:
--   entities: one row per business, accumulates context over its full lifecycle
--   context:  append-only log of everything we learn about an entity
--
-- This migration is safe to run alongside existing tables. Existing data
-- is backfilled by scripts/migrate-to-entities.ts after this migration runs.

-- ============================================================================
-- entities
-- ============================================================================

CREATE TABLE IF NOT EXISTS entities (
  id                TEXT PRIMARY KEY,
  org_id            TEXT NOT NULL REFERENCES organizations(id),

  -- Identity + dedup
  name              TEXT NOT NULL,
  slug              TEXT NOT NULL,
  phone             TEXT,
  website           TEXT,

  -- Lifecycle state machine
  stage             TEXT NOT NULL DEFAULT 'signal' CHECK (stage IN (
    'signal', 'prospect', 'assessing', 'proposing',
    'engaged', 'delivered', 'ongoing', 'lost'
  )),
  stage_changed_at  TEXT NOT NULL DEFAULT (datetime('now')),

  -- Cached DETERMINISTIC attributes (recomputed synchronously on context insert)
  pain_score        INTEGER CHECK (pain_score BETWEEN 1 AND 10),
  vertical          TEXT,
  area              TEXT,
  employee_count    INTEGER,

  -- Cached LLM-DERIVED attributes (computed async or on-demand)
  tier              TEXT CHECK (tier IN ('hot', 'warm', 'cool', 'cold')),
  summary           TEXT,

  -- Next action tracking
  next_action       TEXT,
  next_action_at    TEXT,

  -- Provenance
  source_pipeline   TEXT,

  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now')),

  UNIQUE(org_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_entities_org_stage ON entities(org_id, stage);
CREATE INDEX IF NOT EXISTS idx_entities_org_slug ON entities(org_id, slug);
CREATE INDEX IF NOT EXISTS idx_entities_org_pain ON entities(org_id, pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_entities_org_next ON entities(org_id, next_action_at);
CREATE INDEX IF NOT EXISTS idx_entities_org_vertical ON entities(org_id, vertical);
CREATE INDEX IF NOT EXISTS idx_entities_org_tier ON entities(org_id, tier);

-- ============================================================================
-- context
-- ============================================================================

CREATE TABLE IF NOT EXISTS context (
  id              TEXT PRIMARY KEY,
  entity_id       TEXT NOT NULL REFERENCES entities(id),
  org_id          TEXT NOT NULL REFERENCES organizations(id),

  -- Classification
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
    'intake'
  )),

  -- The actual intelligence
  content         TEXT NOT NULL,
  source          TEXT NOT NULL,
  source_ref      TEXT,
  content_size    INTEGER,

  -- Structured metadata (for linking, not querying)
  metadata        TEXT,

  -- Optional engagement linkage
  engagement_id   TEXT,

  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_context_entity ON context(entity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_entity_type ON context(entity_id, type);
CREATE INDEX IF NOT EXISTS idx_context_org_type ON context(org_id, type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_engagement ON context(engagement_id)
  WHERE engagement_id IS NOT NULL;
