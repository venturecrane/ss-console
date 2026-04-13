-- SOW revision lifecycle and signing records.
--
-- Wave 1 introduces immutable SOW revisions, explicit send authorization,
-- provider request tracking, and an outbox for post-acceptance side effects.

CREATE TABLE IF NOT EXISTS sow_revisions (
  id                   TEXT PRIMARY KEY,
  org_id               TEXT NOT NULL REFERENCES organizations(id),
  quote_id             TEXT NOT NULL REFERENCES quotes(id),
  quote_version        INTEGER NOT NULL,
  sow_number           TEXT NOT NULL,
  status               TEXT NOT NULL CHECK (status IN (
                         'rendered', 'sent', 'superseded', 'signed'
                       )),
  unsigned_storage_key TEXT NOT NULL,
  signed_storage_key   TEXT,
  checksum_sha256      TEXT NOT NULL,
  rendered_by          TEXT NOT NULL,
  rendered_at          TEXT NOT NULL,
  signed_at            TEXT,
  superseded_at        TEXT,
  metadata_json        TEXT,
  created_at           TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sow_revisions_org_number
  ON sow_revisions(org_id, sow_number);

CREATE INDEX IF NOT EXISTS idx_sow_revisions_quote_created
  ON sow_revisions(quote_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sow_revisions_quote_status
  ON sow_revisions(quote_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS sow_send_authorizations (
  id                  TEXT PRIMARY KEY,
  org_id              TEXT NOT NULL REFERENCES organizations(id),
  quote_id            TEXT NOT NULL REFERENCES quotes(id),
  sow_revision_id     TEXT NOT NULL REFERENCES sow_revisions(id),
  signer_contact_id   TEXT NOT NULL REFERENCES contacts(id),
  signer_snapshot_json TEXT NOT NULL,
  checksum_sha256     TEXT NOT NULL,
  authorized_by       TEXT NOT NULL,
  authorized_at       TEXT NOT NULL,
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sow_send_authorizations_revision
  ON sow_send_authorizations(sow_revision_id, authorized_at DESC);

CREATE INDEX IF NOT EXISTS idx_sow_send_authorizations_quote
  ON sow_send_authorizations(quote_id, authorized_at DESC);

CREATE TABLE IF NOT EXISTS signature_requests (
  id                   TEXT PRIMARY KEY,
  org_id               TEXT NOT NULL REFERENCES organizations(id),
  quote_id             TEXT NOT NULL REFERENCES quotes(id),
  sow_revision_id      TEXT NOT NULL REFERENCES sow_revisions(id),
  send_authorization_id TEXT NOT NULL REFERENCES sow_send_authorizations(id),
  provider             TEXT NOT NULL CHECK (provider IN ('signwell')),
  provider_request_id  TEXT,
  status               TEXT NOT NULL CHECK (status IN (
                         'send_failed', 'sent', 'completed_pending_artifact',
                         'completed', 'declined', 'expired'
                       )),
  signer_snapshot_json TEXT NOT NULL,
  provider_payload_json TEXT NOT NULL,
  signed_storage_key   TEXT,
  sent_at              TEXT,
  completed_at         TEXT,
  declined_at          TEXT,
  expired_at           TEXT,
  webhook_last_at      TEXT,
  failure_reason       TEXT,
  created_at           TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signature_requests_provider_request
  ON signature_requests(provider, provider_request_id);

CREATE INDEX IF NOT EXISTS idx_signature_requests_quote_status
  ON signature_requests(quote_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_signature_requests_revision
  ON signature_requests(sow_revision_id, created_at DESC);

CREATE TABLE IF NOT EXISTS outbox_jobs (
  id                  TEXT PRIMARY KEY,
  org_id              TEXT NOT NULL REFERENCES organizations(id),
  signature_request_id TEXT NOT NULL REFERENCES signature_requests(id),
  type                TEXT NOT NULL CHECK (type IN (
                        'send_sow_signed_email', 'send_deposit_invoice'
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_jobs_dedupe
  ON outbox_jobs(dedupe_key);

CREATE INDEX IF NOT EXISTS idx_outbox_jobs_request_status
  ON outbox_jobs(signature_request_id, status, available_at);
