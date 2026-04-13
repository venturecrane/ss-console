-- Migration 0015: Create retainers table
-- Retainers represent post-delivery recurring service agreements.

CREATE TABLE IF NOT EXISTS retainers (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  entity_id TEXT NOT NULL REFERENCES entities(id),
  engagement_id TEXT REFERENCES engagements(id),
  monthly_rate REAL NOT NULL,
  included_hours REAL,
  scope_description TEXT,
  terms TEXT,
  cancellation_policy TEXT,
  start_date TEXT NOT NULL,
  end_date TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'cancelled')),
  last_billed_at TEXT,
  next_billing_date TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_retainers_org_id ON retainers(org_id);
CREATE INDEX IF NOT EXISTS idx_retainers_entity_id ON retainers(entity_id);
CREATE INDEX IF NOT EXISTS idx_retainers_status ON retainers(org_id, status);
CREATE INDEX IF NOT EXISTS idx_retainers_next_billing ON retainers(org_id, status, next_billing_date);
