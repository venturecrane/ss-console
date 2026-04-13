-- Add entity_id to users and expand outbox_jobs type constraint.
--
-- Migration 0010 dropped client_id from contacts, assessments, quotes,
-- engagements, and invoices but skipped the users table. This migration
-- completes that work: drops the legacy client_id column and adds
-- entity_id, which the portal session resolver (getPortalClient) expects.
--
-- Also expands the outbox_jobs type CHECK to allow 'send_portal_invitation',
-- used to send a welcome email when a client portal user is provisioned
-- during SOW signing.

-- ---------------------------------------------------------------
-- 1. users: drop legacy client_id, add entity_id
-- ---------------------------------------------------------------
DROP INDEX IF EXISTS idx_users_client_id;
ALTER TABLE users DROP COLUMN client_id;

ALTER TABLE users ADD COLUMN entity_id TEXT;
CREATE INDEX idx_users_entity ON users(org_id, entity_id);

-- ---------------------------------------------------------------
-- 2. outbox_jobs: expand type CHECK constraint
--
-- SQLite cannot ALTER CHECK constraints. Rebuild the table with the
-- updated constraint. Only 2 rows at time of writing.
-- ---------------------------------------------------------------
CREATE TABLE outbox_jobs_new (
  id                  TEXT PRIMARY KEY,
  org_id              TEXT NOT NULL REFERENCES organizations(id),
  signature_request_id TEXT NOT NULL REFERENCES signature_requests(id),
  type                TEXT NOT NULL CHECK (type IN (
                        'send_sow_signed_email', 'send_deposit_invoice',
                        'send_portal_invitation'
                      )),
  status              TEXT NOT NULL CHECK (status IN (
                        'pending', 'processing', 'completed', 'failed'
                      )),
  dedupe_key          TEXT NOT NULL,
  payload_json        TEXT NOT NULL,
  attempt_count       INTEGER NOT NULL DEFAULT 0,
  available_at        TEXT NOT NULL,
  last_error          TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO outbox_jobs_new SELECT * FROM outbox_jobs;
DROP TABLE outbox_jobs;
ALTER TABLE outbox_jobs_new RENAME TO outbox_jobs;

CREATE UNIQUE INDEX idx_outbox_jobs_dedupe
  ON outbox_jobs(dedupe_key);

CREATE INDEX idx_outbox_jobs_request_status
  ON outbox_jobs(signature_request_id, status, available_at);
