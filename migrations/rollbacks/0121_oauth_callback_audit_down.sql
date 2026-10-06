-- Rollback for 0121: drop the OAuth callback audit table.
-- Discards every persisted callback audit row. The web Worker's insert
-- (src/lib/oauth/audit.ts) fails soft (logged and captured, the callback
-- still completes), so this does not break the callback, but roll the Worker
-- back first if the table is meant to stay gone, or Sentry fills with the
-- failed inserts.
--
-- Manual-only; coordinate with Captain.

DROP INDEX IF EXISTS idx_oauth_callback_audit_customer_ts;
DROP TABLE IF EXISTS oauth_callback_audit;
