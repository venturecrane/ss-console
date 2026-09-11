-- Rollback for 0108_audit_head_history.sql (ss#2500).
--
-- READ THIS BEFORE RUNNING IT. This table is the only copy of the audit chain
-- heads that root on a seat cannot reach. Dropping it discards every pin ever
-- recorded, and no later heartbeat backfills them: a heartbeat can only report
-- the head as it is now. After this runs, the daily verifier has nothing to
-- compare against and every seat reports a HOLD until a fresh pin lands, which
-- is the honest state but not a recoverable one.
--
-- Export first if there is any chance the history is wanted:
--   npx wrangler d1 execute ss-console-db --remote --json \
--     --command "SELECT * FROM audit_head_history ORDER BY id"

DROP INDEX IF EXISTS idx_audit_head_history_head;
DROP INDEX IF EXISTS idx_audit_head_history_seat;
DROP TABLE IF EXISTS audit_head_history;
