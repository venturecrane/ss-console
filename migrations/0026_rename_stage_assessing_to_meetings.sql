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
-- SQLite cannot ALTER a CHECK constraint in place and there is no PRAGMA to
-- disable CHECK on a live table, so we do the standard table-rewrite dance:
--
--   1. Build a shadow table with the NEW CHECK allow-list.
--   2. Copy every row, translating 'assessing' → 'meetings' in the SELECT.
--   3. Drop the old table and rename the shadow.
--   4. Recreate indexes (they are dropped along with the old table).
--
-- Order matters: the translate-in-SELECT must happen when inserting into the
-- shadow table, because the ORIGINAL table's CHECK still forbids 'meetings'.
-- An earlier version of this migration attempted `UPDATE ... SET stage =
-- 'meetings'` against the old table first; that fails with the pre-rewrite
-- CHECK constraint. See CI run 24861522944 for the postmortem.
--
-- Rollback: same shape — build a shadow with the legacy 'assessing' value,
-- translate 'meetings' → 'assessing' in the SELECT, swap tables, recreate
-- indexes. Data is preserved.
--
-- Foreign-key handling:
-- Other tables (contacts, meetings, assessments, engagements, quotes, invoices,
-- context entries, stage_changes, ...) hold foreign keys to entities(id).
-- DROP TABLE entities would trip SQLITE_CONSTRAINT_FOREIGNKEY mid-transaction
-- even though every referenced id is preserved by the shadow-copy.
--
-- D1 supports `PRAGMA foreign_keys=off` inside a migration transaction — see
-- Cloudflare's D1 docs "Disable Foreign Keys and Drop Tables" example which
-- uses exactly this pattern for table drops. Stock upstream SQLite forbids
-- toggling foreign_keys inside a transaction, but D1 allows it for the
-- duration of a single migration-apply run. We previously tried
-- `PRAGMA defer_foreign_keys = ON` here; it returned a D1 engine-side
-- internal error on remote (deploy run 24862074143), while foreign_keys=off
-- is the docs-sanctioned path for this exact scenario. See that run's
-- postmortem. FK enforcement restores automatically when the transaction
-- commits — no need to toggle back on.

PRAGMA foreign_keys=off;

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
  CASE WHEN stage = 'assessing' THEN 'meetings' ELSE stage END AS stage,
  stage_changed_at,
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
