-- Migration 0027: Harden magic_links, context, and milestones
--
-- Fixes three integrity gaps left by earlier incremental migrations:
--
--   1. magic_links stored only email, so auth resolution was ambiguous once
--      the same email existed in multiple orgs. Rebuild with org_id + user_id
--      and intentionally invalidate old links. Magic links expire after 15
--      minutes, so preserving legacy rows is not worth carrying ambiguous auth.
--
--   2. context lost its FK to entities in 0026. Rebuild it with the FK
--      restored. If orphan context rows exist, the INSERT should fail loudly
--      rather than silently preserve corrupted data.
--
--   3. milestones.org_id was bolted on via ALTER TABLE with a sentinel
--      default and no FK/index. Rebuild the table with a real org FK and
--      tenant-aware indexes.

-- ============================================================================
-- 1. magic_links - bind tokens to a specific user
-- ============================================================================

CREATE TABLE magic_links_new (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  user_id         TEXT NOT NULL REFERENCES users(id),
  email           TEXT NOT NULL,
  token           TEXT NOT NULL UNIQUE,
  expires_at      TEXT NOT NULL,
  used_at         TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

DROP TABLE magic_links;
ALTER TABLE magic_links_new RENAME TO magic_links;

CREATE INDEX idx_magic_links_org_email ON magic_links(org_id, email);
CREATE INDEX idx_magic_links_expires ON magic_links(expires_at);
CREATE INDEX idx_magic_links_user_expires ON magic_links(user_id, expires_at);

-- ============================================================================
-- 2. context - restore entity FK
-- ============================================================================

CREATE TABLE context_new (
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

INSERT INTO context_new SELECT * FROM context;

DROP TABLE context;
ALTER TABLE context_new RENAME TO context;

CREATE INDEX idx_context_entity ON context(entity_id, created_at);
CREATE INDEX idx_context_entity_type ON context(entity_id, type);
CREATE INDEX idx_context_org_type ON context(org_id, type, created_at DESC);
CREATE INDEX idx_context_engagement ON context(engagement_id)
  WHERE engagement_id IS NOT NULL;

-- ============================================================================
-- 3. milestones - rebuild with real org FK + indexes
-- ============================================================================

CREATE TABLE milestones_new (
  id              TEXT PRIMARY KEY,
  engagement_id   TEXT NOT NULL REFERENCES engagements(id),
  org_id          TEXT NOT NULL REFERENCES organizations(id),
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

INSERT INTO milestones_new (
  id, engagement_id, org_id, name, description, due_date,
  completed_at, status, payment_trigger, sort_order, created_at
)
SELECT
  m.id,
  m.engagement_id,
  COALESCE(NULLIF(m.org_id, ''), e.org_id),
  m.name,
  m.description,
  m.due_date,
  m.completed_at,
  m.status,
  m.payment_trigger,
  m.sort_order,
  m.created_at
FROM milestones m
JOIN engagements e ON e.id = m.engagement_id;

DROP TABLE milestones;
ALTER TABLE milestones_new RENAME TO milestones;

CREATE INDEX idx_milestones_org_engagement_order ON milestones(org_id, engagement_id, sort_order);
CREATE INDEX idx_milestones_org_status ON milestones(org_id, status);
