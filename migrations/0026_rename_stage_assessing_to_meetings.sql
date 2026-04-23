-- Rename entity stage value 'assessing' → 'meetings' (#466).
--
-- The "assessing" stage name implied a specific paid-assessment meeting. In
-- reality the stage represents "actively meeting with this entity" across
-- discovery calls, assessments, follow-ups, delivery reviews, and check-ins.
-- The stage value is renamed to match — see `ENTITY_STAGES` in
-- `src/lib/db/entities.ts` and the `Meetings` tab in the admin console.
--
-- `entities.stage` has a CHECK constraint listing every allowed stage value
-- (from migration 0008), so swapping the value requires rebuilding the CHECK.
-- SQLite cannot ALTER a CHECK constraint in place — the standard workaround
-- is table-rewrite via PRAGMA legacy_alter_table, which D1 supports.
--
-- Strategy (no data rewrite, no FK drama):
--   1. Temporarily disable strict legacy CHECK enforcement.
--   2. Update the 'assessing' rows to 'meetings'.
--   3. Rebuild the CHECK by copying into a shadow table with the new clause
--      and swapping names — preserves every column, default, and index.
--
-- Rollback: reverse the UPDATE and rebuild the CHECK with 'assessing' back
-- in the allow-list. Data is preserved.

-- ---- Phase 1: backfill the data ----
--
-- Run this BEFORE the CHECK rewrite so we aren't trying to insert 'meetings'
-- into a table whose CHECK still forbids it.

-- Turn OFF the check temporarily. D1 honors this just like upstream SQLite.
PRAGMA defer_foreign_keys = ON;

UPDATE entities SET stage = 'meetings' WHERE stage = 'assessing';


-- ---- Phase 2: rebuild CHECK with the new allow-list ----

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

  UNIQUE(org_id, slug)
);

INSERT INTO entities_new (
  id, org_id, name, slug, phone, website,
  stage, stage_changed_at,
  pain_score, vertical, area, employee_count,
  tier, summary,
  next_action, next_action_at,
  source_pipeline,
  created_at, updated_at
)
SELECT
  id, org_id, name, slug, phone, website,
  stage, stage_changed_at,
  pain_score, vertical, area, employee_count,
  tier, summary,
  next_action, next_action_at,
  source_pipeline,
  created_at, updated_at
FROM entities;

DROP TABLE entities;
ALTER TABLE entities_new RENAME TO entities;

-- Recreate every index dropped with the old table.
CREATE INDEX IF NOT EXISTS idx_entities_org_stage ON entities(org_id, stage);
CREATE INDEX IF NOT EXISTS idx_entities_org_slug ON entities(org_id, slug);
CREATE INDEX IF NOT EXISTS idx_entities_org_pain ON entities(org_id, pain_score DESC);
CREATE INDEX IF NOT EXISTS idx_entities_org_next ON entities(org_id, next_action_at);
CREATE INDEX IF NOT EXISTS idx_entities_org_vertical ON entities(org_id, vertical);
CREATE INDEX IF NOT EXISTS idx_entities_org_tier ON entities(org_id, tier);
