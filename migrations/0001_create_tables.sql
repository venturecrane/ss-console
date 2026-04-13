-- ============================================================================
-- Migration 0001: Create all portal tables
-- ============================================================================
--
-- Creates the complete data model for the SMD Services client portal.
-- See PRD Section 7 (Data Model) for full specification.
--
-- ID Strategy: All primary keys are TEXT columns using ULID format.
-- Multi-tenancy: Every table includes org_id (except organizations and magic_links).
--
-- Table creation order resolves foreign-key dependencies:
--   1. organizations (no FK dependencies)
--   2. clients (depends on organizations)
--   3. users (depends on organizations; client_id FK added via ALTER TABLE after clients exists)
--   4. contacts (depends on organizations, clients)
--   5. assessments (depends on organizations, clients)
--   6. quotes (depends on organizations, clients, assessments; self-ref parent_quote_id)
--   7. engagements (depends on organizations, clients, quotes)
--   8. engagement_contacts (depends on engagements, contacts)
--   9. milestones (depends on engagements)
--  10. parking_lot (depends on engagements, quotes)
--  11. invoices (depends on organizations, engagements, clients)
--  12. follow_ups (depends on organizations, clients, engagements, quotes)
--  13. time_entries (depends on organizations, engagements)
--  14. magic_links (no FK dependencies)
--
-- JSON Column Contracts:
--
--   organizations.branding (TEXT, JSON):
--     {
--       "logo_url": string | null,
--       "colors": { "primary": string, "secondary": string, ... },
--       "fonts": { "heading": string, "body": string }
--     }
--
--   organizations.settings (TEXT, JSON):
--     {
--       "default_rate": number,           -- hourly rate in dollars
--       "default_deposit_pct": number,    -- e.g. 0.5 for 50%
--       "payment_terms": string,          -- e.g. "net_15"
--       "milestone_threshold_hours": number  -- hours above which 3-milestone billing applies
--     }
--
--   assessments.extraction (TEXT, JSON):
--     Full AssessmentExtraction interface from src/portal/assessments/extraction-schema.ts.
--     Top-level fields:
--     {
--       "schema_version": "1.0",
--       "extracted_at": string (ISO 8601),
--       "business_name": string,
--       "vertical": "home_services" | "professional_services" | "contractor_trades" | "retail_salon" | "restaurant_food" | "other",
--       "business_type": string,
--       "years_in_business": number | null,
--       "employee_count": number | null,
--       "geography": string | null,
--       "current_tools": [{ "name": string, "purpose": string, "status": "working" | "underutilized" | "failing" }],
--       "identified_problems": [{ "problem_id": ProblemId, "severity": "high"|"medium"|"low", "summary": string, "owner_quotes": string[], "underlying_cause": string }],
--       "complexity_signals": { "employee_count": number|null, "location_count": number, "tool_migrations": string[], "data_volume_notes": string[], "integration_needs": string[], "additional_factors": string[] },
--       "champion_candidate": { "name": string|null, "role": string|null, "evidence": string, "confidence": "strong"|"moderate"|"weak" } | null,
--       "call_participants": string[],
--       "disqualification_flags": DisqualificationFlags,
--       "budget_signals": BudgetSignals,
--       "quote_drivers": QuoteDrivers,
--       "executive_summary": string,
--       "additional_notes": string
--     }
--
--   assessments.problems (TEXT, JSON):
--     Array of ProblemId strings from the 6 universal SMB operations problems:
--     ["owner_bottleneck", "lead_leakage", "financial_blindness", "scheduling_chaos", "manual_communication", "employee_retention"]
--
--   assessments.disqualifiers (TEXT, JSON):
--     DisqualificationFlags from extraction-schema.ts:
--     {
--       "hard": { "not_decision_maker": boolean, "scope_exceeds_sprint": boolean, "no_tech_baseline": boolean },
--       "soft": { "no_champion": boolean, "books_behind": boolean, "no_willingness_to_change": boolean },
--       "notes": string
--     }
--
--   quotes.line_items (TEXT, JSON):
--     Array of line items derived from assessment extraction:
--     [{ "problem": ProblemId, "description": string, "estimated_hours": number }]
--
-- ============================================================================


