-- Retainers — post-delivery recurring service agreements.
--
-- Created when an engagement completes and the client opts into ongoing
-- support. Linked to entity and optionally to the originating engagement.
-- The follow-up processor's billing handler reads next_billing_date to
-- generate monthly invoices.
--
-- Part of Entity Lifecycle System epic #234 (Phase 2b — retainer).

CREATE TABLE retainers (
  id                  TEXT PRIMARY KEY,
  org_id              TEXT NOT NULL REFERENCES organizations(id),
  entity_id           TEXT NOT NULL REFERENCES entities(id),
  engagement_id       TEXT REFERENCES engagements(id),
  monthly_rate        REAL NOT NULL,
  included_hours      REAL,
  scope_description   TEXT,
  terms               TEXT,
  cancellation_policy TEXT,
  start_date          TEXT NOT NULL,
  end_date            TEXT,
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN (
                        'active', 'paused', 'cancelled'
                      )),
  last_billed_at      TEXT,
  next_billing_date   TEXT NOT NULL,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_retainers_entity ON retainers(entity_id);
CREATE INDEX idx_retainers_status ON retainers(status);
CREATE INDEX idx_retainers_next_billing ON retainers(next_billing_date);
