-- ============================================================================
-- Migration 0001: per-customer D1 schema (issue #859)
-- ============================================================================
--
-- Initial schema for the per-customer D1 database (`hermes-{slug}-d1`).
-- One D1 database per customer. No cross-customer tables. Isolation is
-- enforced at the database binding layer (one binding per Machine), not at
-- the row level — see ADR 0009 (cross-Machine query prohibition).
--
-- NOTE (2026-07-03, ADR 0062): the cost tables in this file (cost_telemetry,
-- captain_time_events) had their placement superseded — they now live in the
-- central ss-console D1 (migrations/0083_central_cost_telemetry.sql). This
-- file stays as historical record.
--
-- Source spec: docs/specs/operator/d1-schema.md
-- Applied by:  operator/adapter/run_migrations.py (invoked from
--              bin/provision-customer.sh during customer provisioning)
-- ============================================================================

-- ---------- 1. Audit log ----------
-- Append-only. Substantive content (drafts, sent emails, payloads) lives in
-- R2; D1 stores digests + metadata only. INSERT-only at the application
-- layer (Worker-binding role); DELETE reserved for Captain legal-hold
-- redactions per compliance-evidence-packet.md retention policy.
CREATE TABLE audit_log (
  id            TEXT PRIMARY KEY,           -- ULID, sortable
  ts            TEXT NOT NULL,              -- ISO 8601 UTC
  action_type   TEXT NOT NULL,
  actor         TEXT NOT NULL,              -- 'agent' | 'captain' | person_mappings.id
  actor_role    TEXT,                       -- 'principal' | 'operator' | 'compliance' | 'agent' | 'captain'
  skill_name    TEXT,
  matter_ref    TEXT,
  input_digest  TEXT,                       -- SHA-256
  output_digest TEXT,
  diff_digest   TEXT,
  trust_ceiling TEXT,
  metadata      TEXT                        -- JSON
);
CREATE INDEX idx_audit_ts ON audit_log(ts);
CREATE INDEX idx_audit_action_type ON audit_log(action_type, ts);
CREATE INDEX idx_audit_actor ON audit_log(actor, ts);

-- ---------- 2. Memory rules ----------
-- Customer-defined hard rules. Soft-delete via deleted_at; version bumps
-- on each edit so prior versions remain queryable for audit.
CREATE TABLE memory_rules (
  id            TEXT PRIMARY KEY,           -- ULID
  rule_type     TEXT NOT NULL,              -- 'case_acceptance' | 'voice' | 'process' | 'scope' | 'escalation'
  category      TEXT,
  content       TEXT NOT NULL,
  source        TEXT NOT NULL,              -- 'direct_teach' | 'edit_inferred' | 'captain'
  source_ref    TEXT,                       -- audit_log.id
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  deleted_at    TEXT,
  version       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_memory_rules_active ON memory_rules(rule_type, deleted_at);

-- ---------- 3. Person mappings ----------
-- Canonical identity for partners, paralegals, clients, opposing counsel,
-- co-counsel referral partners, vendors. external_ids maps to PM/connector
-- IDs (Filevine, Clio, etc.). firm_internal=1 for users at the customer's
-- firm; firm_internal=0 for opposing counsel / opposing parties.
CREATE TABLE person_mappings (
  id              TEXT PRIMARY KEY,
  canonical_name  TEXT NOT NULL,
  role            TEXT NOT NULL,            -- 'partner' | 'paralegal' | 'client' | 'opposing_counsel' | 'co_counsel' | 'vendor' | 'receptionist'
  email_addresses TEXT,                     -- JSON array
  external_ids    TEXT,                     -- JSON {"filevine": "...", "clio": "..."}
  firm_internal   INTEGER NOT NULL DEFAULT 1,
  notes           TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  deleted_at      TEXT
);
CREATE INDEX idx_person_active ON person_mappings(deleted_at);
CREATE INDEX idx_person_email ON person_mappings(email_addresses) WHERE deleted_at IS NULL;

-- ---------- 4. Skill state ----------
-- Per-skill activation + trust ceiling. content_hash pins the SKILL.md
-- version at activation; bumped on each promote/demote cycle. config holds
-- per-customer skill parameters from customer.yaml.
CREATE TABLE skill_state (
  skill_name           TEXT PRIMARY KEY,
  trust_ceiling        TEXT NOT NULL,       -- 'autonomous' | 'draft_for_review' | 'refused'
  content_hash         TEXT NOT NULL,
  activated_at         TEXT NOT NULL,
  last_run_at          TEXT,
  run_count            INTEGER NOT NULL DEFAULT 0,
  operator_may_approve INTEGER NOT NULL DEFAULT 0,  -- 0/1; principal-set per dashboard-roles.md
  config               TEXT                 -- JSON; per-skill params from customer.yaml
);

-- ---------- 5. Draft queue ----------
-- Pending review queue for `draft_for_review` skills. Body lives in R2;
-- D1 row carries metadata + R2 key + review state. expires_at supports
-- auto-expiration policy per skill.
CREATE TABLE draft_queue (
  id                  TEXT PRIMARY KEY,
  skill_name          TEXT NOT NULL,
  matter_ref          TEXT,
  created_at          TEXT NOT NULL,
  expires_at          TEXT,
  status              TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected' | 'expired'
  reviewed_at         TEXT,
  reviewed_by         TEXT,                 -- person_mappings.id
  r2_draft_key        TEXT NOT NULL,        -- R2 key per r2-vectorize-naming.md
  r2_sent_key         TEXT,
  priority            INTEGER NOT NULL DEFAULT 5,
  recipient_cohort_id TEXT                  -- FK voice cohort; for cost/voice analysis
);
CREATE INDEX idx_draft_pending ON draft_queue(status, priority, created_at) WHERE status = 'pending';

-- ---------- 6. Cost telemetry ----------
-- Per-day rollup; event-level data lives in cost-telemetry-events.md.
-- driver is the cost-emission source ('anthropic.tokens', 'composio.action',
-- 'fly.machine.runtime', etc.). amount_cents stores in cents to avoid
-- float drift.
CREATE TABLE cost_telemetry (
  date         TEXT NOT NULL,               -- YYYY-MM-DD
  driver       TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  units        REAL,
  unit_type    TEXT,
  PRIMARY KEY (date, driver)
);

-- ---------- 7. Invariant boot-check log ----------
-- Every container start runs the 8 safety invariants (see safety_substrate/
-- run_invariants.py). Pass/fail logged here. invariant_num 1-8 per
-- platform-prd §7.5.
CREATE TABLE invariant_boot_checks (
  id             TEXT PRIMARY KEY,
  ts             TEXT NOT NULL,
  invariant_num  INTEGER NOT NULL,          -- 1-8
  passed         INTEGER NOT NULL,          -- 0/1
  failure_detail TEXT
);
CREATE INDEX idx_boot_checks_ts ON invariant_boot_checks(ts DESC);

-- ---------- 8. Voice samples ----------
-- D1 row indexes samples stored in R2 (per r2-vectorize-naming.md).
-- sanitized=1 means PII/privileged content stripped (per voice-gate fallback
-- spec). used_in_blind_test=1 means held back from training for the
-- ≥80% blind-test gate.
CREATE TABLE voice_samples (
  id                 TEXT PRIMARY KEY,      -- ULID
  uploaded_at        TEXT NOT NULL,
  uploaded_by        TEXT NOT NULL,         -- person_mappings.id
  source             TEXT NOT NULL,         -- 'customer_upload' | 'bootstrap_scrape' | 'sent_folder'
  recipient_cohort_id TEXT,                 -- FK recipient_cohorts
  r2_key             TEXT NOT NULL,
  sanitized          INTEGER NOT NULL DEFAULT 0,
  active             INTEGER NOT NULL DEFAULT 1,
  used_in_blind_test INTEGER NOT NULL DEFAULT 0,
  notes              TEXT
);
CREATE INDEX idx_voice_active ON voice_samples(active, recipient_cohort_id);

-- ---------- 9. Recipient cohorts (Voice Layer 3) ----------
-- Recipient-shape buckets ('anxious-client', 'opposing-counsel',
-- 'routine-vendor', etc.). match_rules JSON: how to route a recipient to
-- this cohort (by email domain, by person_mappings.role, by inferred tone).
CREATE TABLE recipient_cohorts (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  description      TEXT,
  tone_descriptors TEXT,                    -- JSON array
  match_rules      TEXT NOT NULL,           -- JSON
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

-- ---------- 10. Sent-folder watch state ----------
-- Per-skill cursor for sent-folder watching (Voice Layer 2 sample
-- ingestion). enabled defaults to 0 — opt-in per skill. scope_snapshot
-- records the OAuth scope set at the time enablement was authorized
-- (privilege protection).
CREATE TABLE sent_folder_state (
  skill_name      TEXT PRIMARY KEY,
  enabled         INTEGER NOT NULL DEFAULT 0,
  last_cursor     TEXT,                     -- adapter-specific (MS Graph deltaLink, Gmail historyId, etc.)
  last_checked_at TEXT,
  scope_snapshot  TEXT NOT NULL             -- JSON
);

-- ---------- 11. Escalation events ----------
-- Red-flag events that page the configured recipients (per
-- customer.yaml.escalation_recipients). Acknowledgement + resolution tracked
-- for SLA. payload digest matches the R2 object containing the full body.
CREATE TABLE escalation_events (
  id              TEXT PRIMARY KEY,
  ts              TEXT NOT NULL,
  trigger_skill   TEXT NOT NULL,
  trigger_type    TEXT NOT NULL,            -- 'red_flag' | 'failure' | 'invariant_violation'
  recipients      TEXT NOT NULL,            -- JSON array of emails
  payload_digest  TEXT NOT NULL,            -- SHA-256 of payload body (stored in R2)
  r2_payload_key  TEXT,
  acknowledged_at TEXT,
  acknowledged_by TEXT,                     -- person_mappings.id
  resolved_at     TEXT
);
CREATE INDEX idx_escalation_unacked ON escalation_events(acknowledged_at, ts) WHERE acknowledged_at IS NULL;

-- ---------- Schema version ----------
-- run_migrations.py reads PRAGMA user_version on boot; mismatch with the
-- highest-numbered migration file is a hard boot failure per d1-schema.md
-- §Failure modes.
PRAGMA user_version = 1;