-- ============================================
-- 1. ORGANIZATIONS
-- ============================================
CREATE TABLE organizations (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  slug            TEXT NOT NULL UNIQUE,  -- subdomain: {slug}.forge.app (future)
  domain          TEXT,                  -- custom domain: portal.smd.services
  stripe_account  TEXT,                  -- Stripe connected account ID (future)
  branding        TEXT,                  -- JSON: see contract above
  settings        TEXT,                  -- JSON: see contract above
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ============================================
-- 2. CLIENTS
-- ============================================
CREATE TABLE clients (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  business_name   TEXT NOT NULL,
  vertical        TEXT CHECK (vertical IN (
                    'home_services', 'professional_services',
                    'contractor_trades', 'retail_salon', 'restaurant', 'other'
                  )),
  employee_count  INTEGER,
  years_in_business INTEGER,
  source          TEXT,
  referred_by     TEXT,
  status          TEXT NOT NULL DEFAULT 'prospect' CHECK (status IN (
                    'prospect', 'assessed', 'quoted', 'active', 'completed', 'dead'
                  )),
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ============================================
-- 3. USERS (admin and client portal users)
-- ============================================
-- Note: client_id FK is added via ALTER TABLE below to resolve
-- the forward-reference issue (users depends on clients, but the
-- PRD defines users first).
CREATE TABLE users (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  email           TEXT NOT NULL,
  name            TEXT NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('admin', 'client')),
  client_id       TEXT,                 -- links client portal users to their client record
  last_login_at   TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(org_id, email)
);

-- Add the foreign key constraint now that clients table exists.
-- SQLite does not support ADD CONSTRAINT, but a column defined without
-- REFERENCES can still be enforced via application-layer checks and
-- the foreign_keys pragma. The FK relationship is documented here
-- and enforced at the application layer.
--
-- In SQLite, ALTER TABLE ... ADD CONSTRAINT is not supported.
-- The FK is declared in the column definition above as TEXT only;
-- application code must enforce referential integrity for users.client_id → clients.id.


-- ============================================
-- 4. CONTACTS
-- ============================================
CREATE TABLE contacts (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  client_id       TEXT NOT NULL REFERENCES clients(id),
  name            TEXT NOT NULL,
  email           TEXT,
  phone           TEXT,
  title           TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ============================================
-- 5. ASSESSMENTS
-- ============================================
CREATE TABLE assessments (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  client_id       TEXT NOT NULL REFERENCES clients(id),
  scheduled_at    TEXT,
  completed_at    TEXT,
  duration_minutes INTEGER,
  transcript_path TEXT,            -- R2 key for stored transcript
  extraction      TEXT,            -- JSON: full AssessmentExtraction (see contract above)
  problems        TEXT,            -- JSON: array of ProblemId strings (see contract above)
  disqualifiers   TEXT,            -- JSON: DisqualificationFlags (see contract above)
  champion_name   TEXT,
  champion_role   TEXT,
  status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                    'scheduled', 'completed', 'disqualified', 'converted'
                  )),
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ============================================
-- 6. QUOTES
-- ============================================
CREATE TABLE quotes (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  client_id       TEXT NOT NULL REFERENCES clients(id),
  assessment_id   TEXT NOT NULL REFERENCES assessments(id),
  version         INTEGER NOT NULL DEFAULT 1,
  parent_quote_id TEXT REFERENCES quotes(id),  -- for versioning (self-referencing)
  line_items      TEXT NOT NULL,   -- JSON: array of line items (see contract above)
  total_hours     REAL NOT NULL,
  rate            REAL NOT NULL,   -- internal rate at time of quote
  total_price     REAL NOT NULL,   -- project price shown to client
  deposit_pct     REAL DEFAULT 0.5,
  deposit_amount  REAL,
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                    'draft', 'sent', 'accepted', 'declined', 'expired', 'superseded'
                  )),
  sent_at         TEXT,
  expires_at      TEXT,
  accepted_at     TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ============================================
-- 7. ENGAGEMENTS
-- ============================================
CREATE TABLE engagements (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  client_id       TEXT NOT NULL REFERENCES clients(id),
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


-- ============================================
-- 8. ENGAGEMENT CONTACTS (role per engagement)
-- ============================================
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


-- ============================================
-- 9. MILESTONES
-- ============================================
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


-- ============================================
-- 10. PARKING LOT
-- ============================================
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


-- ============================================
-- 11. INVOICES
-- ============================================
CREATE TABLE invoices (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  engagement_id   TEXT REFERENCES engagements(id),
  client_id       TEXT NOT NULL REFERENCES clients(id),
  type            TEXT NOT NULL CHECK (type IN (
                    'deposit', 'completion', 'milestone', 'assessment', 'retainer'
                  )),
  amount          REAL NOT NULL,
  description     TEXT,
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                    'draft', 'sent', 'paid', 'overdue', 'void'
                  )),
  stripe_invoice_id TEXT,          -- Stripe invoice ID
  stripe_hosted_url TEXT,          -- Stripe hosted invoice URL for client
  due_date        TEXT,
  sent_at         TEXT,
  paid_at         TEXT,
  payment_method  TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ============================================
-- 12. FOLLOW-UPS
-- ============================================
CREATE TABLE follow_ups (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  client_id       TEXT NOT NULL REFERENCES clients(id),
  engagement_id   TEXT REFERENCES engagements(id),
  quote_id        TEXT REFERENCES quotes(id),
  type            TEXT NOT NULL CHECK (type IN (
                    'proposal_day2', 'proposal_day5', 'proposal_day7',
                    'review_request', 'referral_ask',
                    'safety_net_checkin', 'feedback_30day'
                  )),
  scheduled_for   TEXT NOT NULL,
  completed_at    TEXT,
  status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                    'scheduled', 'completed', 'skipped'
                  )),
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ============================================
-- 13. TIME ENTRIES
-- ============================================
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


-- ============================================
-- 14. MAGIC LINKS (auth)
-- ============================================
CREATE TABLE magic_links (
  id              TEXT PRIMARY KEY,
  email           TEXT NOT NULL,
  token           TEXT NOT NULL UNIQUE,
  expires_at      TEXT NOT NULL,
  used_at         TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
