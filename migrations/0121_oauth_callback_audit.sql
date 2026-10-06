-- 0121: the portal OAuth callback's audit events are persisted (code review
-- 2026-10-06, I12).
--
-- Every invocation of the portal OAuth callback
-- (src/pages/portal/products/operator/oauth/[connector]/callback.ts) emits a
-- `token-issued` or `token-rejected` event. Until now those went to
-- console.log only, behind a TODO pointing at #891, which closed on 2026-05-22
-- with nothing persisted: a connector grant or a rejected consent left no
-- record anyone could read back.
--
-- The row carries the event's metadata and nothing else. There is
-- deliberately NO column that could hold token material: the access token,
-- refresh token, authorization code and state never land here.
--
-- Append-only by convention: no UPDATE/DELETE code path
-- (src/lib/oauth/audit.ts only INSERTs). Forward-only, additive. No drops.

CREATE TABLE IF NOT EXISTS oauth_callback_audit (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  -- Always 'oauth-callback'; kept so the row reads the same as the event.
  skill        TEXT NOT NULL DEFAULT 'oauth-callback',
  action       TEXT NOT NULL CHECK (action IN ('token-issued', 'token-rejected')),
  -- The Operator instance slug bound into the signed state. NULL when the
  -- state could not be verified (a rejection before the customer is known).
  customer_id  TEXT,
  -- Provider slug (e.g. 'google-workspace'). NULL when unknown.
  provider     TEXT,
  -- The signed-in reviewer who started the grant. NULL when unknown.
  reviewer_id  TEXT,
  -- Machine reason for a rejection (e.g. 'state_invalid', 'store_failed:x').
  -- NULL on token-issued.
  reason       TEXT,
  -- When the event happened (ISO-8601, from the Worker).
  ts           TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_oauth_callback_audit_customer_ts
  ON oauth_callback_audit (customer_id, ts DESC);
