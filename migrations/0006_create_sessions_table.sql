-- ============================================================================
-- Migration 0006: Create sessions table for D1-backed session storage
-- ============================================================================
--
-- Sessions table is the source of truth for admin authentication.
-- Workers KV serves as a fast-lookup cache, but D1 is authoritative.
--
-- Session tokens are cryptographically random UUIDs.
-- Sessions expire after 7 days of inactivity (sliding window).
-- ============================================================================

CREATE TABLE sessions (
  id          TEXT PRIMARY KEY,
  token       TEXT NOT NULL UNIQUE,
  user_id     TEXT NOT NULL REFERENCES users(id),
  org_id      TEXT NOT NULL REFERENCES organizations(id),
  role        TEXT NOT NULL,
  email       TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_sessions_token ON sessions(token);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
